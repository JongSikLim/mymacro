"""Random action blocks, and targeting another application's window."""

import threading
import types
from collections import Counter

import pytest

from mymacro import models, player as player_mod
from mymacro import windows as win
from mymacro.models import (
    Macro, key_step, mouse_step, option_step, random_step, sync_random_options, window_step,
)
from tests.test_playback import FakeBackend


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def play(macro: Macro, logs: list[str] | None = None) -> None:
    sink = logs.append if logs is not None else (lambda _m: None)
    player_mod.Player(macro, threading.Event(), sink, failsafe=False).run()


def two_options() -> models.Step:
    """The example from the request: a key press or a mouse click."""
    block = random_step(2)
    block.children[0].children = [key_step("press", key="enter")]
    block.children[1].children = [mouse_step("click", x=300, y=400)]
    return block


# --- random blocks --------------------------------------------------------

def test_exactly_one_option_runs(backend):
    macro = Macro(repeat=1)
    macro.steps = [two_options()]
    play(macro)

    assert len(backend.calls) == 1
    assert backend.calls[0] in (("press", "enter"), ("click", 300, 400, "left", 1))


def test_both_options_come_up_over_many_runs(backend):
    macro = Macro(repeat=200)
    macro.steps = [two_options()]
    play(macro)

    picks = Counter(call[0] for call in backend.calls)
    assert picks["press"] > 40, picks
    assert picks["click"] > 40, picks
    assert sum(picks.values()) == 200


def test_an_option_can_hold_several_steps(backend):
    block = random_step(2)
    block.children[0].children = [key_step("press", key="a"), key_step("press", key="b")]
    block.children[1].children = [key_step("press", key="z")]
    macro = Macro(repeat=1)
    macro.steps = [block]
    play(macro)

    pressed = [call[1] for call in backend.calls]
    assert pressed in (["a", "b"], ["z"])


def test_a_disabled_option_is_never_picked(backend):
    block = two_options()
    block.children[1].enabled = False
    macro = Macro(repeat=30)
    macro.steps = [block]
    play(macro)

    assert {call[0] for call in backend.calls} == {"press"}


def test_an_empty_random_block_is_skipped(backend):
    block = random_step(2)
    for option in block.children:
        option.enabled = False
    macro = Macro(repeat=1)
    macro.steps = [block, key_step("press", key="after")]
    logs: list[str] = []
    play(macro, logs)

    assert backend.calls == [("press", "after")]
    assert any("선택지가 없어" in line for line in logs)


def test_the_chosen_option_is_logged(backend):
    macro = Macro(repeat=1)
    macro.steps = [two_options()]
    logs: list[str] = []
    play(macro, logs)

    assert any("선택지" in line and "번 실행" in line for line in logs)


def test_option_count_syncs_both_ways():
    block = random_step(2)
    block.children[0].children = [key_step("press", key="a")]

    block.params["count"] = 4
    sync_random_options(block)
    assert len(block.children) == 4
    assert block.children[0].children, "existing options keep their steps"

    block.params["count"] = 2
    sync_random_options(block)
    assert len(block.children) == 2

    block.params["count"] = 1  # below the minimum
    sync_random_options(block)
    assert len(block.children) == 2


def test_random_block_survives_a_save(tmp_path):
    macro = Macro(steps=[two_options()])
    path = tmp_path / "m.json"
    macro.save(path)

    back = Macro.load(path).steps[0]
    assert back.type == models.RANDOM
    assert len(back.children) == 2
    assert back.children[0].children[0].params["key"] == "enter"
    assert [label for label, _ in back.branches()] == ["└ 선택지 1", "└ 선택지 2"]


# --- window targeting -----------------------------------------------------

def fake_window(title="메모장", process="notepad.exe", handle=42):
    return win.WindowInfo(handle=handle, title=title, pid=1, process=process)


@pytest.mark.parametrize(
    "title, process, expected",
    [
        ("메모", "", True),
        ("메모장", "notepad.exe", True),
        ("크롬", "", False),
        ("", "NOTEPAD.EXE", True),      # case-insensitive
        ("메모", "chrome.exe", False),   # every filter must hold
        ("", "", False),                 # no filter matches nothing
    ],
)
def test_window_matching(title, process, expected):
    assert win.matches(fake_window(), title, process) is expected


def test_activate_brings_the_window_forward(backend, monkeypatch):
    activated: list[int] = []
    monkeypatch.setattr(win, "find", lambda t, p: fake_window())
    monkeypatch.setattr(win, "activate", lambda h: activated.append(h) or True)
    monkeypatch.setattr(player_mod, "win", win)

    macro = Macro(repeat=1)
    macro.steps = [window_step("activate", title="메모장")]
    logs: list[str] = []
    play(macro, logs)

    assert activated == [42]
    assert any("활성화 완료" in line for line in logs)


def test_a_refused_activation_is_reported_not_swallowed(monkeypatch, backend):
    monkeypatch.setattr(win, "find", lambda t, p: fake_window())
    monkeypatch.setattr(win, "activate", lambda h: False)
    monkeypatch.setattr(player_mod, "win", win)

    macro = Macro(repeat=1)
    macro.steps = [window_step("activate", title="메모장")]
    logs: list[str] = []
    play(macro, logs)

    assert any("활성화 실패" in line for line in logs)


def test_wait_only_finds_the_window_without_focusing_it(monkeypatch, backend):
    activated: list[int] = []
    monkeypatch.setattr(win, "find", lambda t, p: fake_window())
    monkeypatch.setattr(win, "activate", lambda h: activated.append(h) or True)
    monkeypatch.setattr(player_mod, "win", win)

    macro = Macro(repeat=1)
    macro.steps = [window_step("wait", title="메모장")]
    play(macro)

    assert activated == []


def test_a_missing_window_times_out_and_the_macro_goes_on(monkeypatch, backend):
    monkeypatch.setattr(win, "find", lambda t, p: None)
    monkeypatch.setattr(player_mod, "win", win)

    macro = Macro(repeat=1)
    macro.steps = [
        window_step("activate", title="없는창", timeout_ms=100),
        key_step("press", key="after"),
    ]
    logs: list[str] = []
    play(macro, logs)

    assert backend.calls == [("press", "after")]
    assert any("창을 찾지 못했습니다" in line for line in logs)


def test_a_window_appearing_late_is_still_caught(monkeypatch, backend):
    attempts = {"n": 0}

    def late_find(_title, _process):
        attempts["n"] += 1
        return fake_window() if attempts["n"] >= 3 else None

    monkeypatch.setattr(win, "find", late_find)
    monkeypatch.setattr(win, "activate", lambda h: True)
    monkeypatch.setattr(player_mod, "win", win)

    macro = Macro(repeat=1)
    macro.steps = [window_step("activate", title="메모장", timeout_ms=5000)]
    logs: list[str] = []
    play(macro, logs)

    assert attempts["n"] >= 3
    assert any("활성화 완료" in line for line in logs)


def test_a_window_step_with_no_target_is_skipped(monkeypatch, backend):
    macro = Macro(repeat=1)
    macro.steps = [window_step("activate"), key_step("press", key="after")]
    logs: list[str] = []
    play(macro, logs)

    assert backend.calls == [("press", "after")]
    assert any("대상 창이 지정되지 않아" in line for line in logs)


def test_window_helpers_are_safe_off_windows(monkeypatch):
    """The whole module has to be callable on any platform."""
    monkeypatch.setattr(win, "IS_WINDOWS", False)
    assert win.list_windows() == []
    assert win.find("x") is None
    assert win.activate(1) is False
    assert win.rect(1) is None
    assert win.foreground_handle() == 0
