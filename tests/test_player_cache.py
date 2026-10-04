"""The player's side of the position cache: what it remembers and when it forgets."""

import threading
import types

import pytest

from mymacro import player as player_mod
from mymacro.models import Macro, if_image_step, key_step, loop_step
from tests.test_playback import FakeBackend, found


class RecordingVision:
    """Records the hot_spot handed to every lookup, and replays scripted hits."""

    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.hot_spots: list[tuple | None] = []

    def wait_for(self, _image, **kwargs):
        self.hot_spots.append(kwargs.get("hot_spot"))
        return self.results.pop(0) if self.results else None

    def find(self, _image, **kwargs):
        return self.wait_for(_image, **kwargs)


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def use_vision(monkeypatch, results: list) -> RecordingVision:
    fake = RecordingVision(results)
    monkeypatch.setattr(
        player_mod, "vision", types.SimpleNamespace(wait_for=fake.wait_for, find=fake.find)
    )
    return fake


def make_player(macro: Macro, logs: list[str] | None = None) -> player_mod.Player:
    sink = logs.append if logs is not None else (lambda _m: None)
    return player_mod.Player(macro, threading.Event(), sink, failsafe=False)


def loop_checking(step, times: int) -> Macro:
    """A counted loop that runs `step` on every turn."""
    block = loop_step("count", count=times)
    block.children = [step, key_step("press", key="a")]
    macro = Macro(repeat=1)
    macro.steps = [block]
    return macro


def test_first_lookup_has_nothing_cached_then_reuses_the_hit(backend, monkeypatch):
    hit = found(300, 200)
    vision = use_vision(monkeypatch, [hit, hit, hit])
    make_player(loop_checking(if_image_step("a.png"), 3)).run()

    assert vision.hot_spots == [None, (hit.left, hit.top), (hit.left, hit.top)]


def test_cache_follows_the_image_when_it_moves(backend, monkeypatch):
    first, second = found(300, 200), found(700, 640)
    vision = use_vision(monkeypatch, [first, second, second])
    make_player(loop_checking(if_image_step("a.png"), 3)).run()

    assert vision.hot_spots == [
        None,
        (first.left, first.top),
        (second.left, second.top),  # the cache was corrected
    ]


def test_a_failed_lookup_forgets_the_position(backend, monkeypatch):
    hit = found(300, 200)
    vision = use_vision(monkeypatch, [hit, None, hit])
    make_player(loop_checking(if_image_step("a.png"), 3)).run()

    assert vision.hot_spots == [None, (hit.left, hit.top), None]


def test_cache_can_be_turned_off_per_step(backend, monkeypatch):
    hit = found(300, 200)
    vision = use_vision(monkeypatch, [hit, hit, hit])
    make_player(loop_checking(if_image_step("a.png", use_cache=False), 3)).run()

    assert vision.hot_spots == [None, None, None]


def test_each_step_has_its_own_cache(backend, monkeypatch):
    first, second = found(100, 100), found(900, 700)
    vision = use_vision(monkeypatch, [first, second, first, second])

    block = loop_step("count", count=2)
    block.children = [if_image_step("a.png"), if_image_step("b.png")]
    macro = Macro(repeat=1)
    macro.steps = [block]
    make_player(macro).run()

    assert vision.hot_spots == [
        None, None,                                   # first turn, nothing known
        (first.left, first.top), (second.left, second.top),
    ]


def test_loop_conditions_use_the_cache_too(backend, monkeypatch):
    hit = found(300, 200)
    vision = use_vision(monkeypatch, [hit, hit, None])
    block = loop_step("while_image", image="busy.png", max_iterations=10)
    block.children = [key_step("press", key="a")]
    macro = Macro(repeat=1)
    macro.steps = [block]

    make_player(macro).run()
    assert vision.hot_spots == [None, (hit.left, hit.top), (hit.left, hit.top)]


def test_cache_starts_empty_on_every_run(backend, monkeypatch):
    hit = found(300, 200)
    vision = use_vision(monkeypatch, [hit, hit])
    macro = Macro(repeat=1)
    macro.steps = [if_image_step("a.png")]
    player = make_player(macro)

    player.run()
    player.run()
    assert vision.hot_spots == [None, None], "a new run must not trust the old screen"


def test_hit_and_miss_are_reported(backend, monkeypatch):
    hit = found(300, 200)
    hit_again = found(300, 200)
    hit_again.from_hot_spot = True
    vision = use_vision(monkeypatch, [hit, hit_again, hit])
    logs: list[str] = []
    make_player(loop_checking(if_image_step("a.png"), 3), logs).run()

    assert any("[캐시]" in line for line in logs)
    assert any("[전체 탐색]" in line for line in logs)
    assert any("위치 캐시: 적중 1 / 빗나감 1" in line for line in logs)
