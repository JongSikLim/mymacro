"""Where the app keeps macros and template images.

Anchored to the executable (or the project root in development) rather than
the current working directory, so a double-clicked .exe always finds the same
`macros/` folder no matter where it was started from.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


MACRO_DIR = app_dir() / "macros"
IMAGE_DIR = MACRO_DIR / "images"


def ensure_dirs() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
