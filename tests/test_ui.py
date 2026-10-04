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
    models.IF_IMAGE: if_image_step(
        "a.png", timeout_ms=1000, key_on_match="ctrl+v", click_jitter_px=4
    ),
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
        if step_type == models.IF_IMAGE:
            assert params["key_on_match"] == "ctrl+v"
            assert params["click_jitter_px"] == 4
    finally:
        dialog.deleteLater()


# --- regression: capturing an image used to throw the whole edit away ------

class StubRegionPicker:
    """Stands in for the screen overlay; no display needed."""

    region = [100, 200, 80, 40]

    def __init__(self, *args, **kwargs) -> None:
        pass

    def pick(self):
        return self.region


@pytest.fixture
def stub_overlay(monkeypatch, tmp_path):
    from mymacro.ui import step_dialogs

    monkeypatch.setattr(step_dialogs, "RegionPicker", StubRegionPicker)
    monkeypatch.setattr(
        step_dialogs.vision, "save_region_png", lambda region, path: path
    )
    return tmp_path


@pytest.mark.parametrize("action", ["_capture_template", "_pick_region"])
def test_using_the_screen_picker_keeps_the_dialog_open(window, stub_overlay, action):
    """`QDialog.setVisible(False)` exits `exec()`.

    The capture button used to hide the dialog to stay out of the screenshot,
    so `exec()` returned Rejected and the step was silently never added.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    from mymacro.ui.step_dialogs import ImageConditionDialog

    dialog = ImageConditionDialog(window, None, images_dir=stub_overlay)
    QTimer.singleShot(10, getattr(dialog.template, action))
    QTimer.singleShot(300, dialog.accept)

    assert dialog.exec() == QDialog.DialogCode.Accepted, "the edit must survive"
    params = dialog.to_params()
    if action == "_capture_template":
        assert params["image"], "the captured template must be filled in"
    else:
        assert params["region"] == StubRegionPicker.region


def test_adding_an_image_condition_really_inserts_a_step(window, stub_overlay, monkeypatch):
    """The whole path the user takes, from button press to a row in the tree."""
    from mymacro.ui import main_window as mw

    def fake_edit(parent, step_type, step=None, images_dir=None, macro=None):
        assert step_type == models.IF_IMAGE
        return {"image": "x.png", "confidence": 0.85, "region": None,
                "grayscale": True, "use_cache": True, "timeout_ms": 0,
                "click_on_match": True, "match_offset": [0, 0]}

    monkeypatch.setattr(mw, "edit_step", fake_edit)
    window.macro = Macro()
    window._refresh_tree()

    window._add_step(models.IF_IMAGE)

    assert len(window.macro.steps) == 1
    assert window.macro.steps[0].type == models.IF_IMAGE
    assert window.tree.topLevelItemCount() == 1


# --- the "watch an image and click it" block ------------------------------

def build_watch(window, images_dir, **settings):
    from mymacro.ui.step_dialogs import ImageWatchDialog

    dialog = ImageWatchDialog(window, images_dir=images_dir)
    dialog.template.image_path.setText(settings.get("image", "popup.png"))
    dialog.interval.setValue(settings.get("interval", 2.0))
    dialog.click_on_match.setChecked(settings.get("click", True))
    if "after" in settings:
        dialog.after_match.setCurrentIndex(dialog.after_match.findData(settings["after"]))
    if "max_checks" in settings:
        dialog.max_checks.setValue(settings["max_checks"])
    try:
        return dialog.to_steps()
    finally:
        dialog.deleteLater()


def test_image_watch_builds_loop_condition_and_delay(window, stub_overlay):
    steps = build_watch(window, stub_overlay, image="popup.png", interval=2.5)

    assert len(steps) == 1
    block = steps[0]
    assert block.type == models.LOOP
    assert block.params["mode"] == "forever"

    condition, delay = block.children
    assert condition.type == models.IF_IMAGE
    assert condition.params["image"] == "popup.png"
    assert condition.params["click_on_match"] is True
    assert condition.params["timeout_ms"] == 0, "the delay sets the pace, not a wait"
    assert delay.type == models.DELAY
    assert delay.params["ms"] == 2500
    assert condition.then_steps == [], "keeps watching by default"


def test_image_watch_can_stop_after_the_first_hit(window, stub_overlay):
    steps = build_watch(window, stub_overlay, after="stop")
    condition = steps[0].children[0]

    assert len(condition.then_steps) == 1
    assert condition.then_steps[0].type == models.JUMP
    assert condition.then_steps[0].params["action"] == "break"


def test_image_watch_block_actually_clicks_when_the_image_appears(window, stub_overlay, monkeypatch):
    """Run the generated block through the player, not just inspect it."""
    import threading
    import types

    from mymacro import player as player_mod
    from tests.test_playback import FakeBackend, found

    steps = build_watch(window, stub_overlay, interval=0.001, max_checks=3, after="stop")
    macro = Macro(repeat=1)
    macro.steps = steps

    hit = found(640, 480)
    results = [None, hit]
    monkeypatch.setattr(
        player_mod, "vision",
        types.SimpleNamespace(
            wait_for=lambda *a, **k: results.pop(0) if results else None,
            find=lambda *a, **k: results.pop(0) if results else None,
        ),
    )
    backend = FakeBackend()
    monkeypatch.setattr(player_mod, "ib", backend)

    player_mod.Player(macro, threading.Event(), lambda _m: None, failsafe=False).run()

    clicks = [c for c in backend.calls if c[0] == "click"]
    assert clicks == [("click", hit.x, hit.y, "left", 1)], "clicks once, on the match"


def test_image_watch_can_send_a_key(window, stub_overlay):
    from mymacro.ui.step_dialogs import ImageWatchDialog

    dialog = ImageWatchDialog(window, images_dir=stub_overlay)
    dialog.template.image_path.setText("popup.png")
    dialog.key_on_match.setCurrentText("enter")
    try:
        condition = dialog.to_steps()[0].children[0]
    finally:
        dialog.deleteLater()

    assert condition.params["key_on_match"] == "enter"
    assert "찾으면 enter 입력" in condition.describe()


def test_image_watch_passes_the_click_jitter_through(window, stub_overlay):
    from mymacro.ui.step_dialogs import ImageWatchDialog

    dialog = ImageWatchDialog(window, images_dir=stub_overlay)
    dialog.template.image_path.setText("popup.png")
    dialog.click_jitter.setValue(6)
    try:
        condition = dialog.to_steps()[0].children[0]
    finally:
        dialog.deleteLater()

    assert condition.params["click_jitter_px"] == 6
    assert "±6px" in condition.describe()
