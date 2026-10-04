"""Execute a macro, including its control flow.

The player runs on a worker thread. It checks the stop event between steps and
inside every wait, so stopping feels immediate even during a long delay.

Input goes through `input_backend`, which uses `SendInput`. That injects into
the system input queue, so the keystrokes and clicks land in whichever window
currently has focus - MyMacro itself stays in the background.

Control flow is carried by exceptions. `break`/`continue` are caught by the
nearest enclosing loop; `restart`/`goto` unwind all the way to the top-level
pass, which then resumes from the requested position.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Callable, Iterable

from . import input_backend as ib
from . import models, vision
from .models import Macro, Step

# Dragging the pointer into the very top-left corner aborts the run. It is the
# last-resort escape hatch when a macro misbehaves and the keyboard is busy.
FAILSAFE_CORNER_PX = 3

LogFn = Callable[[str], None]


class MacroAborted(Exception):
    """Raised when the stop event, the failsafe, or a stop step fires."""


class BreakLoop(Exception):
    """Leave the nearest enclosing loop."""


class ContinueLoop(Exception):
    """Skip to the next iteration of the nearest enclosing loop."""


class RestartMacro(Exception):
    """Go back to the first step of the macro."""


class GotoLabel(Exception):
    """Continue from a named label at the top level."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class Player:
    def __init__(
        self,
        macro: Macro,
        stop_event: threading.Event,
        log: LogFn | None = None,
        failsafe: bool = True,
    ):
        self.macro = macro
        self.stop_event = stop_event
        self.log: LogFn = log or (lambda _msg: None)
        self.failsafe = failsafe
        # Counts real work done. Used to detect a jump loop that never makes
        # progress, which would otherwise spin forever at full speed.
        self._progress = 0
        self._progress_at_last_jump = -1
        # Where each image step last found its template, keyed by step. Lets a
        # repeated search start next to the previous hit instead of sweeping
        # the whole screen. Purely an optimisation: a miss falls back to the
        # full search, so results do not depend on it.
        self._match_cache: dict[int, tuple[int, int]] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    # --- public ---------------------------------------------------------
    def run(self) -> None:
        loop = 0
        self._match_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        try:
            while True:
                self._check_stop()
                loop += 1
                total = "무한" if self.macro.repeat == 0 else str(self.macro.repeat)
                self.log(f"--- 반복 {loop}/{total} 시작 ---")
                self._run_pass()

                if self.macro.repeat != 0 and loop >= self.macro.repeat:
                    break
                if self.macro.repeat_delay_ms > 0:
                    self._sleep_ms(self.macro.repeat_delay_ms)
            self.log("매크로 완료.")
            self._log_cache_summary()
        except MacroAborted as exc:
            self.log(str(exc) or "매크로 중지됨.")
        except ib.UnknownKeyError as exc:
            self.log(f"키 이름 오류로 중단: {exc}")
        except Exception as exc:
            self.log(f"오류로 중단: {exc}")
            raise

    def _run_pass(self) -> None:
        """One pass over the macro, honouring restart and goto."""
        start = 0
        while True:
            try:
                self._run_steps(self.macro.steps[start:])
                return
            except RestartMacro:
                self._guard_progress("매크로 처음으로")
                self.log("  -> 매크로 처음으로 돌아갑니다")
                start = 0
            except GotoLabel as jump:
                index = self._find_label(jump.name)
                if index is None:
                    self.log(f"  (라벨 '{jump.name}' 을(를) 찾을 수 없어 매크로를 끝냅니다)")
                    return
                self._guard_progress(f"라벨 '{jump.name}'")
                self.log(f"  -> 라벨 '{jump.name}' 로 이동")
                start = index
            except BreakLoop:
                self.log("  (루프 밖에서 '루프 탈출'을 만나 매크로를 끝냅니다)")
                return
            except ContinueLoop:
                self.log("  (루프 밖에서 '다음 반복'을 만나 매크로를 끝냅니다)")
                return

    def _find_label(self, name: str) -> int | None:
        for index, step in enumerate(self.macro.steps):
            if step.type == models.LABEL and str(step.params.get("name", "")) == name:
                return index
        return None

    def _guard_progress(self, where: str) -> None:
        """Stop a jump loop that does nothing between jumps."""
        if self._progress == self._progress_at_last_jump:
            raise MacroAborted(
                f"무한 루프를 막기 위해 중단했습니다: {where} 로 돌아가는 사이에 "
                "실행되는 단계가 없습니다."
            )
        self._progress_at_last_jump = self._progress

    # --- step dispatch ---------------------------------------------------
    def _run_steps(self, steps: Iterable[Step]) -> None:
        for step in steps:
            self._check_stop()
            if not step.enabled:
                continue
            if step.type == models.LABEL:
                continue  # a marker, nothing to execute
            self.log(f"  {step.describe()}")
            if step.type == models.MOUSE:
                self._do_mouse(step.params)
            elif step.type == models.KEY:
                self._do_key(step.params)
            elif step.type == models.DELAY:
                self._do_delay(step.params)
            elif step.type == models.IF_IMAGE:
                self._do_if_image(step)
            elif step.type == models.LOOP:
                self._do_loop(step)
            elif step.type == models.JUMP:
                self._do_jump(step.params)
            else:
                self.log(f"  (알 수 없는 단계 무시: {step.type})")

    def _do_mouse(self, p: dict) -> None:
        self._progress += 1
        action = str(p.get("action", "click"))
        x, y = p.get("x"), p.get("y")
        duration = float(p.get("duration_ms", 0) or 0)
        button = str(p.get("button", "left"))

        if action == "move":
            ib.move_to(x, y, duration)
        elif action == "click":
            ib.click(x, y, "left", 1, duration)
        elif action == "double_click":
            ib.click(x, y, "left", 2, duration)
        elif action == "right_click":
            ib.click(x, y, "right", 1, duration)
        elif action == "middle_click":
            ib.click(x, y, "middle", 1, duration)
        elif action == "mouse_down":
            ib.mouse_down(x, y, button)
        elif action == "mouse_up":
            ib.mouse_up(x, y, button)
        elif action == "drag":
            ib.drag(x, y, p.get("to_x"), p.get("to_y"), button, duration)
        elif action == "scroll":
            ib.scroll(int(p.get("scroll_amount", 0)), x, y)
        else:
            self.log(f"  (알 수 없는 마우스 동작: {action})")

    def _do_key(self, p: dict) -> None:
        self._progress += 1
        action = str(p.get("action", "press"))
        if action == "press":
            ib.press(str(p.get("key", "")))
        elif action == "key_down":
            ib.key_down(str(p.get("key", "")))
        elif action == "key_up":
            ib.key_up(str(p.get("key", "")))
        elif action == "hotkey":
            keys = [str(k) for k in (p.get("keys") or []) if str(k)]
            if keys:
                ib.hotkey(keys)
        elif action == "type":
            self._type_text(str(p.get("text", "")), float(p.get("interval_ms", 0) or 0))
        else:
            self.log(f"  (알 수 없는 키 동작: {action})")

    def _type_text(self, text: str, interval_ms: float) -> None:
        try:
            ib.type_text(text, interval_ms)
        except ValueError:
            # A character outside the basic multilingual plane (an emoji, for
            # example) cannot be sent as one UTF-16 unit.
            self.log("    (SendInput으로 보낼 수 없는 문자가 있어 클립보드로 입력합니다)")
            ib.type_text_via_clipboard(text)

    def _do_delay(self, p: dict) -> None:
        self._progress += 1
        ms = int(p.get("ms", 0))
        jitter = int(p.get("jitter_ms", 0) or 0)
        if jitter:
            ms = max(0, ms + random.randint(-jitter, jitter))
        self._sleep_ms(ms)

    def _locate(self, step: Step, timeout_ms: int = 0):
        """Find this step's template, starting from the remembered position.

        The cache only changes how long the search takes. When the image is
        not where it was, the full search runs and the cache is corrected.
        """
        p = step.params
        key = id(step)
        use_cache = bool(p.get("use_cache", True))
        hot_spot = self._match_cache.get(key) if use_cache else None

        self._progress += 1
        match = vision.wait_for(
            str(p.get("image", "")),
            confidence=float(p.get("confidence", 0.85)),
            region=p.get("region"),
            grayscale=bool(p.get("grayscale", True)),
            timeout_ms=timeout_ms,
            should_stop=self.stop_event.is_set,
            hot_spot=hot_spot,
        )

        if match is None:
            # Gone for now; stop paying for a hot search that keeps missing.
            self._match_cache.pop(key, None)
            if hot_spot is not None:
                self._cache_misses += 1
        else:
            if hot_spot is not None:
                if match.from_hot_spot:
                    self._cache_hits += 1
                else:
                    self._cache_misses += 1
            if use_cache:
                self._match_cache[key] = (match.left, match.top)
        return match

    def _log_cache_summary(self) -> None:
        total = self._cache_hits + self._cache_misses
        if total:
            self.log(
                f"위치 캐시: 적중 {self._cache_hits} / 빗나감 {self._cache_misses}"
            )

    def _do_if_image(self, step: Step) -> None:
        p = step.params
        image = str(p.get("image", ""))
        if not image:
            self.log("  (이미지 경로가 비어 있어 건너뜀)")
            return

        match = self._locate(step, timeout_ms=int(p.get("timeout_ms", 0)))
        self._check_stop()

        if match is not None:
            how = "캐시" if match.from_hot_spot else "전체 탐색"
            self.log(f"    찾음 ({match.x}, {match.y}) 점수 {match.score:.3f} [{how}]")
            if p.get("click_on_match"):
                dx, dy = (p.get("match_offset") or [0, 0])[:2]
                ib.click(match.x + int(dx), match.y + int(dy))
            key_on_match = str(p.get("key_on_match", "") or "")
            if key_on_match:
                self.log(f"    {key_on_match} 입력")
                ib.press_combo(key_on_match)
            self._run_steps(step.then_steps)
        else:
            self.log("    못 찾음")
            self._run_steps(step.else_steps)

    def _do_loop(self, step: Step) -> None:
        p = step.params
        mode = str(p.get("mode", "count"))
        count = int(p.get("count", 1) or 0)
        max_iterations = int(p.get("max_iterations", 1000) or 1000)

        iteration = 0
        while True:
            self._check_stop()

            if mode == "count":
                if iteration >= count:
                    break
            else:
                if iteration >= max_iterations:
                    self.log(f"    (최대 반복 {max_iterations}회에 도달해 루프를 끝냅니다)")
                    break
                if mode in ("while_image", "until_image"):
                    if not self._loop_image_allows(step, mode):
                        break

            iteration += 1
            self.log(f"    [{iteration}회차]")
            try:
                self._run_steps(step.children)
            except BreakLoop:
                self.log("    루프 탈출")
                break
            except ContinueLoop:
                continue

    def _loop_image_allows(self, step: Step, mode: str) -> bool:
        """Decide whether the next iteration should run."""
        if not str(step.params.get("image", "")):
            self.log("    (이미지가 지정되지 않아 루프를 끝냅니다)")
            return False

        found = self._locate(step) is not None

        if mode == "while_image":
            if not found:
                self.log("    이미지가 사라져 루프를 끝냅니다")
            return found
        if found:
            self.log("    이미지가 나타나 루프를 끝냅니다")
        return not found

    def _do_jump(self, p: dict) -> None:
        action = str(p.get("action", "break"))
        if action == "break":
            raise BreakLoop()
        if action == "continue":
            raise ContinueLoop()
        if action == "restart":
            raise RestartMacro()
        if action == "goto":
            raise GotoLabel(str(p.get("target", "")))
        if action == "stop":
            raise MacroAborted("매크로 종료 단계를 만나 끝냅니다.")
        self.log(f"  (알 수 없는 흐름 제어: {action})")

    # --- helpers ---------------------------------------------------------
    def _sleep_ms(self, ms: int) -> None:
        """Sleep in short slices so a stop request lands quickly."""
        remaining = ms / 1000.0
        while remaining > 0:
            self._check_stop()
            slice_s = min(0.05, remaining)
            time.sleep(slice_s)
            remaining -= slice_s
        self._check_stop()

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise MacroAborted("매크로 중지됨.")
        if self.failsafe:
            x, y = ib.position()
            if x <= FAILSAFE_CORNER_PX and y <= FAILSAFE_CORNER_PX:
                raise MacroAborted("비상 정지: 마우스가 화면 왼쪽 위 모서리에 닿았습니다.")
