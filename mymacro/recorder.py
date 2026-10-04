"""Record real mouse and keyboard input into macro steps.

pynput listeners run on their own threads. `Recorder` collects steps there and
hands the finished list back when `stop()` is called.

Timing is captured as explicit delay steps between actions, so the recording
stays editable: you can shorten a 4-second pause to 200ms afterwards.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard, mouse

from . import keymap
from .models import Step, delay_step, key_step, mouse_step

# Movement under this many pixels between press and release counts as a click.
DRAG_THRESHOLD_PX = 5
# Gaps shorter than this are noise, not intent.
MIN_DELAY_MS = 30

_BUTTON_ACTIONS = {
    mouse.Button.left: "click",
    mouse.Button.right: "right_click",
    mouse.Button.middle: "middle_click",
}


class Recorder:
    def __init__(
        self,
        record_moves: bool = False,
        capture_delays: bool = True,
        ignore_keys: tuple[str, ...] = (),
        on_step: Callable[[Step], None] | None = None,
    ):
        self.record_moves = record_moves
        self.capture_delays = capture_delays
        self.ignore_keys = set(ignore_keys)
        self.on_step = on_step

        self._steps: list[Step] = []
        self._lock = threading.Lock()
        self._last_event_at: float | None = None
        self._press_pos: tuple[int, int] | None = None
        self._held_modifiers: list[str] = []
        self._mouse_listener: mouse.Listener | None = None
        self._key_listener: keyboard.Listener | None = None
        self._running = False

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._steps = []
        self._last_event_at = time.monotonic()
        self._held_modifiers = []
        self._running = True

        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
            on_move=self._on_move if self.record_moves else None,
        )
        self._key_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._mouse_listener.start()
        self._key_listener.start()

    def stop(self) -> list[Step]:
        self._running = False
        for listener in (self._mouse_listener, self._key_listener):
            if listener is not None:
                listener.stop()
        self._mouse_listener = None
        self._key_listener = None
        with self._lock:
            return list(self._steps)

    @property
    def running(self) -> bool:
        return self._running

    # --- internals --------------------------------------------------------
    def _append(self, step: Step) -> None:
        with self._lock:
            self._steps.append(step)
        if self.on_step is not None:
            self.on_step(step)

    def _flush_delay(self) -> None:
        now = time.monotonic()
        if self.capture_delays and self._last_event_at is not None:
            gap_ms = int((now - self._last_event_at) * 1000)
            if gap_ms >= MIN_DELAY_MS:
                self._append(delay_step(gap_ms))
        self._last_event_at = now

    def _on_move(self, x: int, y: int) -> None:
        if not self._running:
            return
        self._flush_delay()
        self._append(mouse_step("move", x=int(x), y=int(y)))

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._running:
            return
        if pressed:
            self._press_pos = (int(x), int(y))
            return

        start = self._press_pos or (int(x), int(y))
        self._press_pos = None
        moved = abs(int(x) - start[0]) + abs(int(y) - start[1])

        self._flush_delay()
        if moved > DRAG_THRESHOLD_PX:
            self._append(
                mouse_step(
                    "drag",
                    x=start[0], y=start[1],
                    to_x=int(x), to_y=int(y),
                    button=button.name,
                    duration_ms=200,
                )
            )
        else:
            action = _BUTTON_ACTIONS.get(button, "click")
            self._append(mouse_step(action, x=start[0], y=start[1]))

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._running:
            return
        self._flush_delay()
        self._append(mouse_step("scroll", x=int(x), y=int(y), scroll_amount=int(dy)))

    def _on_press(self, key) -> None:
        if not self._running:
            return
        name = keymap.from_pynput(key)
        if name is None or name in self.ignore_keys:
            return

        if name in keymap.MODIFIER_KEYS:
            base = keymap.normalize_modifier(name)
            if base not in self._held_modifiers:
                self._held_modifiers.append(base)
            return

        self._flush_delay()
        if self._held_modifiers:
            self._append(key_step("hotkey", keys=[*self._held_modifiers, name]))
        else:
            self._append(key_step("press", key=name))

    def _on_release(self, key) -> None:
        if not self._running:
            return
        name = keymap.from_pynput(key)
        if name is None:
            return
        if name in keymap.MODIFIER_KEYS:
            base = keymap.normalize_modifier(name)
            if base in self._held_modifiers:
                self._held_modifiers.remove(base)
