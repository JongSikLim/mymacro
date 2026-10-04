"""Input injection for Windows.

Why not pyautogui: its Windows backend calls the legacy `mouse_event` and
`keybd_event` APIs. Two of its choices break this tool.

1. `_sendMouseEvent` normalises the click position against the PRIMARY
   monitor (`65536 * x // GetSystemMetrics(0)`), so every click on a second
   monitor lands in the wrong place, and negative coordinates are impossible.
2. `keybd_event(vk, 0, ...)` sends a zero scan code and never sets
   KEYEVENTF_EXTENDEDKEY, so arrows, Delete/Insert, Home/End, PageUp/PageDown
   and right-hand Ctrl/Alt are sent without their extended bit.

pynput - already a dependency for recording - uses `SendInput`, the API
Microsoft recommends. It fills in the scan code via
`MapVirtualKey(vk, MAPVK_VK_TO_VSC)`, flags extended keys correctly, moves the
pointer with `SetCursorPos` (correct across the whole virtual desktop,
negative coordinates included), and types non-ASCII text through
`KEYEVENTF_UNICODE`, so Korean goes in directly without the clipboard.

All of this is injected into the system input queue, which means it reaches
whichever window currently has focus - MyMacro does not need to be in front.
"""

from __future__ import annotations

import time
from typing import Iterable

from pynput import keyboard, mouse

# Controllers are created on first use, not at import time: building them
# has platform side effects, and nothing that only reads key tables should
# pay for that.
_mouse_controller: mouse.Controller | None = None
_keyboard_controller: keyboard.Controller | None = None


def _mouse_ctl() -> mouse.Controller:
    global _mouse_controller
    if _mouse_controller is None:
        _mouse_controller = mouse.Controller()
    return _mouse_controller


def _keyboard_ctl() -> keyboard.Controller:
    global _keyboard_controller
    if _keyboard_controller is None:
        _keyboard_controller = keyboard.Controller()
    return _keyboard_controller

_BUTTONS = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
}

# Our key names (pyautogui style) -> the attribute name on pynput's Key enum.
_KEY_ALIASES = {
    "enter": "enter",
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "ctrl": "ctrl",
    "ctrlleft": "ctrl_l",
    "ctrlright": "ctrl_r",
    "shift": "shift",
    "shiftleft": "shift_l",
    "shiftright": "shift_r",
    "alt": "alt",
    "altleft": "alt_l",
    "altright": "alt_r",
    "win": "cmd",
    "winleft": "cmd_l",
    "winright": "cmd_r",
    "capslock": "caps_lock",
    "numlock": "num_lock",
    "scrolllock": "scroll_lock",
    "printscreen": "print_screen",
    "apps": "menu",
    "pause": "pause",
    **{f"f{i}": f"f{i}" for i in range(1, 25)},
}

# Windows virtual key codes for keys pynput's enum does not expose.
VK_NUMPAD0 = 0x60


class UnknownKeyError(ValueError):
    pass


def resolve_key(name: str):
    """Turn a key name into something pynput can press."""
    name = (name or "").strip().lower()
    if not name:
        raise UnknownKeyError("키 이름이 비어 있습니다.")

    alias = _KEY_ALIASES.get(name)
    if alias is not None:
        key = getattr(keyboard.Key, alias, None)
        if key is not None:
            return key
        # Some Key members do not exist on every platform or pynput version.
        raise UnknownKeyError(f"이 환경에서 지원하지 않는 키입니다: {name}")

    if name.startswith("num") and name[3:].isdigit():
        digit = int(name[3:])
        if 0 <= digit <= 9:
            return keyboard.KeyCode.from_vk(VK_NUMPAD0 + digit)

    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)

    raise UnknownKeyError(f"알 수 없는 키 이름입니다: {name}")


def resolve_button(name: str) -> mouse.Button:
    return _BUTTONS.get((name or "left").lower(), mouse.Button.left)


# --- mouse ---------------------------------------------------------------

def position() -> tuple[int, int]:
    x, y = _mouse_ctl().position
    return int(x), int(y)


def move_to(x: int | None, y: int | None, duration_ms: float = 0) -> None:
    """Move the pointer. `duration_ms` spreads the move over several steps so
    applications that track pointer movement still see it move."""
    if x is None or y is None:
        return
    x, y = int(x), int(y)
    if duration_ms <= 0:
        _mouse_ctl().position = (x, y)
        return

    start_x, start_y = position()
    steps = max(2, int(duration_ms / 10))
    for i in range(1, steps + 1):
        t = i / steps
        _mouse_ctl().position = (
            int(start_x + (x - start_x) * t),
            int(start_y + (y - start_y) * t),
        )
        time.sleep(duration_ms / 1000.0 / steps)


def click(x=None, y=None, button: str = "left", count: int = 1, duration_ms: float = 0) -> None:
    move_to(x, y, duration_ms)
    _mouse_ctl().click(resolve_button(button), count)


def mouse_down(x=None, y=None, button: str = "left") -> None:
    move_to(x, y)
    _mouse_ctl().press(resolve_button(button))


def mouse_up(x=None, y=None, button: str = "left") -> None:
    move_to(x, y)
    _mouse_ctl().release(resolve_button(button))


def drag(x, y, to_x, to_y, button: str = "left", duration_ms: float = 200) -> None:
    move_to(x, y)
    btn = resolve_button(button)
    _mouse_ctl().press(btn)
    try:
        # A drag that teleports is ignored by many applications, so always
        # move in steps even when no duration was given.
        move_to(to_x, to_y, duration_ms or 200)
    finally:
        _mouse_ctl().release(btn)


def scroll(amount: int, x=None, y=None) -> None:
    move_to(x, y)
    _mouse_ctl().scroll(0, int(amount))


# --- keyboard ------------------------------------------------------------

def press(name: str) -> None:
    key = resolve_key(name)
    _keyboard_ctl().press(key)
    _keyboard_ctl().release(key)


def key_down(name: str) -> None:
    _keyboard_ctl().press(resolve_key(name))


def key_up(name: str) -> None:
    _keyboard_ctl().release(resolve_key(name))


def hotkey(names: Iterable[str]) -> None:
    """Press keys in order, then release them in reverse order."""
    keys = [resolve_key(n) for n in names]
    pressed = []
    try:
        for key in keys:
            _keyboard_ctl().press(key)
            pressed.append(key)
    finally:
        for key in reversed(pressed):
            _keyboard_ctl().release(key)


def type_text(text: str, interval_ms: float = 0) -> None:
    """Type text. pynput routes anything outside the keyboard layout through
    KEYEVENTF_UNICODE, so Korean and most symbols go in directly."""
    if not text:
        return
    if interval_ms <= 0:
        _keyboard_ctl().type(text)
        return
    for char in text:
        _keyboard_ctl().type(char)
        time.sleep(interval_ms / 1000.0)


def type_text_via_clipboard(text: str) -> None:
    """Fallback for characters SendInput cannot express as a single UTF-16
    value, such as emoji above the basic plane."""
    import pyperclip

    saved = ""
    try:
        saved = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    time.sleep(0.05)
    hotkey(["ctrl", "v"])
    time.sleep(0.05)
    if saved:
        try:
            pyperclip.copy(saved)
        except Exception:
            pass
