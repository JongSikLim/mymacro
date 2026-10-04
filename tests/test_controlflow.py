"""Loops, labels and jumps.

The last test is the scenario this feature was added for: a prerequisite
section, then a block that loops, with one condition that breaks out and
another that sends the macro back to the start.
"""

import threading
import types
from pathlib import Path

import pytest

from mymacro import player as player_mod
from mymacro.models import (
    Macro, delay_step, if_image_step, jump_step, key_step, label_step, loop_step,
)
from tests.test_playback import FakeBackend, found


class FakeVision:
    """Hands out scripted results per image file name, in order.

    Anything not scripted, or a script that ran out, counts as "not found".
    """

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {name: list(results) for name, results in script.items()}
        self.checked: list[str] = []

    def _next(self, image):
        name = Path(str(image)).name
        self.checked.append(name)
        queue = self.script.get(name)
        return queue.pop(0) if queue else None

    def wait_for(self, image, **_kwargs):
        return self._next(image)

    def find(self, image, **_kwargs):
        return self._next(image)


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", fake)
    return fake


def use_vision(monkeypatch, script: dict[str, list]) -> FakeVision:
    fake = FakeVision(script)
    monkeypatch.setattr(
        player_mod, "vision", types.SimpleNamespace(wait_for=fake.wait_for, find=fake.find)
    )
    return fake


def play(macro: Macro, logs: list[str] | None = None) -> None:
    sink = logs.append if logs is not None else (lambda _m: None)
    player_mod.Player(macro, threading.Event(), sink, failsafe=False).run()


def macro_of(*steps) -> Macro:
    macro = Macro(repeat=1)
    macro.steps = list(steps)
    return macro


def pressed(backend: FakeBackend) -> list[str]:
    return [call[1] for call in backend.calls if call[0] == "press"]


# --- loops ---------------------------------------------------------------

def test_loop_runs_a_fixed_number_of_times(backend):
    block = loop_step("count", count=3)
    block.children = [key_step("press", key="a")]
    play(macro_of(block))
    assert pressed(backend) == ["a"] * 3


def test_steps_after_a_loop_still_run(backend):
    block = loop_step("count", count=2)
    block.children = [key_step("press", key="a")]
    play(macro_of(block, key_step("press", key="done")))
    assert pressed(backend) == ["a", "a", "done"]


def test_break_leaves_the_loop(backend, monkeypatch):
    # The image appears on the third check, which is when the break fires.
    use_vision(monkeypatch, {"stop.png": [None, None, found()]})
    cond = if_image_step("stop.png")
    cond.then_steps = [jump_step("break")]
    block = loop_step("forever", max_iterations=50)
    block.children = [key_step("press", key="a"), cond]

    play(macro_of(block, key_step("press", key="after")))
    assert pressed(backend) == ["a", "a", "a", "after"]


def test_continue_skips_the_rest_of_the_iteration(backend, monkeypatch):
    use_vision(monkeypatch, {"skip.png": [found(), None, None]})
    cond = if_image_step("skip.png")
    cond.then_steps = [jump_step("continue")]
    block = loop_step("count", count=3)
    block.children = [cond, key_step("press", key="tail")]

    play(macro_of(block))
    assert pressed(backend) == ["tail", "tail"]  # first iteration skipped


def test_while_image_loops_while_present(backend, monkeypatch):
    use_vision(monkeypatch, {"busy.png": [found(), found(), None]})
    block = loop_step("while_image", image="busy.png", max_iterations=50)
    block.children = [key_step("press", key="a")]
    play(macro_of(block))
    assert pressed(backend) == ["a", "a"]


def test_until_image_loops_until_present(backend, monkeypatch):
    use_vision(monkeypatch, {"ready.png": [None, None, found()]})
    block = loop_step("until_image", image="ready.png", max_iterations=50)
    block.children = [key_step("press", key="a")]
    play(macro_of(block))
    assert pressed(backend) == ["a", "a"]


def test_max_iterations_caps_a_runaway_loop(backend, monkeypatch):
    use_vision(monkeypatch, {"never.png": []})  # never found
    block = loop_step("until_image", image="never.png", max_iterations=4)
    block.children = [key_step("press", key="a")]
    logs: list[str] = []
    play(macro_of(block), logs)
    assert pressed(backend) == ["a"] * 4
    assert any("최대 반복" in line for line in logs)


