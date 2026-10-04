"""Build the real widgets offscreen.

Catches Qt API misuse and tree/dialog wiring mistakes without a desktop.
Global hotkeys are stubbed: starting a pynput listener needs desktop
permissions and is not what these tests are about.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from mymacro import hotkeys, models  # noqa: E402
from mymacro.models import (  # noqa: E402
    Macro, delay_step, if_image_step, jump_step, key_step, label_step, loop_step, mouse_step,
)


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, monkeypatch):
    monkeypatch.setattr(hotkeys.GlobalHotkeys, "start", lambda self: None)
    monkeypatch.setattr(hotkeys.GlobalHotkeys, "stop", lambda self: None)
    from mymacro.ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def scenario_macro() -> Macro:
    done = if_image_step("done.png")
    done.then_steps = [jump_step("break")]
    reset = if_image_step("reset.png")
    reset.then_steps = [jump_step("goto", "시작")]
    body = loop_step("forever")
    body.children = [key_step("press", key="a"), done, reset]
    return Macro(
        steps=[
            label_step("시작"),
            mouse_step("click", x=10, y=20),
            body,
            key_step("press", key="end"),
        ]
    )


def test_nested_macro_renders_every_node(window):
    window.macro = scenario_macro()
    window._refresh_tree()

    kinds = [node[0] for node in window._nodes.values()]
    # 4 top-level steps + 5 nested ones (a, done, break, reset, goto)
    assert kinds.count("step") == 9
    # the loop body, plus then/else for each of the two image conditions
    assert kinds.count("group") == 5


def test_nested_steps_are_addressable_at_the_right_depth(window):
    window.macro = scenario_macro()
    window._refresh_tree()

    depths = {
        node[3]
        for node in window._nodes.values()
        if node[0] == "step"
    }
    assert depths == {0, 1, 2}  # top level, loop body, inside the condition


def test_missing_goto_target_is_reported(window):
    window.macro = scenario_macro()
    assert window._missing_labels() == set()

    # the second image condition is the one holding the goto
    window.macro.steps[2].children[2].then_steps[0].params["target"] = "없음"
    assert window._missing_labels() == {"없음"}


@pytest.mark.parametrize("step_type", models.STEP_TYPES)
def test_every_dialog_builds_and_returns_params(window, step_type):
    from mymacro.ui import step_dialogs

    dialog = step_dialogs.DIALOGS[step_type](window, None, images_dir=None, macro=window.macro)
    try:
        assert isinstance(dialog.to_params(), dict)
    finally:
        dialog.deleteLater()


EXISTING_STEPS = {
    models.MOUSE: mouse_step("drag", x=1, y=2, to_x=3, to_y=4, button="left"),
    models.KEY: key_step("hotkey", keys=["ctrl", "s"]),
    models.DELAY: delay_step(500, 50),
    models.IF_IMAGE: if_image_step("a.png", timeout_ms=1000),
    models.LOOP: loop_step("while_image", image="b.png"),
    models.LABEL: label_step("시작"),
    models.JUMP: jump_step("goto", "시작"),
}


@pytest.mark.parametrize("step_type", models.STEP_TYPES)
def test_every_dialog_round_trips_an_existing_step(window, step_type):
    from mymacro.ui import step_dialogs

    step = EXISTING_STEPS[step_type]
    dialog = step_dialogs.DIALOGS[step_type](window, step, images_dir=None, macro=window.macro)
    try:
        params = dialog.to_params()
        assert params
        if step_type == models.LABEL:
            assert params["name"] == "시작"
        if step_type == models.JUMP:
            assert params["action"] == "goto" and params["target"] == "시작"
        if step_type == models.LOOP:
            assert params["mode"] == "while_image"
    finally:
        dialog.deleteLater()
