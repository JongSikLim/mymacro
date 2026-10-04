"""Global hotkeys that work while another application has focus.

Callbacks run on a pynput listener thread. The UI must not touch widgets from
there - it forwards through a Qt signal instead.
"""

from __future__ import annotations

from typing import Callable

from pynput import keyboard


class GlobalHotkeys:
    def __init__(self, bindings: dict[str, Callable[[], None]]):
        """`bindings` maps a pynput hotkey string to a callback.

        Example: {"<f9>": start, "<f10>": stop}
        """
        self._bindings = bindings
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.GlobalHotKeys(self._bindings)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class SingleKeyCapture:
    """Wait for one key press, then stop listening.

    Used by the coordinate picker: the user parks the mouse over the target and
    presses the trigger key, so nothing has to cover the screen.
    """

    def __init__(self, trigger: str, on_trigger: Callable[[], None], on_cancel: Callable[[], None] | None = None):
        self.trigger = trigger.lower()
        self.on_trigger = on_trigger
        self.on_cancel = on_cancel
        self._listener: keyboard.Listener | None = None

    def start(self) -> None:
        from . import keymap

        def handle(key):
            name = keymap.from_pynput(key)
            if name is None:
                return None
            if name == self.trigger:
                self.on_trigger()
                return False  # stops the listener
            if name == "esc" and self.on_cancel is not None:
                self.on_cancel()
                return False
            return None

        self._listener = keyboard.Listener(on_press=handle)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
