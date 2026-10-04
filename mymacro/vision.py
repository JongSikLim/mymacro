"""Screen capture and template matching.

`mss` grabs the screen far faster than PIL's ImageGrab, and OpenCV's
`matchTemplate` does the actual search. Every coordinate returned here is an
absolute virtual-desktop coordinate, which is what SetCursorPos expects.

Two things keep repeated searches cheap:

* The MSS instance is created once per thread and reused. Building one costs
  about 2ms, which is pure waste when a loop checks the screen every turn.
* A caller that already knows where the image was last seen passes `hot_spot`.
  The search then starts with a small window around that point, which is where
  the image almost always still is. A miss falls back to the full search, so
  the result is the same either way - only the cost changes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import mss
import numpy as np

Region = Sequence[int] | None  # [left, top, width, height]

# How far around the remembered position to look first. Big enough to absorb a
# window that shifted slightly, small enough to stay far cheaper than a full
# screen search.
HOT_MARGIN_PX = 30

# Searching a hot window only pays off while it stays much smaller than the
# area it replaces.
HOT_MAX_AREA_RATIO = 0.25

_local = threading.local()


@dataclass
class Match:
    x: int           # centre of the match, absolute screen coordinate
    y: int
    score: float
    left: int
    top: int
    width: int
    height: int
    from_hot_spot: bool = False


# --- capture ------------------------------------------------------------

def _screenshotter():
    """One MSS instance per thread, reused across calls.

    MSS is not thread safe, and the player runs on a worker thread while the
    editor captures from the UI thread.
    """
    sct = getattr(_local, "sct", None)
    if sct is None:
        factory = getattr(mss, "MSS", None) or mss.mss
        sct = factory()
        _local.sct = sct
    return sct


def reset_screenshotter() -> None:
    """Drop the cached instance, e.g. after the display layout changed."""
    sct = getattr(_local, "sct", None)
    if sct is not None:
        try:
            sct.close()
        except Exception:
            pass
    _local.sct = None


def virtual_screen() -> dict[str, int]:
    """Bounding box of every monitor combined."""
    monitor = _screenshotter().monitors[0]
    return {
        "left": monitor["left"],
        "top": monitor["top"],
        "width": monitor["width"],
        "height": monitor["height"],
    }


def grab(region: Region = None) -> np.ndarray:
    """Capture the screen (or a region) as a BGR numpy array."""
    box = _box(region)
    try:
        raw = _screenshotter().grab(box)
    except Exception:
        # A monitor was added, removed or resized: the cached instance holds
        # stale geometry. Rebuild it once and try again.
        reset_screenshotter()
        raw = _screenshotter().grab(box)
    # mss returns BGRA; drop the alpha channel.
    return np.asarray(raw)[:, :, :3]


def _box(region: Region) -> dict[str, int]:
    if region is None:
        return dict(_screenshotter().monitors[0])
    left, top, width, height = (int(v) for v in region)
    return {"left": left, "top": top, "width": width, "height": height}


def save_region_png(region: Sequence[int], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), grab(region))
    return path


def _load_template(path: str | Path) -> np.ndarray:
    # imread chokes on non-ASCII paths on Windows, so decode the bytes manually.
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        raise FileNotFoundError(f"템플릿 이미지를 읽을 수 없습니다: {path}")
    template = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"이미지 형식을 해석할 수 없습니다: {path}")
    return template


# --- matching -----------------------------------------------------------

def _search(
    template: np.ndarray,
    box: Sequence[int],
    confidence: float,
    grayscale: bool,
) -> Match | None:
    """Look for `template` inside one absolute screen rectangle."""
    screen = grab(box)
    if screen.shape[0] < template.shape[0] or screen.shape[1] < template.shape[1]:
        return None

    if grayscale:
        screen_img = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        template_img = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    else:
        screen_img, template_img = screen, template

    result = cv2.matchTemplate(screen_img, template_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence:
        return None

    height, width = template.shape[:2]
    left = max_loc[0] + int(box[0])
    top = max_loc[1] + int(box[1])
    return Match(
        x=left + width // 2,
        y=top + height // 2,
        score=float(max_val),
        left=left,
        top=top,
        width=width,
        height=height,
    )


def _hot_box(
    hot_spot: Sequence[int],
    template_size: tuple[int, int],
    bounds: Sequence[int],
    margin: int,
) -> list[int] | None:
    """A small rectangle around the last known position, clipped to `bounds`.

    Returns None when the window cannot be built, or when it would be large
    enough that searching it is no cheaper than searching everything.
    """
    template_width, template_height = template_size
    bound_left, bound_top, bound_width, bound_height = (int(v) for v in bounds)

    left = max(bound_left, int(hot_spot[0]) - margin)
    top = max(bound_top, int(hot_spot[1]) - margin)
    right = min(bound_left + bound_width, int(hot_spot[0]) + template_width + margin)
    bottom = min(bound_top + bound_height, int(hot_spot[1]) + template_height + margin)

    width, height = right - left, bottom - top
    if width < template_width or height < template_height:
        return None
    if width * height >= bound_width * bound_height * HOT_MAX_AREA_RATIO:
        return None
    return [left, top, width, height]


def find(
    template_path: str | Path,
    confidence: float = 0.85,
    region: Region = None,
    grayscale: bool = True,
    hot_spot: Sequence[int] | None = None,
    hot_margin: int = HOT_MARGIN_PX,
) -> Match | None:
    """Find `template_path` on screen once.

    `hot_spot` is the (left, top) of a previous match. When given, a small
    window around it is searched first; anything found there is returned with
    `from_hot_spot` set. A miss falls back to the full search, so the answer
    never depends on the cache - only the time taken does.
    """
    template = _load_template(template_path)
    template_size = (template.shape[1], template.shape[0])

    if region is None:
        screen = virtual_screen()
        bounds = [screen["left"], screen["top"], screen["width"], screen["height"]]
    else:
        bounds = [int(v) for v in region]

    if hot_spot is not None:
        box = _hot_box(hot_spot, template_size, bounds, hot_margin)
        if box is not None:
            match = _search(template, box, confidence, grayscale)
            if match is not None:
                match.from_hot_spot = True
                return match

    return _search(template, bounds, confidence, grayscale)


def wait_for(
    template_path: str | Path,
    confidence: float = 0.85,
    region: Region = None,
    grayscale: bool = True,
    timeout_ms: int = 0,
    interval_ms: int = 200,
    should_stop: Callable[[], bool] | None = None,
    hot_spot: Sequence[int] | None = None,
    hot_margin: int = HOT_MARGIN_PX,
) -> Match | None:
    """Keep looking until the template appears or `timeout_ms` elapses.

    `timeout_ms=0` means a single check with no waiting.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if should_stop is not None and should_stop():
            return None
        match = find(template_path, confidence, region, grayscale, hot_spot, hot_margin)
        if match is not None:
            return match
        if timeout_ms <= 0 or time.monotonic() >= deadline:
            return None
        time.sleep(max(0.01, interval_ms / 1000.0))
