"""Process-wide setup that must run before Qt or any screen capture starts.

The whole tool depends on one invariant: the coordinates reported by pynput,
the coordinates consumed by SendInput/SetCursorPos, the pixels grabbed by mss
and the geometry used by Qt must all be the same physical screen pixels.

On Windows that only holds when the process is per-monitor DPI aware AND Qt's
own high-DPI scaling is turned off. Calling this first makes every later
coordinate comparable.
"""

from __future__ import annotations

import os
import sys


def enable_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware (Windows only, no-op elsewhere)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # 2 == PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def disable_qt_scaling() -> None:
    """Keep Qt logical pixels equal to physical pixels.

    Must run before QApplication is constructed. The UI looks slightly smaller
    on a high-DPI display; the app compensates with a larger base font.
    """
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")


def bootstrap() -> None:
    enable_dpi_awareness()
    disable_qt_scaling()
