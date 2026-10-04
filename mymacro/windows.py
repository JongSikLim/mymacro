"""Finding and activating other applications' windows (Windows only).

Why this exists: input is injected into the system input queue, so it lands in
whatever window has focus. Picking the target application therefore means
bringing its window to the front first.

`SetForegroundWindow` is deliberately restricted by Windows - a background
process cannot just steal focus. The usual way around it is to attach our
input queue to the foreground window's thread for the duration of the call,
which is what `activate()` does. It can still fail, so `activate()` checks the
result instead of assuming it worked.

Everything degrades to an empty list or False on other platforms, so the rest
of the app can call it unconditionally.
"""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    pid: int
    process: str  # executable file name, e.g. "notepad.exe"

    def label(self) -> str:
        return f"{self.title}  —  {self.process}" if self.process else self.title


def _process_name(pid: int) -> str:
    """Executable name for a pid, or "" when it cannot be read."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[WindowInfo]:
    """Visible top-level windows that have a title, newest first in Z order."""
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    found: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def collect(handle, _param):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        found.append(
            WindowInfo(
                handle=int(handle),
                title=title,
                pid=int(pid.value),
                process=_process_name(int(pid.value)),
            )
        )
        return True

    user32.EnumWindows(collect, 0)
    return found


def matches(window: WindowInfo, title: str = "", process: str = "") -> bool:
    """Both filters are optional, case-insensitive, and must all hold."""
    if title and title.lower() not in window.title.lower():
        return False
    if process and process.lower() not in window.process.lower():
        return False
    return bool(title or process)


def find(title: str = "", process: str = "") -> WindowInfo | None:
    """First window matching the filters, or None."""
    for window in list_windows():
        if matches(window, title, process):
            return window
    return None


def foreground_handle() -> int:
    if not IS_WINDOWS:
        return 0
    return int(ctypes.windll.user32.GetForegroundWindow())


def activate(handle: int) -> bool:
    """Bring a window to the front and give it keyboard focus.

    Returns whether the window really ended up in the foreground.
    """
    if not IS_WINDOWS or not handle:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)

    current = user32.GetForegroundWindow()
    if current == handle:
        return True

    # Windows only grants focus changes to the thread that owns the current
    # foreground window, so borrow its input queue for the call.
    our_thread = kernel32.GetCurrentThreadId()
    their_thread = user32.GetWindowThreadProcessId(current, None) if current else 0

    attached = False
    if their_thread and their_thread != our_thread:
        attached = bool(user32.AttachThreadInput(our_thread, their_thread, True))
    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, their_thread, False)

    # Focus changes are not instant; give the shell a moment before judging.
    for _ in range(20):
        if user32.GetForegroundWindow() == handle:
            return True
        time.sleep(0.02)
    return False


def rect(handle: int) -> list[int] | None:
    """[left, top, width, height] of a window, in screen coordinates."""
    if not IS_WINDOWS or not handle:
        return None

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    box = RECT()
    if not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(box)):
        return None
    return [box.left, box.top, box.right - box.left, box.bottom - box.top]
