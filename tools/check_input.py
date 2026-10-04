"""Prove that MyMacro's input reaches another application that has focus.

Run it on Windows with an empty Notepad open:

    check_input.bat

The interesting test is PHASE 3. It types a unique marker into whatever window
is focused, then sends Ctrl+A and Ctrl+C and reads the clipboard back. If the
marker comes back, the keystrokes provably reached a foreign, focused window -
not this console. PHASE 4 does the same for the mouse: it double-clicks inside
that window and checks that a single word was selected.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mymacro.platform_utils import bootstrap

bootstrap()

import ctypes  # noqa: E402

from mymacro import diagnostics as diag  # noqa: E402
from mymacro import input_backend as ib  # noqa: E402

MARKER = "MYMACROWORD"
KOREAN = "한글입력테스트"
RESULTS: list[tuple[str, bool | None, str]] = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""))


def heading(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


# --------------------------------------------------------------- phase 0
def phase_environment() -> bool:
    heading("PHASE 0. 환경 확인")
    for line in diag.report_lines():
        print(f"  {line}")
    if not diag.IS_WINDOWS:
        record("Windows 환경", False, "이 검사는 Windows에서만 의미가 있습니다")
        return False
    record("Windows 환경", True)
    record(
        "DPI 인식",
        "PER_MONITOR" in diag.dpi_awareness(),
        diag.dpi_awareness(),
    )
    return True


# --------------------------------------------------------------- phase 1
def phase_cursor_roundtrip() -> None:
    """SetCursorPos must land exactly, including on a second monitor."""
    heading("PHASE 1. 마우스 좌표 정확도 (다중 모니터 포함)")
    vd = diag.virtual_desktop()
    saved = diag.cursor_pos()

    targets = [
        (vd["left"] + 50, vd["top"] + 50),
        (vd["left"] + vd["width"] // 2, vd["top"] + vd["height"] // 2),
        (vd["left"] + vd["width"] - 60, vd["top"] + vd["height"] - 60),
    ]
    if vd["monitors"] > 1:
        # A point near the far edge only exists when a second monitor is there.
        targets.append((vd["left"] + vd["width"] - 10, vd["top"] + 10))

    worst = 0
    for target in targets:
        ib.move_to(*target)
        time.sleep(0.05)
        actual = diag.cursor_pos()
        delta = max(abs(actual[0] - target[0]), abs(actual[1] - target[1]))
        worst = max(worst, delta)
        print(f"    요청 {target} -> 실제 {actual} (오차 {delta}px)")

    if saved:
        ib.move_to(*saved)
    record(
        f"좌표 정확도 (모니터 {vd['monitors']}대)",
        worst <= 1,
        f"최대 오차 {worst}px",
    )


# --------------------------------------------------------------- phase 2
def phase_injection_visible() -> None:
    """Confirm the events really enter the system input queue.

    The low-level hooks (WH_KEYBOARD_LL / WH_MOUSE_LL) see every event in the
    queue regardless of which window has focus, so observing our own injected
    events there proves injection works at the OS level.
    """
    heading("PHASE 2. 주입한 이벤트가 시스템 입력 큐에 들어가는지")
    from pynput import keyboard, mouse

    seen_keys: list[str] = []
    seen_clicks: list[str] = []

    key_listener = keyboard.Listener(on_press=lambda k: seen_keys.append(str(k)))
    mouse_listener = mouse.Listener(
        on_click=lambda x, y, b, p: seen_clicks.append(f"{b}-{'down' if p else 'up'}")
    )
    key_listener.start()
    mouse_listener.start()
    time.sleep(0.4)

    try:
        for name in ("f13", "home", "end", "left", "right"):
            try:
                ib.press(name)
            except ib.UnknownKeyError:
                pass
            time.sleep(0.05)

        saved = diag.cursor_pos()
        ib.click(saved[0], saved[1], "left")
        time.sleep(0.2)
    finally:
        key_listener.stop()
        mouse_listener.stop()

    record("키 이벤트 주입", len(seen_keys) > 0, f"{len(seen_keys)}건 관측: {seen_keys[:5]}")
    record("마우스 이벤트 주입", len(seen_clicks) > 0, f"{len(seen_clicks)}건 관측: {seen_clicks}")


# --------------------------------------------------------------- phase 3
def phase_foreign_keyboard() -> str | None:
    """The real test: type into a different, focused application."""
    heading("PHASE 3. 포커스된 다른 프로그램에 키보드 입력")
    print("  빈 메모장(Notepad)을 열고 본문을 클릭해 포커스를 주세요.")
    for remaining in range(6, 0, -1):
        print(f"    {remaining}초...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 30, end="\r")

    front = diag.foreground_window()
    if front is None:
        record("포커스 창 확인", False, "포그라운드 창을 읽지 못했습니다")
        return None
    print(f"    포커스된 창: {front.title!r} (pid {front.pid})")

    if front.pid == os.getpid():
        record(
            "다른 프로그램이 포커스됨",
            False,
            "이 콘솔이 포커스되어 있습니다. 메모장을 클릭한 뒤 다시 실행하세요",
        )
        return None
    record("다른 프로그램이 포커스됨", True, front.title or "(제목 없음)")

    import pyperclip

    sentinel = "__mymacro_sentinel__"
    pyperclip.copy(sentinel)

    # Many identical words, so a later double-click anywhere lands on one.
    body = "\n".join([MARKER] * 40)
    ib.type_text(body)
    time.sleep(0.2)
    ib.press("enter")
    ib.type_text(KOREAN)
    time.sleep(0.4)

    ib.hotkey(["ctrl", "a"])
    time.sleep(0.15)
    ib.hotkey(["ctrl", "c"])
    time.sleep(0.4)

    try:
        clip = pyperclip.paste()
    except Exception as exc:
        record("클립보드 읽기", False, str(exc))
        return front.title

    if clip == sentinel:
        record(
            "키 입력이 다른 프로그램에 전달됨",
            False,
            "클립보드가 그대로입니다. 대상이 관리자 권한으로 실행 중일 수 있습니다 (UIPI)",
        )
        return front.title

    record("ASCII 입력 전달", MARKER in clip, f"클립보드 {len(clip)}자")
    record(
        "한글(유니코드) 입력 전달",
        KOREAN in clip,
        "KEYEVENTF_UNICODE 경로" if KOREAN in clip else "한글이 들어가지 않았습니다",
    )
    record(
        "단축키 Ctrl+A / Ctrl+C 전달",
        clip != sentinel,
        "클립보드가 바뀌었으므로 조합키가 처리되었습니다",
    )
    return front.title


# --------------------------------------------------------------- phase 4
def phase_foreign_mouse() -> None:
    """Double-click inside the focused window and check what got selected."""
    heading("PHASE 4. 포커스된 다른 프로그램에 마우스 입력")
    front = diag.foreground_window()
    if front is None or front.pid == os.getpid():
        record("마우스 입력 전달", None, "앞 단계에서 대상 창을 확보하지 못했습니다")
        return

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    if not ctypes.windll.user32.GetWindowRect(front.handle, ctypes.byref(rect)):
        record("마우스 입력 전달", None, "창 영역을 읽지 못했습니다")
        return

    # Aim well inside the text area, below the title bar and menu.
    x = (rect.left + rect.right) // 2
    y = rect.top + int((rect.bottom - rect.top) * 0.45)
    print(f"    창 영역 ({rect.left}, {rect.top})-({rect.right}, {rect.bottom}), 더블클릭 ({x}, {y})")

    import pyperclip

    sentinel = "__mymacro_mouse_sentinel__"
    pyperclip.copy(sentinel)

    ib.click(x, y, "left", count=2)   # double-click selects one word
    time.sleep(0.3)
    ib.hotkey(["ctrl", "c"])
    time.sleep(0.4)

    clip = pyperclip.paste()
    if clip == sentinel:
        record("마우스 클릭이 다른 프로그램에 전달됨", False, "선택이 일어나지 않았습니다")
        return
    selected = clip.strip()
    record(
        "마우스 더블클릭 전달",
        selected == MARKER,
        f"선택된 내용 {selected!r}" + ("" if selected == MARKER else f" (기대값 {MARKER!r})"),
    )


def cleanup() -> None:
    """Leave the target window empty so it can be closed without a save prompt."""
    heading("정리")
    try:
        ib.hotkey(["ctrl", "a"])
        time.sleep(0.1)
        ib.press("delete")
        print("  대상 창의 내용을 지웠습니다.")
    except Exception as exc:
        print(f"  정리 실패(무시해도 됩니다): {exc}")


def summary() -> int:
    heading("결과 요약")
    failed = [name for name, ok, _ in RESULTS if ok is False]
    for name, ok, detail in RESULTS:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
        print(f"  {mark:4}  {name}" + (f"  ({detail})" if detail else ""))
    print()
    if not failed:
        print("모두 통과. 다른 프로그램이 포커스된 상태에서 키/마우스 입력이 전달됩니다.")
        return 0
    print("실패 항목:")
    for name in failed:
        print(f"  - {name}")
    print()
    print("가장 흔한 원인:")
    print("  1. 대상 프로그램이 관리자 권한으로 실행 중 -> MyMacro도 관리자 권한으로 실행")
    print("  2. 대상이 DirectInput 기반 게임 -> SendInput을 무시하는 경우가 있음")
    print("  3. 백신/보안 프로그램이 입력 주입을 차단")
    return 1


def main() -> int:
    print("MyMacro 입력 전달 검사")
    print("=" * 50)
    print("주의: 이 검사는 포커스된 창에 실제로 글자를 입력합니다.")
    print("      반드시 빈 메모장처럼 지워도 되는 창을 사용하세요.")

    if not phase_environment():
        return summary()

    phase_cursor_roundtrip()
    phase_injection_visible()
    title = phase_foreign_keyboard()
    if title is not None:
        phase_foreign_mouse()
        cleanup()
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
