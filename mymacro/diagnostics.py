"""Windows facts that decide whether injected input actually lands.

Three things silently stop a macro from reaching another application:

* The target runs elevated and MyMacro does not. Windows UIPI then drops
  every injected event without an error. This is the most common cause.
* The process is not DPI aware, so the coordinates it computes do not match
  the coordinates the screen uses.
* The click is on a second monitor and the backend normalised it against the
  primary one. `input_backend` avoids this by using SetCursorPos.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

# GetSystemMetrics indices for the whole virtual desktop.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CMONITORS = 80

_AWARENESS_NAMES = {
    0: "DPI_UNAWARE (좌표가 어긋납니다)",
    1: "SYSTEM_DPI_AWARE",
    2: "PER_MONITOR_DPI_AWARE (정상)",
}


@dataclass
class ForegroundWindow:
    handle: int
    title: str
    pid: int


def is_elevated() -> bool | None:
    """True when running as administrator. None when it cannot be determined."""
    if not IS_WINDOWS:
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def dpi_awareness() -> str:
    if not IS_WINDOWS:
        return "N/A"
    try:
        value = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(value))
        return _AWARENESS_NAMES.get(value.value, f"알 수 없음({value.value})")
    except Exception as exc:
        return f"확인 실패: {exc}"


def virtual_desktop() -> dict[str, int]:
    if not IS_WINDOWS:
        return {}
    get = ctypes.windll.user32.GetSystemMetrics
    return {
        "left": get(SM_XVIRTUALSCREEN),
        "top": get(SM_YVIRTUALSCREEN),
        "width": get(SM_CXVIRTUALSCREEN),
        "height": get(SM_CYVIRTUALSCREEN),
        "monitors": get(SM_CMONITORS),
    }


def foreground_window() -> ForegroundWindow | None:
    """Which window currently has keyboard focus."""
    if not IS_WINDOWS:
        return None
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return ForegroundWindow(handle=int(handle), title=buffer.value, pid=int(pid.value))


def cursor_pos() -> tuple[int, int] | None:
    if not IS_WINDOWS:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return int(point.x), int(point.y)
    return None


def report_lines() -> list[str]:
    lines = [f"플랫폼: {sys.platform}"]
    if not IS_WINDOWS:
        lines.append("Windows가 아니므로 입력 진단을 할 수 없습니다.")
        return lines
    elevated = is_elevated()
    lines.append(f"관리자 권한: {'예' if elevated else '아니오' if elevated is False else '확인 불가'}")
    lines.append(f"DPI 인식: {dpi_awareness()}")
    vd = virtual_desktop()
    lines.append(
        f"가상 데스크톱: ({vd['left']}, {vd['top']}) {vd['width']}x{vd['height']}, "
        f"모니터 {vd['monitors']}대"
    )
    if elevated is False:
        lines.append(
            "주의: 관리자 권한으로 실행된 프로그램에는 입력이 전달되지 않습니다 (Windows UIPI). "
            "그런 프로그램을 조작하려면 MyMacro도 관리자 권한으로 실행하세요."
        )
    return lines
