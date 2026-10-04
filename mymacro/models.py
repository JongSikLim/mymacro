"""Macro data model and JSON (de)serialization.

A macro is an ordered list of steps. Seven step types exist:

  mouse     - move / click / drag / scroll at a remembered coordinate
  key       - press a key, press a hotkey combo, or type text
  delay     - wait a fixed time, optionally with random jitter
  if_image  - look for a template image, then run one of two nested lists
  loop      - repeat the steps inside it, by count or by an image condition
  label     - a named position at the top level, used as a jump target
  jump      - break / continue / restart / stop / goto a label

Steps nest: a loop can hold an if_image that holds another loop. The editor
caps the depth so the tree stays readable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MOUSE = "mouse"
KEY = "key"
DELAY = "delay"
IF_IMAGE = "if_image"
LOOP = "loop"
LABEL = "label"
JUMP = "jump"
RANDOM = "random"
OPTION = "option"
WINDOW = "window"

STEP_TYPES = (MOUSE, KEY, DELAY, IF_IMAGE, LOOP, LABEL, JUMP, RANDOM, OPTION, WINDOW)

# Types the user adds directly. OPTION only ever exists inside a RANDOM block,
# so it has no button and no editor of its own.
USER_STEP_TYPES = (MOUSE, KEY, DELAY, WINDOW, IF_IMAGE, LOOP, RANDOM, LABEL, JUMP)

CONTAINER_TYPES = (IF_IMAGE, LOOP, RANDOM)
CHILD_TYPES = (LOOP, RANDOM, OPTION)

MOUSE_ACTIONS = (
    "move", "click", "double_click", "right_click", "middle_click",
    "drag", "scroll", "mouse_down", "mouse_up",
)
KEY_ACTIONS = ("press", "hotkey", "type", "key_down", "key_up")
LOOP_MODES = ("count", "forever", "while_image", "until_image")
JUMP_ACTIONS = ("break", "continue", "restart", "stop", "goto")

THEN_LABEL = "└ 찾았을 때"
ELSE_LABEL = "└ 못 찾았을 때"
BODY_LABEL = "└ 반복할 내용"
OPTION_LABEL = "└ 이 선택지"


@dataclass
class Step:
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    then_steps: list["Step"] = field(default_factory=list)
    else_steps: list["Step"] = field(default_factory=list)
    children: list["Step"] = field(default_factory=list)
    enabled: bool = True

    # --- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type,
            "params": self.params,
            "enabled": self.enabled,
        }
        if self.type == IF_IMAGE:
            data["then_steps"] = [s.to_dict() for s in self.then_steps]
            data["else_steps"] = [s.to_dict() for s in self.else_steps]
        elif self.type in CHILD_TYPES:
            data["children"] = [s.to_dict() for s in self.children]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        step_type = data.get("type", "")
        if step_type not in STEP_TYPES:
            raise ValueError(f"unknown step type: {step_type!r}")
        return cls(
            type=step_type,
            params=dict(data.get("params") or {}),
            then_steps=[cls.from_dict(d) for d in data.get("then_steps") or []],
            else_steps=[cls.from_dict(d) for d in data.get("else_steps") or []],
            children=[cls.from_dict(d) for d in data.get("children") or []],
            enabled=bool(data.get("enabled", True)),
        )

    def copy(self) -> "Step":
        return Step.from_dict(json.loads(json.dumps(self.to_dict())))

    def branches(self) -> list[tuple[str, list["Step"]]]:
        """The nested step lists this step owns, with the label to show."""
        if self.type == IF_IMAGE:
            return [(THEN_LABEL, self.then_steps), (ELSE_LABEL, self.else_steps)]
        if self.type == LOOP:
            return [(BODY_LABEL, self.children)]
        if self.type == RANDOM:
            return [
                (f"└ 선택지 {index + 1}", option.children)
                for index, option in enumerate(self.children)
            ]
        if self.type == OPTION:
            return [(OPTION_LABEL, self.children)]
        return []

    # --- display -------------------------------------------------------
    def describe(self) -> str:
        p = self.params
        if self.type == MOUSE:
            return _describe_mouse(p)
        if self.type == KEY:
            return _describe_key(p)
        if self.type == DELAY:
            jitter = int(p.get("jitter_ms") or 0)
            base = f"{int(p.get('ms', 0))}ms 대기"
            return f"{base} (±{jitter}ms)" if jitter else base
        if self.type == IF_IMAGE:
            name = Path(str(p.get("image", ""))).name or "(이미지 없음)"
            conf = float(p.get("confidence", 0.85))
            notes = [f"정확도 {conf:.2f}"]
            timeout = int(p.get("timeout_ms", 0))
            if timeout:
                notes.append(f"최대 {timeout}ms 대기")
            if p.get("click_on_match"):
                jitter = int(p.get("click_jitter_px", 0) or 0)
                notes.append(f"찾으면 클릭 (±{jitter}px)" if jitter else "찾으면 클릭")
            if p.get("key_on_match"):
                notes.append(f"찾으면 {p['key_on_match']} 입력")
            return f"이미지 조건: {name} ({', '.join(notes)})"
        if self.type == LOOP:
            return _describe_loop(p)
        if self.type == LABEL:
            return f"라벨: {p.get('name', '')}"
        if self.type == JUMP:
            return _describe_jump(p)
        if self.type == RANDOM:
            return f"랜덤 선택 ({len(self.children)}개 중 하나)"
        if self.type == OPTION:
            return "선택지"
        if self.type == WINDOW:
            return _describe_window(p)
        return self.type


def _describe_mouse(p: dict[str, Any]) -> str:
    action = str(p.get("action", "click"))
    x, y = p.get("x"), p.get("y")
    jitter = int(p.get("jitter_px", 0) or 0)
    spread = f" ±{jitter}px" if jitter and x is not None else ""
    pos = f"({x}, {y}){spread}" if x is not None and y is not None else "(현재 위치)"
    labels = {
        "move": "이동", "click": "좌클릭", "double_click": "더블클릭",
        "right_click": "우클릭", "middle_click": "휠클릭",
        "mouse_down": "버튼 누름", "mouse_up": "버튼 뗌",
    }
    if action == "drag":
        return f"드래그 {pos} -> ({p.get('to_x')}, {p.get('to_y')})"
    if action == "scroll":
        amount = int(p.get("scroll_amount", 0))
        direction = "위로" if amount > 0 else "아래로"
        return f"스크롤 {direction} {abs(amount)}칸 {pos}"
    return f"{labels.get(action, action)} {pos}"


def _describe_key(p: dict[str, Any]) -> str:
    action = str(p.get("action", "press"))
    if action == "hotkey":
        keys = p.get("keys") or []
        return f"단축키 {'+'.join(str(k) for k in keys)}"
    if action == "type":
        text = str(p.get("text", ""))
        shown = text if len(text) <= 30 else text[:30] + "..."
        return f"텍스트 입력 {shown!r}"
    labels = {"press": "키 입력", "key_down": "키 누름", "key_up": "키 뗌"}
    return f"{labels.get(action, action)} {p.get('key', '')}"


def _describe_loop(p: dict[str, Any]) -> str:
    mode = str(p.get("mode", "count"))
    if mode == "count":
        return f"반복 {int(p.get('count', 1))}회"
    if mode == "forever":
        return "반복 (무한, 탈출 단계로 빠져나감)"
    name = Path(str(p.get("image", ""))).name or "(이미지 없음)"
    if mode == "while_image":
        return f"반복: {name} 이(가) 보이는 동안"
    return f"반복: {name} 이(가) 나타날 때까지"


def _describe_jump(p: dict[str, Any]) -> str:
    action = str(p.get("action", "break"))
    if action == "goto":
        return f"라벨 '{p.get('target', '')}' 로 이동"
    return {
        "break": "루프 탈출",
        "continue": "다음 반복으로 건너뛰기",
        "restart": "매크로 처음으로 돌아가기",
        "stop": "매크로 종료",
    }.get(action, action)


def _describe_window(p: dict[str, Any]) -> str:
    target = p.get("title") or p.get("process") or "(지정 안 됨)"
    action = str(p.get("action", "activate"))
    if action == "wait":
        return f"창이 나타날 때까지 대기: {target}"
    return f"창 활성화: {target}"


@dataclass
class Macro:
    name: str = "새 매크로"
    steps: list[Step] = field(default_factory=list)
    repeat: int = 1  # 0 means repeat until stopped
    repeat_delay_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "name": self.name,
            "repeat": self.repeat,
            "repeat_delay_ms": self.repeat_delay_ms,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Macro":
        return cls(
            name=str(data.get("name", "새 매크로")),
            steps=[Step.from_dict(d) for d in data.get("steps") or []],
            repeat=int(data.get("repeat", 1)),
            repeat_delay_ms=int(data.get("repeat_delay_ms", 0)),
        )

    def labels(self) -> list[str]:
        """Label names available as jump targets (top level only)."""
        return [
            str(s.params.get("name", ""))
            for s in self.steps
            if s.type == LABEL and str(s.params.get("name", ""))
        ]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "Macro":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --- convenient constructors ------------------------------------------

def mouse_step(action: str, **params: Any) -> Step:
    return Step(type=MOUSE, params={"action": action, **params})


def key_step(action: str, **params: Any) -> Step:
    return Step(type=KEY, params={"action": action, **params})


def delay_step(ms: int, jitter_ms: int = 0) -> Step:
    return Step(type=DELAY, params={"ms": int(ms), "jitter_ms": int(jitter_ms)})


def if_image_step(image: str, **params: Any) -> Step:
    base = {
        "image": image,
        "confidence": 0.85,
        "region": None,          # [left, top, width, height], None = whole screen
        "timeout_ms": 0,         # 0 = check once, >0 = keep looking until timeout
        "grayscale": True,
        "click_on_match": False,
        "match_offset": [0, 0],
        # Scatter the click a little instead of hitting the same pixel every
        # time. 0 means the exact point.
        "click_jitter_px": 0,
        # A key sent after the optional click. "enter", "ctrl+v", "" for none.
        # The 찾았을 때 branch still handles anything longer than one key.
        "key_on_match": "",
        # Look near the last hit first. Falls back to a full search on a miss,
        # so this only changes speed, never the outcome.
        "use_cache": True,
    }
    base.update(params)
    return Step(type=IF_IMAGE, params=base)


def loop_step(mode: str = "count", **params: Any) -> Step:
    base = {
        "mode": mode,
        "count": 3,
        "image": "",
        "confidence": 0.85,
        "region": None,
        "grayscale": True,
        # Safety net for the modes that have no fixed count, so a mistake
        # cannot spin forever unnoticed.
        "max_iterations": 1000,
        "use_cache": True,
    }
    base.update(params)
    return Step(type=LOOP, params=base)


def label_step(name: str) -> Step:
    return Step(type=LABEL, params={"name": name})


def jump_step(action: str, target: str = "") -> Step:
    return Step(type=JUMP, params={"action": action, "target": target})


def option_step() -> Step:
    return Step(type=OPTION)


def random_step(count: int = 2) -> Step:
    """A block that runs exactly one of its options, picked at random."""
    count = max(2, count)
    step = Step(type=RANDOM, params={"count": count})
    step.children = [option_step() for _ in range(count)]
    return step


def sync_random_options(step: Step) -> None:
    """Make the option list match the count the editor asked for."""
    wanted = max(2, int(step.params.get("count", len(step.children)) or 2))
    while len(step.children) < wanted:
        step.children.append(option_step())
    del step.children[wanted:]
    step.params["count"] = wanted


def window_step(action: str = "activate", **params: Any) -> Step:
    base = {
        "action": action,     # activate | wait
        "title": "",          # substring of the window title
        "process": "",        # substring of the executable name
        "timeout_ms": 5000,
    }
    base.update(params)
    return Step(type=WINDOW, params=base)
