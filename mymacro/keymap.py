"""Translate pynput key objects into the plain key names used in saved macros.

The names follow the familiar pyautogui spelling ("ctrlleft", "pageup") so a
saved macro file stays readable; `input_backend.resolve_key` turns them back
into pynput keys at playback time.
"""

from __future__ import annotations

from typing import Any

from pynput import keyboard

# pynput special keys whose name differs from the name we store.
_SPECIAL_NAMES = {
    "alt_l": "altleft",
    "alt_r": "altright",
    "alt_gr": "altright",
    "ctrl_l": "ctrlleft",
    "ctrl_r": "ctrlright",
    "shift_l": "shiftleft",
    "shift_r": "shiftright",
    "cmd": "win",
    "cmd_l": "winleft",
    "cmd_r": "winright",
    "page_up": "pageup",
    "page_down": "pagedown",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "print_screen": "printscreen",
    "esc": "esc",
    "enter": "enter",
    "space": "space",
    "backspace": "backspace",
    "tab": "tab",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "menu": "apps",
    "pause": "pause",
}

MODIFIER_KEYS = {
    "ctrl", "ctrlleft", "ctrlright",
    "shift", "shiftleft", "shiftright",
    "alt", "altleft", "altright",
    "win", "winleft", "winright",
}

# Names offered in the key dropdown of the editor.
COMMON_KEYS = [
    "enter", "esc", "tab", "space", "backspace", "delete", "insert",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "ctrl", "shift", "alt", "win", "capslock",
    *[f"f{i}" for i in range(1, 13)],
    *list("abcdefghijklmnopqrstuvwxyz"),
    *[str(d) for d in range(10)],
]


def normalize_modifier(name: str) -> str:
    """Collapse left/right variants so hotkeys stay readable."""
    for base in ("ctrl", "shift", "alt", "win"):
        if name.startswith(base):
            return base
    return name


def from_pynput(key: Any) -> str | None:
    """Return a stored key name, or None when the key cannot be mapped."""
    if isinstance(key, keyboard.Key):
        name = key.name
        return _SPECIAL_NAMES.get(name, name)

    if isinstance(key, keyboard.KeyCode):
        char = key.char
        if char is not None:
            code = ord(char)
            # Ctrl+<letter> arrives as a control character (Ctrl+A -> \x01).
            if code < 32:
                return chr(code + 96)
            return char.lower() if char.isalpha() else char
        # Dead keys and numpad keys only carry a virtual key code.
        if key.vk is not None:
            if 96 <= key.vk <= 105:
                return f"num{key.vk - 96}"
            if 48 <= key.vk <= 57:
                return chr(key.vk)
            if 65 <= key.vk <= 90:
                return chr(key.vk).lower()
    return None
