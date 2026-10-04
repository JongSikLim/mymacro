"""Start MyMacro.

    python run.py

The DPI bootstrap must happen before Qt, pynput or mss load, otherwise the
coordinates they report will not agree with each other on a scaled display.
"""

from mymacro.platform_utils import bootstrap

bootstrap()

from mymacro.app import main  # noqa: E402  (import after bootstrap, on purpose)

if __name__ == "__main__":
    raise SystemExit(main())
