"""Drive the player with a fake input backend.

This catches the bug class that would otherwise only appear on Windows: a
parameter the recorder or the editor writes but the player never reads, or
reads under a different name.
"""

import threading
import types

import pytest
from pynput import mouse as pmouse

from mymacro import player as player_mod
from mymacro.vision import Match
from mymacro.models import Macro, delay_step, if_image_step, key_step, mouse_step
from mymacro.recorder import Recorder


class FakeBackend:
    """Stands in for `input_backend`, recording what was asked for."""

    UnknownKeyError = ValueError

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def position(self):
        return (500, 500)

    def move_to(self, x, y, duration_ms=0):
        self.calls.append(("move_to", x, y))

    def click(self, x=None, y=None, button="left", count=1, duration_ms=0):
        self.calls.append(("click", x, y, button, count))

    def mouse_down(self, x=None, y=None, button="left"):
        self.calls.append(("mouse_down", x, y, button))

    def mouse_up(self, x=None, y=None, button="left"):
        self.calls.append(("mouse_up", x, y, button))

    def drag(self, x, y, to_x, to_y, button="left", duration_ms=200):
        self.calls.append(("drag", x, y, to_x, to_y, button))

    def scroll(self, amount, x=None, y=None):
        self.calls.append(("scroll", amount, x, y))

    def press(self, name):
        self.calls.append(("press", name))

    def key_down(self, name):
        self.calls.append(("key_down", name))

    def key_up(self, name):
        self.calls.append(("key_up", name))

    def hotkey(self, names):
        self.calls.append(("hotkey", list(names)))

    def type_text(self, text, interval_ms=0):
        self.calls.append(("type_text", text))

    def type_text_via_clipboard(self, text):
        self.calls.append(("clipboard", text))

    @property
    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def found(x: int = 777, y: int = 888, width: int = 20, height: int = 10) -> Match:
    """A real Match, so the fakes cannot drift from what the player reads."""
    return Match(
        x=x, y=y, score=0.99,
        left=x - width // 2, top=y - height // 2,
        width=width, height=height,
    )


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def set_image_result(monkeypatch, match) -> None:
    monkeypatch.setattr(
        player_mod, "vision", types.SimpleNamespace(wait_for=lambda *a, **k: match)
    )


def play(macro: Macro, stop: threading.Event | None = None) -> None:
    player_mod.Player(macro, stop or threading.Event(), lambda _m: None, failsafe=False).run()


def every_step_macro() -> Macro:
    macro = Macro(repeat=1)
    macro.steps = [
        mouse_step("move", x=1, y=2, duration_ms=0),
        mouse_step("click", x=10, y=20),
        mouse_step("double_click", x=11, y=21),
        mouse_step("right_click", x=12, y=22),
        mouse_step("middle_click", x=13, y=23),
        mouse_step("mouse_down", x=14, y=24, button="left"),
        mouse_step("mouse_up", x=15, y=25, button="left"),
        mouse_step("drag", x=16, y=26, to_x=30, to_y=40, button="left", duration_ms=200),
        mouse_step("scroll", x=17, y=27, scroll_amount=-3),
        key_step("press", key="enter"),
        key_step("key_down", key="shift"),
        key_step("key_up", key="shift"),
        key_step("hotkey", keys=["ctrl", "shift", "s"]),
        key_step("type", text="hello 한글", interval_ms=0),
        delay_step(1),
    ]
    return macro


def test_every_step_shape_executes(backend, monkeypatch):
    set_image_result(monkeypatch, found())
    play(every_step_macro())

    assert backend.names == [
        "move_to", "click", "click", "click", "click", "mouse_down", "mouse_up",
        "drag", "scroll", "press", "key_down", "key_up", "hotkey", "type_text",
    ]
    assert ("click", 10, 20, "left", 1) in backend.calls
    assert ("click", 11, 21, "left", 2) in backend.calls  # double click
    assert ("click", 12, 22, "right", 1) in backend.calls
    assert ("drag", 16, 26, 30, 40, "left") in backend.calls
    assert ("scroll", -3, 17, 27) in backend.calls
    assert ("hotkey", ["ctrl", "shift", "s"]) in backend.calls


def conditional_macro() -> Macro:
    step = if_image_step("x.png", click_on_match=True, match_offset=[5, -5])
    step.then_steps = [key_step("press", key="esc")]
    step.else_steps = [key_step("press", key="tab")]
    macro = Macro(repeat=1)
    macro.steps = [step]
    return macro


def test_then_branch_and_match_offset(backend, monkeypatch):
    set_image_result(monkeypatch, found())
    play(conditional_macro())

    assert ("click", 782, 883, "left", 1) in backend.calls  # centre + offset
    assert ("press", "esc") in backend.calls
    assert ("press", "tab") not in backend.calls


def test_else_branch_when_image_absent(backend, monkeypatch):
    set_image_result(monkeypatch, None)
    play(conditional_macro())

    assert ("press", "tab") in backend.calls
    assert ("press", "esc") not in backend.calls


def test_recorded_input_replays(backend):
    """Whatever the recorder produces must survive a playback pass."""
    recorder = Recorder()
    recorder._running = True
    recorder._on_click(100, 200, pmouse.Button.left, True)
    recorder._on_click(100, 200, pmouse.Button.left, False)   # click
    recorder._on_click(10, 10, pmouse.Button.left, True)
    recorder._on_click(80, 90, pmouse.Button.left, False)     # drag
    recorder._on_click(5, 5, pmouse.Button.right, True)
    recorder._on_click(5, 5, pmouse.Button.right, False)      # right click
    recorder._on_scroll(1, 2, 0, -2)
    steps = recorder.stop()

    macro = Macro(repeat=1)
    macro.steps = steps
    play(macro)

    assert {"click", "drag", "scroll"} <= set(backend.names)
    assert [c for c in backend.calls if c[0] == "click" and c[3] == "right"]


def test_disabled_steps_skipped_and_repeat_honoured(backend):
    enabled = key_step("press", key="a")
    disabled = key_step("press", key="b")
    disabled.enabled = False
    macro = Macro(repeat=3)
    macro.steps = [enabled, disabled]

    play(macro)
    assert backend.calls == [("press", "a")] * 3


def test_stop_event_breaks_infinite_macro(backend):
    macro = Macro(repeat=0)
    macro.steps = [key_step("press", key="a"), delay_step(20)]
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    try:
        play(macro, stop)
    finally:
        timer.cancel()

    assert backend.calls
    assert stop.is_set()
