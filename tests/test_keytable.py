"""The key tables must line up with pynput's *Windows* Key enum.

The enum differs per platform, so the Windows source file is parsed instead of
introspecting the pynput that happens to be running. That keeps this test
meaningful even when it runs on another OS.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

from mymacro import keymap
from mymacro.input_backend import _KEY_ALIASES


def windows_key_names() -> set[str]:
    spec = importlib.util.find_spec("pynput")
    assert spec and spec.submodule_search_locations
    source = Path(spec.submodule_search_locations[0]) / "keyboard" / "_win32.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Key":
            return {
                target.id
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                for target in stmt.targets
                if isinstance(target, ast.Name)
            }
    raise AssertionError("pynput Windows Key enum not found")


@pytest.fixture(scope="module")
def win_keys() -> set[str]:
    return windows_key_names()


def test_every_alias_exists_on_windows(win_keys):
    missing = {name: alias for name, alias in _KEY_ALIASES.items() if alias not in win_keys}
    assert not missing, f"not real Windows keys: {missing}"


def test_recorder_names_are_replayable():
    """Anything the recorder writes must be resolvable at playback time."""
    produced = set(keymap._SPECIAL_NAMES.values())
    unresolvable = sorted(
        name for name in produced
        if name not in _KEY_ALIASES and len(name) != 1 and not name.startswith("num")
    )
    assert not unresolvable


def test_keymap_sources_are_windows_keys(win_keys):
    unknown = sorted(name for name in keymap._SPECIAL_NAMES if name not in win_keys)
    assert not unknown


def test_modifiers_and_dropdown_resolve():
    for modifier in keymap.MODIFIER_KEYS:
        assert modifier in _KEY_ALIASES
    unresolvable = [k for k in keymap.COMMON_KEYS if k not in _KEY_ALIASES and len(k) != 1]
    assert not unresolvable
