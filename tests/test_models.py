"""Macro file format: round-trip, deep copy, validation."""

import json
import pytest

from mymacro.models import (
    Macro, Step, delay_step, if_image_step, key_step, mouse_step,
)


def sample_macro() -> Macro:
    macro = Macro(name="t", repeat=3, repeat_delay_ms=250)
    macro.steps = [
        mouse_step("click", x=100, y=200),
        delay_step(500, jitter_ms=50),
        key_step("hotkey", keys=["ctrl", "c"]),
        key_step("type", text="안녕 hello", interval_ms=10),
        if_image_step("macros/images/x.png", timeout_ms=3000, click_on_match=True),
    ]
    macro.steps[-1].then_steps = [mouse_step("double_click", x=5, y=6), delay_step(100)]
    macro.steps[-1].else_steps = [key_step("press", key="esc")]
    return macro


def test_json_round_trip(tmp_path):
    macro = sample_macro()
    path = tmp_path / "m.json"
    macro.save(path)
    back = Macro.load(path)

    assert back.to_dict() == macro.to_dict()
    assert back.repeat == 3 and back.repeat_delay_ms == 250
    assert len(back.steps[-1].then_steps) == 2
    assert len(back.steps[-1].else_steps) == 1


def test_saved_file_is_readable_utf8(tmp_path):
    path = tmp_path / "m.json"
    sample_macro().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "안녕 hello" in json.dumps(data, ensure_ascii=False)


def test_copy_is_deep():
    macro = sample_macro()
    clone = macro.steps[-1].copy()
    clone.then_steps[0].params["x"] = 999
    assert macro.steps[-1].then_steps[0].params["x"] == 5


def test_describe_never_fails():
    macro = sample_macro()
    for step in macro.steps + macro.steps[-1].then_steps:
        assert step.describe()


def test_unknown_step_type_rejected():
    with pytest.raises(ValueError):
        Step.from_dict({"type": "nope"})