def test_nested_loops(backend):
    inner = loop_step("count", count=2)
    inner.children = [key_step("press", key="i")]
    outer = loop_step("count", count=3)
    outer.children = [inner, key_step("press", key="o")]
    play(macro_of(outer))
    assert pressed(backend) == ["i", "i", "o"] * 3


def test_break_only_leaves_the_inner_loop(backend, monkeypatch):
    use_vision(monkeypatch, {"x.png": [found()] * 10})
    cond = if_image_step("x.png")
    cond.then_steps = [jump_step("break")]
    inner = loop_step("forever", max_iterations=50)
    inner.children = [key_step("press", key="i"), cond]
    outer = loop_step("count", count=2)
    outer.children = [inner, key_step("press", key="o")]

    play(macro_of(outer))
    assert pressed(backend) == ["i", "o", "i", "o"]


# --- labels and jumps ----------------------------------------------------

def test_restart_goes_back_to_the_first_step(backend, monkeypatch):
    use_vision(monkeypatch, {"again.png": [found(), None]})
    cond = if_image_step("again.png")
    cond.then_steps = [jump_step("restart")]
    play(macro_of(key_step("press", key="a"), cond, key_step("press", key="end")))
    assert pressed(backend) == ["a", "a", "end"]


def test_goto_jumps_to_a_label(backend, monkeypatch):
    use_vision(monkeypatch, {"back.png": [found(), None]})
    cond = if_image_step("back.png")
    cond.then_steps = [jump_step("goto", "중간")]
    play(
        macro_of(
            key_step("press", key="head"),
            label_step("중간"),
            key_step("press", key="body"),
            cond,
            key_step("press", key="tail"),
        )
    )
    # 'head' runs once; the jump re-enters at the label, so 'body' runs twice.
    assert pressed(backend) == ["head", "body", "body", "tail"]


def test_stop_ends_the_whole_macro(backend):
    macro = macro_of(key_step("press", key="a"), jump_step("stop"), key_step("press", key="b"))
    macro.repeat = 5
    play(macro)
    assert pressed(backend) == ["a"]


def test_missing_label_ends_the_pass_without_crashing(backend):
    logs: list[str] = []
    play(macro_of(key_step("press", key="a"), jump_step("goto", "없는라벨")), logs)
    assert pressed(backend) == ["a"]
    assert any("찾을 수 없" in line for line in logs)


def test_jump_loop_with_no_work_is_stopped(backend):
    """A label followed straight by a goto would spin at full speed forever."""
    logs: list[str] = []
    play(macro_of(label_step("시작"), jump_step("goto", "시작")), logs)
    assert any("무한 루프" in line for line in logs)


def test_label_itself_does_nothing(backend):
    play(macro_of(label_step("시작"), key_step("press", key="a")))
    assert pressed(backend) == ["a"]


# --- the scenario this was built for -------------------------------------

def test_prerequisite_then_conditional_loop_with_return_to_start(backend, monkeypatch):
    """1~5 선행 진행, 6~10 루프, 조건에 따라 탈출하거나 1로 회귀.

    Pass 1: prerequisite runs, the loop body runs once, the reset condition
    fires and sends the macro back to the label.
    Pass 2: prerequisite runs again, the done condition fires and breaks out.
    """
    vision = use_vision(
        monkeypatch,
        {
            "done.png": [None, found()],   # not done first time, done second
            "reset.png": [found()],        # asks for a restart first time
        },
    )

    done = if_image_step("done.png")
    done.then_steps = [jump_step("break")]
    reset = if_image_step("reset.png")
    reset.then_steps = [jump_step("goto", "시작")]

    body = loop_step("forever", max_iterations=100)
    body.children = [key_step("press", key="work"), done, reset]

    macro = macro_of(
        label_step("시작"),
        key_step("press", key="prep1"),
        key_step("press", key="prep2"),
        delay_step(1),
        body,
        key_step("press", key="finish"),
    )

    logs: list[str] = []
    play(macro, logs)

    assert pressed(backend) == [
        "prep1", "prep2", "work",   # first pass, then the reset sends us back
        "prep1", "prep2", "work",   # second pass
        "finish",                   # the loop broke out, so the tail runs
    ]
    assert vision.checked == ["done.png", "reset.png", "done.png"]
    assert any("라벨 '시작' 로 이동" in line for line in logs)
