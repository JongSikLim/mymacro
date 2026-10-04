"""Sending a key when the image is found, next to the existing click option."""

import threading
import types

import pytest

from mymacro import input_backend, models, player as player_mod
from mymacro.models import Macro, if_image_step
from tests.test_playback import FakeBackend, found


# --- the key spec parser -------------------------------------------------

@pytest.fixture
def recorded(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(input_backend, "press", lambda name: calls.append(("press", name)))
    monkeypatch.setattr(input_backend, "hotkey", lambda names: calls.append(("hotkey", list(names))))
    return calls


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("enter", [("press", "enter")]),
        ("  F5 ", [("press", "f5")]),
        ("ctrl+v", [("hotkey", ["ctrl", "v"])]),
        ("Ctrl + Shift + S", [("hotkey", ["ctrl", "shift", "s"])]),
        ("", []),
        ("   ", []),
        ("+", []),
    ],
)
def test_press_combo_handles_single_keys_and_combos(recorded, spec, expected):
    input_backend.press_combo(spec)
    assert recorded == expected


# --- the player ----------------------------------------------------------

@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def set_match(monkeypatch, match) -> None:
    monkeypatch.setattr(
        player_mod, "vision",
        types.SimpleNamespace(wait_for=lambda *a, **k: match, find=lambda *a, **k: match),
    )


def play(step, logs: list[str] | None = None) -> None:
    macro = Macro(repeat=1)
    macro.steps = [step]
    sink = logs.append if logs is not None else (lambda _m: None)
    player_mod.Player(macro, threading.Event(), sink, failsafe=False).run()


def test_key_is_sent_after_the_click(backend, monkeypatch):
    hit = found(400, 300)
    set_match(monkeypatch, hit)
    play(if_image_step("a.png", click_on_match=True, key_on_match="enter"))

    assert backend.calls == [
        ("click", hit.x, hit.y, "left", 1),
        ("press", "enter"),
    ]


def test_key_without_a_click(backend, monkeypatch):
    set_match(monkeypatch, found(400, 300))
    play(if_image_step("a.png", click_on_match=False, key_on_match="f5"))

    assert backend.calls == [("press", "f5")]


def test_combo_key_on_match(backend, monkeypatch):
    set_match(monkeypatch, found(400, 300))
    play(if_image_step("a.png", key_on_match="ctrl+shift+s"))

    assert backend.calls == [("hotkey", ["ctrl", "shift", "s"])]


def test_no_key_when_the_field_is_empty(backend, monkeypatch):
    set_match(monkeypatch, found(400, 300))
    play(if_image_step("a.png", click_on_match=True, key_on_match=""))

    assert backend.calls == [("click", 400, 300, "left", 1)]


def test_nothing_is_sent_when_the_image_is_absent(backend, monkeypatch):
    set_match(monkeypatch, None)
    play(if_image_step("a.png", click_on_match=True, key_on_match="enter"))

    assert backend.calls == []


def test_key_runs_before_the_then_branch(backend, monkeypatch):
    """Click, then key, then whatever the 찾았을 때 branch holds."""
    set_match(monkeypatch, found(400, 300))
    step = if_image_step("a.png", click_on_match=True, key_on_match="enter")
    step.then_steps = [models.key_step("press", key="tab")]
    play(step)

    assert [call[0] for call in backend.calls] == ["click", "press", "press"]
    assert backend.calls[1] == ("press", "enter")
    assert backend.calls[2] == ("press", "tab")


def test_the_key_is_logged(backend, monkeypatch):
    set_match(monkeypatch, found(400, 300))
    logs: list[str] = []
    play(if_image_step("a.png", key_on_match="ctrl+v"), logs)

    assert any("ctrl+v 입력" in line for line in logs)


# --- how it reads in the step list ---------------------------------------

@pytest.mark.parametrize(
    "params, expected",
    [
        ({"click_on_match": True}, "찾으면 클릭"),
        ({"key_on_match": "enter"}, "찾으면 enter 입력"),
        ({"click_on_match": True, "key_on_match": "ctrl+v"}, "찾으면 클릭, 찾으면 ctrl+v 입력"),
    ],
)
def test_description_mentions_what_happens_on_a_match(params, expected):
    assert expected in if_image_step("popup.png", **params).describe()


def test_key_on_match_survives_a_save(tmp_path):
    macro = Macro(steps=[if_image_step("a.png", key_on_match="ctrl+v")])
    path = tmp_path / "m.json"
    macro.save(path)

    assert Macro.load(path).steps[0].params["key_on_match"] == "ctrl+v"
