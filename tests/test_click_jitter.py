"""Scattering the click instead of hitting the same pixel every time."""

import threading
import types

import pytest

from mymacro import player as player_mod
from mymacro.models import Macro, if_image_step
from mymacro.player import CLICK_EDGE_INSET_PX, click_point
from mymacro.vision import Match
from tests.test_playback import FakeBackend


def match_at(left=400, top=300, width=60, height=24) -> Match:
    return Match(
        x=left + width // 2, y=top + height // 2, score=0.99,
        left=left, top=top, width=width, height=height,
    )


# --- the geometry on its own ---------------------------------------------

def test_no_jitter_means_the_exact_point():
    match = match_at()
    assert click_point(match, (0, 0), 0) == (match.x, match.y)
    assert click_point(match, (5, -7), 0) == (match.x + 5, match.y - 7)


def test_jittered_clicks_stay_within_the_requested_range():
    match = match_at(width=400, height=400)  # roomy, so clamping never bites
    points = {click_point(match, (0, 0), 6) for _ in range(400)}

    for x, y in points:
        assert abs(x - match.x) <= 6
        assert abs(y - match.y) <= 6


def test_jittered_clicks_actually_move_around():
    match = match_at(width=400, height=400)
    points = {click_point(match, (0, 0), 6) for _ in range(400)}

    assert len(points) > 20, "the click should not keep landing on one pixel"


def test_a_click_aimed_at_the_image_never_leaves_it():
    """A scattered click that lands beside the button would do nothing."""
    match = match_at(left=400, top=300, width=20, height=10)

    for _ in range(500):
        x, y = click_point(match, (0, 0), 100)  # far wider than the match
        assert match.left + CLICK_EDGE_INSET_PX <= x <= match.left + match.width - 1 - CLICK_EDGE_INSET_PX
        assert match.top + CLICK_EDGE_INSET_PX <= y <= match.top + match.height - 1 - CLICK_EDGE_INSET_PX


def test_a_deliberate_offset_outside_the_image_is_respected():
    """match_offset is how you click the thing next to the icon."""
    match = match_at(left=400, top=300, width=20, height=10)
    far = 200

    points = [click_point(match, (far, 0), 4) for _ in range(200)]
    for x, _ in points:
        assert abs(x - (match.x + far)) <= 4, "must not be dragged back into the match"


def test_jitter_is_clamped_but_still_varies_in_a_tight_match():
    match = match_at(left=0, top=0, width=9, height=9)
    points = {click_point(match, (0, 0), 3) for _ in range(300)}

    assert len(points) > 1
    for x, y in points:
        assert 1 <= x <= 7 and 1 <= y <= 7


# --- through the player ---------------------------------------------------

@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def play(step, backend_unused=None) -> None:
    macro = Macro(repeat=1)
    macro.steps = [step]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()


def set_match(monkeypatch, match) -> None:
    monkeypatch.setattr(
        player_mod, "vision",
        types.SimpleNamespace(wait_for=lambda *a, **k: match, find=lambda *a, **k: match),
    )


def test_player_clicks_the_exact_centre_by_default(backend, monkeypatch):
    match = match_at()
    set_match(monkeypatch, match)
    play(if_image_step("a.png", click_on_match=True))

    assert backend.calls == [("click", match.x, match.y, "left", 1)]


def test_player_scatters_the_click_when_asked(backend, monkeypatch):
    match = match_at(width=200, height=200)
    set_match(monkeypatch, match)

    macro = Macro(repeat=40)
    macro.steps = [if_image_step("a.png", click_on_match=True, click_jitter_px=8)]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    points = {(call[1], call[2]) for call in backend.calls if call[0] == "click"}
    assert len(points) > 5, "repeated runs should not reuse one pixel"
    for x, y in points:
        assert abs(x - match.x) <= 8 and abs(y - match.y) <= 8


def test_jitter_shows_up_in_the_step_description():
    assert "찾으면 클릭 (±5px)" in if_image_step(
        "a.png", click_on_match=True, click_jitter_px=5
    ).describe()
    assert "찾으면 클릭" in if_image_step("a.png", click_on_match=True).describe()
    assert "±" not in if_image_step("a.png", click_on_match=True).describe()


def test_jitter_survives_a_save(tmp_path):
    macro = Macro(steps=[if_image_step("a.png", click_jitter_px=7)])
    path = tmp_path / "m.json"
    macro.save(path)
    assert Macro.load(path).steps[0].params["click_jitter_px"] == 7


# --- fixed coordinates, the same idea ------------------------------------

from mymacro.models import mouse_step  # noqa: E402
from mymacro.player import jitter_point  # noqa: E402


def test_jitter_point_leaves_an_unset_coordinate_alone():
    """No coordinate means "use the current pointer position"."""
    assert jitter_point(None, None, 10) == (None, None)
    assert jitter_point(None, 5, 10) == (None, 5)
    assert jitter_point(100, 200, 0) == (100, 200)


def test_jitter_point_stays_in_range_and_moves_around():
    points = {jitter_point(1000, 500, 7) for _ in range(400)}

    assert len(points) > 20
    for x, y in points:
        assert abs(x - 1000) <= 7 and abs(y - 500) <= 7


def test_mouse_click_can_be_scattered(backend, monkeypatch):
    macro = Macro(repeat=40)
    macro.steps = [mouse_step("click", x=800, y=600, jitter_px=5)]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    points = {(call[1], call[2]) for call in backend.calls}
    assert len(points) > 5
    for x, y in points:
        assert abs(x - 800) <= 5 and abs(y - 600) <= 5


def test_mouse_click_without_jitter_is_exact(backend):
    macro = Macro(repeat=5)
    macro.steps = [mouse_step("click", x=800, y=600)]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    assert backend.calls == [("click", 800, 600, "left", 1)] * 5


def test_drag_scatters_both_ends(backend, monkeypatch):
    macro = Macro(repeat=30)
    macro.steps = [mouse_step("drag", x=100, y=100, to_x=400, to_y=400, jitter_px=6)]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    starts = {(c[1], c[2]) for c in backend.calls if c[0] == "drag"}
    ends = {(c[3], c[4]) for c in backend.calls if c[0] == "drag"}
    assert len(starts) > 3 and len(ends) > 3
    for x, y in starts:
        assert abs(x - 100) <= 6 and abs(y - 100) <= 6
    for x, y in ends:
        assert abs(x - 400) <= 6 and abs(y - 400) <= 6


def test_a_step_using_the_current_position_is_never_scattered(backend):
    macro = Macro(repeat=3)
    macro.steps = [mouse_step("click", x=None, y=None, jitter_px=20)]
    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    assert backend.calls == [("click", None, None, "left", 1)] * 3


def test_mouse_jitter_shows_up_in_the_step_description():
    assert "±4px" in mouse_step("click", x=1, y=2, jitter_px=4).describe()
    assert "±" not in mouse_step("click", x=1, y=2).describe()
    # nothing to scatter when there is no coordinate
    assert "±" not in mouse_step("click", x=None, y=None, jitter_px=4).describe()
