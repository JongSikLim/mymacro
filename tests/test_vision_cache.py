"""Hot-spot search: it must speed things up without changing the answer."""

import cv2
import numpy as np
import pytest

from mymacro import vision

SCREEN_W, SCREEN_H = 1200, 800
TPL_X, TPL_Y, TPL_W, TPL_H = 500, 300, 80, 40


@pytest.fixture
def fake_screen(monkeypatch, tmp_path):
    """A synthetic desktop plus a template cut out of it.

    `grab` is served from the array, and every requested rectangle is recorded
    so a test can tell a small hot-window search from a full sweep.
    """
    rng = np.random.default_rng(1234)
    screen = rng.integers(0, 255, (SCREEN_H, SCREEN_W, 3), dtype=np.uint8)
    template = screen[TPL_Y:TPL_Y + TPL_H, TPL_X:TPL_X + TPL_W].copy()

    path = tmp_path / "tpl.png"
    cv2.imwrite(str(path), template)

    boxes: list[tuple[int, int, int, int]] = []

    def fake_grab(region=None):
        if region is None:
            region = (0, 0, SCREEN_W, SCREEN_H)
        left, top, width, height = (int(v) for v in region)
        boxes.append((left, top, width, height))
        return screen[top:top + height, left:left + width].copy()

    monkeypatch.setattr(vision, "grab", fake_grab)
    monkeypatch.setattr(
        vision, "virtual_screen",
        lambda: {"left": 0, "top": 0, "width": SCREEN_W, "height": SCREEN_H},
    )
    return {"path": str(path), "boxes": boxes}


def test_full_search_finds_the_template(fake_screen):
    match = vision.find(fake_screen["path"], confidence=0.9)

    assert match is not None
    assert (match.left, match.top) == (TPL_X, TPL_Y)
    assert (match.x, match.y) == (TPL_X + TPL_W // 2, TPL_Y + TPL_H // 2)
    assert match.from_hot_spot is False
    assert fake_screen["boxes"] == [(0, 0, SCREEN_W, SCREEN_H)]


def test_hot_spot_search_only_reads_a_small_window(fake_screen):
    match = vision.find(fake_screen["path"], confidence=0.9, hot_spot=(TPL_X, TPL_Y))

    assert match is not None
    assert match.from_hot_spot is True
    assert (match.left, match.top) == (TPL_X, TPL_Y)

    assert len(fake_screen["boxes"]) == 1, "the full screen should not be read"
    _, _, width, height = fake_screen["boxes"][0]
    assert width == TPL_W + 2 * vision.HOT_MARGIN_PX
    assert height == TPL_H + 2 * vision.HOT_MARGIN_PX


def test_slightly_moved_target_is_still_caught_by_the_hot_window(fake_screen):
    """A window that shifted a few pixels stays inside the margin."""
    match = vision.find(
        fake_screen["path"], confidence=0.9, hot_spot=(TPL_X - 12, TPL_Y + 9)
    )

    assert match is not None and match.from_hot_spot is True
    assert (match.left, match.top) == (TPL_X, TPL_Y)
    assert len(fake_screen["boxes"]) == 1


def test_stale_hot_spot_falls_back_to_the_full_search(fake_screen):
    """A wrong cache must cost time, never correctness."""
    match = vision.find(fake_screen["path"], confidence=0.9, hot_spot=(50, 50))

    assert match is not None
    assert (match.left, match.top) == (TPL_X, TPL_Y), "the real position still wins"
    assert match.from_hot_spot is False
    assert len(fake_screen["boxes"]) == 2, "hot window first, then the whole screen"
    assert fake_screen["boxes"][1] == (0, 0, SCREEN_W, SCREEN_H)


def test_hot_spot_is_clipped_to_the_search_region(fake_screen):
    """A cached point at the edge must not produce a rectangle off-screen."""
    vision.find(fake_screen["path"], confidence=0.9, hot_spot=(2, 2))

    left, top, width, height = fake_screen["boxes"][0]
    assert left >= 0 and top >= 0
    assert left + width <= SCREEN_W and top + height <= SCREEN_H


def test_hot_window_is_skipped_when_it_is_not_smaller(fake_screen):
    """With a huge margin the window stops being a shortcut, so skip it."""
    vision.find(
        fake_screen["path"], confidence=0.9, hot_spot=(TPL_X, TPL_Y), hot_margin=600
    )

    assert fake_screen["boxes"] == [(0, 0, SCREEN_W, SCREEN_H)]


# --- the geometry helper on its own --------------------------------------

BOUNDS = (0, 0, 1000, 1000)


def test_hot_box_is_centred_on_the_remembered_point():
    box = vision._hot_box((400, 400), (50, 20), BOUNDS, margin=10)
    assert box == [390, 390, 70, 40]


def test_hot_box_clamps_to_the_bounds():
    box = vision._hot_box((0, 0), (50, 20), BOUNDS, margin=10)
    assert box == [0, 0, 60, 30]


def test_hot_box_refuses_a_window_that_is_not_worth_it():
    assert vision._hot_box((400, 400), (50, 20), BOUNDS, margin=400) is None


def test_hot_box_refuses_a_window_smaller_than_the_template():
    tiny_bounds = (0, 0, 30, 30)
    assert vision._hot_box((0, 0), (50, 20), tiny_bounds, margin=5) is None
