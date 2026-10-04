"""Editors for every step type."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import keymap, models, vision
from .. import windows as win
from ..models import Macro, Step
from ..paths import IMAGE_DIR
from .pickers import CoordPicker, RegionPicker, invisible_while

MAX_COORD = 100000
MIN_COORD = -100000


class TemplatePicker(QWidget):
    """Choose a template image plus how to search for it.

    Shared by the image condition step and the image-driven loop, so both
    behave identically: capture from a frozen screen, limit the search region,
    and test the match before running anything.
    """

    def __init__(self, parent: QWidget | None = None, images_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.images_dir = Path(images_dir or IMAGE_DIR)
        self._region: list[int] | None = None
        self._picker: RegionPicker | None = None

        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form = layout

        self.image_path = QLineEdit()
        capture = QPushButton("화면에서 캡처")
        capture.clicked.connect(self._capture_template)
        browse = QPushButton("파일 열기")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.image_path, 1)
        row.addWidget(capture)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        layout.addRow("찾을 이미지", holder)

        self.preview = QLabel("미리보기 없음")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(90)
        self.preview.setStyleSheet("border: 1px solid #888;")
        layout.addRow("", self.preview)

        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(0.30, 1.00)
        self.confidence.setSingleStep(0.01)
        self.confidence.setDecimals(2)
        self.confidence.setValue(0.85)
        self.confidence.setToolTip("낮출수록 느슨하게 찾습니다. 0.80~0.90이 보통 잘 맞습니다.")
        layout.addRow("정확도", self.confidence)

        self.region_label = QLabel("전체 화면")
        set_region = QPushButton("검색 영역 지정")
        set_region.clicked.connect(self._pick_region)
        clear_region = QPushButton("전체 화면")
        clear_region.clicked.connect(self._clear_region)
        region_row = QHBoxLayout()
        region_row.addWidget(self.region_label, 1)
        region_row.addWidget(set_region)
        region_row.addWidget(clear_region)
        region_holder = QWidget()
        region_holder.setLayout(region_row)
        layout.addRow("검색 범위", region_holder)

        self.grayscale = QCheckBox("흑백으로 비교 (더 빠름)")
        self.grayscale.setChecked(True)
        layout.addRow("", self.grayscale)

        self.use_cache = QCheckBox("마지막으로 찾은 자리를 먼저 확인 (반복 시 훨씬 빠름)")
        self.use_cache.setChecked(True)
        self.use_cache.setToolTip(
            "직전에 찾은 위치 주변을 먼저 봅니다. 거기 없으면 평소처럼 전체를 다시 찾으므로\n"
            "결과는 달라지지 않고 속도만 빨라집니다."
        )
        layout.addRow("", self.use_cache)

        test = QPushButton("지금 화면에서 찾아보기")
        test.clicked.connect(self._test_match)
        layout.addRow("", test)

    # --- values ----------------------------------------------------------
    def set_params(self, p: dict[str, Any]) -> None:
        self.image_path.setText(str(p.get("image", "")))
        self.confidence.setValue(float(p.get("confidence", 0.85)))
        self._region = list(p["region"]) if p.get("region") else None
        self.region_label.setText(self._region_text())
        self.grayscale.setChecked(bool(p.get("grayscale", True)))
        self.use_cache.setChecked(bool(p.get("use_cache", True)))
        self._refresh_preview()

    def to_params(self) -> dict[str, Any]:
        return {
            "image": self.image_path.text().strip(),
            "confidence": round(self.confidence.value(), 2),
            "region": self._region,
            "grayscale": self.grayscale.isChecked(),
            "use_cache": self.use_cache.isChecked(),
        }

    # --- helpers ----------------------------------------------------------
    def _region_text(self) -> str:
        if not self._region:
            return "전체 화면"
        left, top, width, height = self._region
        return f"({left}, {top}) {width} x {height}"

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "이미지 선택", str(self.images_dir), "이미지 (*.png *.jpg *.bmp)"
        )
        if path:
            self.image_path.setText(path)
            self._refresh_preview()

    def _capture_template(self) -> None:
        self._run_region_picker(self._on_template_captured)

    def _pick_region(self) -> None:
        self._run_region_picker(self._on_region_picked)

    def _clear_region(self) -> None:
        self._region = None
        self.region_label.setText(self._region_text())

    def _run_region_picker(self, handler) -> None:
        """Take our windows out of the shot, then show the frozen overlay.

        The windows are made transparent rather than hidden. Hiding a dialog
        cancels the `exec()` it is running, which used to throw the whole edit
        away the moment the capture button was pressed.
        """
        dialog = self.window()
        windows = [dialog]
        if dialog.parent() is not None:
            windows.append(dialog.parent().window())

        with invisible_while(windows):
            region = RegionPicker().pick()

        dialog.raise_()
        dialog.activateWindow()
        handler(region)

    def _on_template_captured(self, region) -> None:
        if not region:
            return
        self.images_dir.mkdir(parents=True, exist_ok=True)
        path = self.images_dir / f"tpl_{time.strftime('%Y%m%d_%H%M%S')}.png"
        vision.save_region_png(region, path)
        self.image_path.setText(str(path))
        self._refresh_preview()

    def _on_region_picked(self, region) -> None:
        if not region:
            return
        self._region = [int(v) for v in region]
        self.region_label.setText(self._region_text())

    def _refresh_preview(self) -> None:
        path = self.image_path.text().strip()
        if not path or not Path(path).exists():
            self.preview.setText("미리보기 없음")
            self.preview.setPixmap(QPixmap())
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview.setText("이미지를 읽을 수 없습니다")
            return
        self.preview.setPixmap(
            pixmap.scaled(
                480, 120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _test_match(self) -> None:
        path = self.image_path.text().strip()
        if not path:
            QMessageBox.warning(self, "확인", "먼저 찾을 이미지를 지정하세요.")
            return
        try:
            match = vision.find(
                path,
                confidence=self.confidence.value(),
                region=self._region,
                grayscale=self.grayscale.isChecked(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "오류", str(exc))
            return
        if match is None:
            QMessageBox.information(
                self, "결과", "지금 화면에서는 찾지 못했습니다.\n정확도를 낮춰보세요."
            )
        else:
            QMessageBox.information(
                self, "결과",
                f"찾았습니다.\n위치 ({match.x}, {match.y})\n일치도 {match.score:.3f}",
            )


def _click_jitter_field(initial: int = 0) -> QSpinBox:
    """How far a click may wander from the exact point."""
    spin = QSpinBox()
    spin.setRange(0, 500)
    spin.setSuffix(" px")
    spin.setValue(initial)
    spin.setToolTip(
        "매번 같은 픽셀을 누르지 않고 이 범위 안에서 조금씩 다른 곳을 누릅니다.\n"
        "0이면 항상 정확히 같은 지점입니다.\n"
        "찾은 이미지 안을 겨냥한 경우에는 이미지 밖으로 나가지 않습니다."
    )
    return spin


def _key_on_match_field(initial: str = "") -> QComboBox:
    """Editable dropdown: pick a common key or type a combo like ctrl+v."""
    box = QComboBox()
    box.setEditable(True)
    box.addItem("")  # none
    box.addItems(keymap.COMMON_KEYS)
    box.setCurrentText(initial)
    box.setToolTip(
        "이미지를 찾으면 이 키를 누릅니다. 비워두면 아무 키도 누르지 않습니다.\n"
        "조합키는 ctrl+v 처럼 + 로 이어 쓰세요.\n"
        "여러 단계가 필요하면 '찾았을 때' 가지에 단계를 추가하세요."
    )
    return box


class _BaseStepDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._outer = QVBoxLayout(self)
        self.form = QFormLayout()
        self._outer.addLayout(self.form)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._outer.addWidget(self._buttons)

    def add_widget(self, widget: QWidget) -> None:
        self._outer.insertWidget(self._outer.count() - 1, widget)

    def to_params(self) -> dict[str, Any]:
        raise NotImplementedError


class MouseStepDialog(_BaseStepDialog):
    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "마우스 단계")
        p = dict(step.params) if step else {}

        self.action = QComboBox()
        labels = {
            "click": "좌클릭", "double_click": "더블클릭", "right_click": "우클릭",
            "middle_click": "휠클릭", "move": "이동만", "drag": "드래그",
            "scroll": "스크롤", "mouse_down": "버튼 누르고 있기", "mouse_up": "버튼 떼기",
        }
        for key in models.MOUSE_ACTIONS:
            self.action.addItem(labels.get(key, key), key)
        self.action.setCurrentIndex(max(0, self.action.findData(p.get("action", "click"))))
        self.form.addRow("동작", self.action)

        self.x = self._coord_spin(p.get("x"))
        self.y = self._coord_spin(p.get("y"))
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("X"))
        pick_row.addWidget(self.x)
        pick_row.addWidget(QLabel("Y"))
        pick_row.addWidget(self.y)
        pick_button = QPushButton("화면에서 찍기 (F2)")
        pick_button.clicked.connect(lambda: self._pick_into(self.x, self.y))
        pick_row.addWidget(pick_button)
        holder = QWidget()
        holder.setLayout(pick_row)
        self.form.addRow("좌표", holder)

        self.use_current = QCheckBox("좌표를 쓰지 않고 현재 마우스 위치에서 실행")
        self.use_current.setChecked(p.get("x") is None)
        self.form.addRow("", self.use_current)

        self.to_x = self._coord_spin(p.get("to_x"))
        self.to_y = self._coord_spin(p.get("to_y"))
        drag_row = QHBoxLayout()
        drag_row.addWidget(QLabel("X"))
        drag_row.addWidget(self.to_x)
        drag_row.addWidget(QLabel("Y"))
        drag_row.addWidget(self.to_y)
        drag_pick = QPushButton("화면에서 찍기 (F2)")
        drag_pick.clicked.connect(lambda: self._pick_into(self.to_x, self.to_y))
        drag_row.addWidget(drag_pick)
        self._drag_holder = QWidget()
        self._drag_holder.setLayout(drag_row)
        self.form.addRow("드래그 도착 좌표", self._drag_holder)

        self.button = QComboBox()
        for name, label in (("left", "왼쪽"), ("right", "오른쪽"), ("middle", "가운데")):
            self.button.addItem(label, name)
        self.button.setCurrentIndex(max(0, self.button.findData(p.get("button", "left"))))
        self.form.addRow("버튼", self.button)

        self.scroll_amount = QSpinBox()
        self.scroll_amount.setRange(-100, 100)
        self.scroll_amount.setValue(int(p.get("scroll_amount", 3)))
        self.scroll_amount.setToolTip("양수는 위로, 음수는 아래로 스크롤합니다.")
        self.form.addRow("스크롤 양", self.scroll_amount)

        self.duration = QSpinBox()
        self.duration.setRange(0, 10000)
        self.duration.setSingleStep(50)
        self.duration.setSuffix(" ms")
        self.duration.setValue(int(p.get("duration_ms", 0)))
        self.duration.setToolTip("0이면 즉시 이동합니다. 값을 주면 그 시간 동안 천천히 움직입니다.")
        self.form.addRow("이동 시간", self.duration)

        self.jitter = QSpinBox()
        self.jitter.setRange(0, 500)
        self.jitter.setSuffix(" px")
        self.jitter.setValue(int(p.get("jitter_px", 0) or 0))
        self.jitter.setToolTip(
            "매번 같은 픽셀을 누르지 않고 이 범위 안에서 조금씩 다른 곳을 누릅니다.\n"
            "0이면 항상 정확히 같은 지점입니다.\n"
            "드래그는 시작점과 도착점 모두에 적용됩니다."
        )
        self.form.addRow("좌표 오차 범위", self.jitter)

        self.action.currentIndexChanged.connect(self._sync_visibility)
        self.use_current.toggled.connect(self._sync_visibility)
        self._sync_visibility()

    def _coord_spin(self, value: Any) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(MIN_COORD, MAX_COORD)
        spin.setValue(int(value) if value is not None else 0)
        return spin

    def _pick_into(self, x_spin: QSpinBox, y_spin: QSpinBox) -> None:
        picker = CoordPicker(self)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.result_point:
            x_spin.setValue(picker.result_point[0])
            y_spin.setValue(picker.result_point[1])
            self.use_current.setChecked(False)

    def _sync_visibility(self) -> None:
        action = self.action.currentData()
        at_current = self.use_current.isChecked()
        self.x.setEnabled(not at_current)
        self.y.setEnabled(not at_current)
        _set_row_visible(self.form, self._drag_holder, action == "drag")
        _set_row_visible(self.form, self.button, action in ("drag", "mouse_down", "mouse_up"))
        _set_row_visible(self.form, self.scroll_amount, action == "scroll")

    def to_params(self) -> dict[str, Any]:
        action = self.action.currentData()
        params: dict[str, Any] = {"action": action}
        if self.use_current.isChecked():
            params["x"] = None
            params["y"] = None
        else:
            params["x"] = self.x.value()
            params["y"] = self.y.value()
        if action == "drag":
            params["to_x"] = self.to_x.value()
            params["to_y"] = self.to_y.value()
        if action in ("drag", "mouse_down", "mouse_up"):
            params["button"] = self.button.currentData()
        if action == "scroll":
            params["scroll_amount"] = self.scroll_amount.value()
        params["duration_ms"] = self.duration.value()
        params["jitter_px"] = self.jitter.value()
        return params


class KeyStepDialog(_BaseStepDialog):
    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "키보드 단계")
        p = dict(step.params) if step else {}

        self.action = QComboBox()
        for key, label in (
            ("press", "키 한 번 입력"),
            ("hotkey", "단축키 조합 (Ctrl+C 등)"),
            ("type", "텍스트 입력"),
            ("key_down", "키 누르고 있기"),
            ("key_up", "키 떼기"),
        ):
            self.action.addItem(label, key)
        self.action.setCurrentIndex(max(0, self.action.findData(p.get("action", "press"))))
        self.form.addRow("동작", self.action)

        self.key = QComboBox()
        self.key.setEditable(True)
        self.key.addItems(keymap.COMMON_KEYS)
        self.key.setCurrentText(str(p.get("key", "enter")))
        self.form.addRow("키", self.key)

        self.keys = QLineEdit("+".join(str(k) for k in (p.get("keys") or ["ctrl", "c"])))
        self.keys.setPlaceholderText("예: ctrl+shift+s")
        self.form.addRow("단축키", self.keys)

        self.text = QPlainTextEdit(str(p.get("text", "")))
        self.text.setPlaceholderText("한글과 대부분의 기호가 그대로 입력됩니다.")
        self.text.setFixedHeight(80)
        self.form.addRow("텍스트", self.text)

        self.interval = QSpinBox()
        self.interval.setRange(0, 1000)
        self.interval.setSuffix(" ms")
        self.interval.setValue(int(p.get("interval_ms", 0)))
        self.form.addRow("글자 간격", self.interval)

        self.action.currentIndexChanged.connect(self._sync_visibility)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        action = self.action.currentData()
        _set_row_visible(self.form, self.key, action in ("press", "key_down", "key_up"))
        _set_row_visible(self.form, self.keys, action == "hotkey")
        _set_row_visible(self.form, self.text, action == "type")
        _set_row_visible(self.form, self.interval, action == "type")

    def to_params(self) -> dict[str, Any]:
        action = self.action.currentData()
        params: dict[str, Any] = {"action": action}
        if action in ("press", "key_down", "key_up"):
            params["key"] = self.key.currentText().strip().lower()
        elif action == "hotkey":
            params["keys"] = [k.strip().lower() for k in self.keys.text().split("+") if k.strip()]
        elif action == "type":
            params["text"] = self.text.toPlainText()
            params["interval_ms"] = self.interval.value()
        return params


class DelayStepDialog(_BaseStepDialog):
    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "딜레이 단계")
        p = dict(step.params) if step else {}

        self.ms = QSpinBox()
        self.ms.setRange(0, 3600000)
        self.ms.setSingleStep(100)
        self.ms.setSuffix(" ms")
        self.ms.setValue(int(p.get("ms", 500)))
        self.form.addRow("대기 시간", self.ms)

        self.jitter = QSpinBox()
        self.jitter.setRange(0, 60000)
        self.jitter.setSingleStep(50)
        self.jitter.setSuffix(" ms")
        self.jitter.setValue(int(p.get("jitter_ms", 0)))
        self.jitter.setToolTip("매번 ±이 값만큼 무작위로 흔들어 기계적인 간격을 피합니다.")
        self.form.addRow("무작위 폭", self.jitter)

    def to_params(self) -> dict[str, Any]:
        return {"ms": self.ms.value(), "jitter_ms": self.jitter.value()}


class ImageConditionDialog(_BaseStepDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        step: Step | None = None,
        images_dir: Path | None = None,
        **_: Any,
    ) -> None:
        super().__init__(parent, "이미지 조건 단계")
        self.setMinimumWidth(540)
        p = dict(step.params) if step else {}

        self.template = TemplatePicker(self, images_dir)
        self.template.set_params(p)
        self.form.addRow(self.template)

        self.timeout = QSpinBox()
        self.timeout.setRange(0, 600000)
        self.timeout.setSingleStep(500)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(int(p.get("timeout_ms", 0)))
        self.timeout.setToolTip(
            "0이면 즉시 한 번만 확인합니다. 값을 주면 나타날 때까지 그 시간만큼 기다립니다."
        )
        self.form.addRow("최대 대기", self.timeout)

        self.click_on_match = QCheckBox("찾으면 그 위치를 클릭")
        self.click_on_match.setChecked(bool(p.get("click_on_match", False)))
        self.form.addRow("", self.click_on_match)

        offset = p.get("match_offset") or [0, 0]
        self.offset_x = QSpinBox()
        self.offset_x.setRange(-5000, 5000)
        self.offset_x.setValue(int(offset[0]))
        self.offset_y = QSpinBox()
        self.offset_y.setRange(-5000, 5000)
        self.offset_y.setValue(int(offset[1]))
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("X"))
        offset_row.addWidget(self.offset_x)
        offset_row.addWidget(QLabel("Y"))
        offset_row.addWidget(self.offset_y)
        offset_holder = QWidget()
        offset_holder.setLayout(offset_row)
        offset_holder.setToolTip("찾은 이미지 중앙에서 이만큼 떨어진 곳을 클릭합니다.")
        self.form.addRow("클릭 보정", offset_holder)

        self.click_jitter = _click_jitter_field(int(p.get("click_jitter_px", 0) or 0))
        self.form.addRow("클릭 오차 범위", self.click_jitter)

        self.key_on_match = _key_on_match_field(str(p.get("key_on_match", "")))
        self.form.addRow("찾으면 키 입력", self.key_on_match)

    def to_params(self) -> dict[str, Any]:
        params = self.template.to_params()
        params.update(
            {
                "timeout_ms": self.timeout.value(),
                "click_on_match": self.click_on_match.isChecked(),
                "match_offset": [self.offset_x.value(), self.offset_y.value()],
                "key_on_match": self.key_on_match.currentText().strip().lower(),
                "click_jitter_px": self.click_jitter.value(),
            }
        )
        return params


class LoopStepDialog(_BaseStepDialog):
    """Repeat the steps nested inside this block."""

    def __init__(
        self,
        parent: QWidget | None = None,
        step: Step | None = None,
        images_dir: Path | None = None,
        **_: Any,
    ) -> None:
        super().__init__(parent, "반복 블록")
        self.setMinimumWidth(540)
        p = dict(step.params) if step else {}

        self.mode = QComboBox()
        for key, label in (
            ("count", "정해진 횟수만큼"),
            ("forever", "무한 (탈출 단계로 빠져나감)"),
            ("while_image", "이미지가 보이는 동안"),
            ("until_image", "이미지가 나타날 때까지"),
        ):
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(p.get("mode", "count"))))
        self.form.addRow("반복 방식", self.mode)

        self.count = QSpinBox()
        self.count.setRange(1, 1000000)
        self.count.setValue(int(p.get("count", 3)))
        self.form.addRow("횟수", self.count)

        self.max_iterations = QSpinBox()
        self.max_iterations.setRange(1, 1000000)
        self.max_iterations.setValue(int(p.get("max_iterations", 1000)))
        self.max_iterations.setToolTip(
            "조건이 끝내 바뀌지 않아도 이 횟수에서 루프를 멈춥니다. 안전장치입니다."
        )
        self.form.addRow("최대 반복", self.max_iterations)

        self.template = TemplatePicker(self, images_dir)
        self.template.set_params(p)
        self.form.addRow(self.template)

        self.mode.currentIndexChanged.connect(self._sync_visibility)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        mode = self.mode.currentData()
        _set_row_visible(self.form, self.count, mode == "count")
        _set_row_visible(self.form, self.max_iterations, mode != "count")
        self.template.setVisible(mode in ("while_image", "until_image"))
        self.adjustSize()

    def to_params(self) -> dict[str, Any]:
        mode = self.mode.currentData()
        params: dict[str, Any] = {
            "mode": mode,
            "count": self.count.value(),
            "max_iterations": self.max_iterations.value(),
        }
        params.update(self.template.to_params())
        return params


class ImageWatchDialog(_BaseStepDialog):
    """Build the "watch for an image and click it" block in one go.

    This is the most common thing people want: sit in a loop, check the screen
    every few seconds, and click the thing when it shows up. Assembling it by
    hand means a loop, an image condition and a delay, so this dialog writes
    those three steps for you. They are ordinary steps afterwards - open them
    and change anything.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        images_dir: Path | None = None,
        **_: Any,
    ) -> None:
        super().__init__(parent, "이미지 감시 추가")
        self.setMinimumWidth(560)

        self.form.addRow(
            QLabel(
                "화면을 일정 간격으로 확인해서, 지정한 이미지가 보이면 클릭합니다.\n"
                "반복 블록 + 이미지 조건 + 딜레이 세 단계로 만들어집니다."
            )
        )

        self.template = TemplatePicker(self, images_dir)
        self.template.set_params({})
        self.form.addRow(self.template)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 3600.0)
        self.interval.setSingleStep(0.5)
        self.interval.setDecimals(1)
        self.interval.setValue(1.0)
        self.interval.setSuffix(" 초")
        self.interval.setToolTip("한 번 확인한 뒤 다음 확인까지 기다리는 시간입니다.")
        self.form.addRow("확인 주기", self.interval)

        self.click_on_match = QCheckBox("보이면 그 위치를 클릭")
        self.click_on_match.setChecked(True)
        self.form.addRow("", self.click_on_match)

        self.offset_x = QSpinBox()
        self.offset_x.setRange(-5000, 5000)
        self.offset_y = QSpinBox()
        self.offset_y.setRange(-5000, 5000)
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("X"))
        offset_row.addWidget(self.offset_x)
        offset_row.addWidget(QLabel("Y"))
        offset_row.addWidget(self.offset_y)
        offset_holder = QWidget()
        offset_holder.setLayout(offset_row)
        offset_holder.setToolTip("찾은 이미지 중앙에서 이만큼 떨어진 곳을 클릭합니다.")
        self.form.addRow("클릭 보정", offset_holder)

        self.click_jitter = _click_jitter_field()
        self.form.addRow("클릭 오차 범위", self.click_jitter)

        self.key_on_match = _key_on_match_field()
        self.form.addRow("보이면 키 입력", self.key_on_match)

        self.after_match = QComboBox()
        self.after_match.addItem("계속 감시한다", "continue")
        self.after_match.addItem("감시를 끝낸다", "stop")
        self.form.addRow("클릭한 뒤", self.after_match)

        self.max_checks = QSpinBox()
        self.max_checks.setRange(1, 1000000)
        self.max_checks.setValue(1000)
        self.max_checks.setToolTip("이 횟수만큼 확인하면 감시를 끝냅니다. 안전장치입니다.")
        self.form.addRow("최대 확인 횟수", self.max_checks)

    def to_params(self) -> dict[str, Any]:
        # Not a step type; the caller uses to_steps() instead.
        return {}

    def to_steps(self) -> list[Step]:
        template = self.template.to_params()

        condition = models.if_image_step(
            template["image"],
            confidence=template["confidence"],
            region=template["region"],
            grayscale=template["grayscale"],
            use_cache=template["use_cache"],
            timeout_ms=0,          # one look per turn; the delay sets the pace
            click_on_match=self.click_on_match.isChecked(),
            match_offset=[self.offset_x.value(), self.offset_y.value()],
            key_on_match=self.key_on_match.currentText().strip().lower(),
            click_jitter_px=self.click_jitter.value(),
        )
        if self.after_match.currentData() == "stop":
            condition.then_steps = [models.jump_step("break")]

        block = models.loop_step(
            "forever",
            max_iterations=self.max_checks.value(),
        )
        block.children = [
            condition,
            models.delay_step(int(round(self.interval.value() * 1000))),
        ]
        return [block]


class LabelStepDialog(_BaseStepDialog):
    """A named position that a jump step can return to."""

    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "라벨")
        p = dict(step.params) if step else {}
        self.name = QLineEdit(str(p.get("name", "시작")))
        self.name.setPlaceholderText("예: 시작, 로그인후, 재시도")
        self.form.addRow("라벨 이름", self.name)
        self.form.addRow(
            "",
            QLabel(
                "라벨은 아무 동작도 하지 않습니다.\n'라벨로 이동' 단계가 돌아올 위치를 표시합니다."
            ),
        )

    def to_params(self) -> dict[str, Any]:
        return {"name": self.name.text().strip()}


class JumpStepDialog(_BaseStepDialog):
    """break / continue / restart / stop / goto."""

    def __init__(
        self,
        parent: QWidget | None = None,
        step: Step | None = None,
        macro: Macro | None = None,
        **_: Any,
    ) -> None:
        super().__init__(parent, "흐름 제어")
        p = dict(step.params) if step else {}

        self.action = QComboBox()
        for key, label in (
            ("break", "루프 탈출 (지금 반복 블록을 끝냄)"),
            ("continue", "다음 반복으로 건너뛰기"),
            ("restart", "매크로 처음으로 돌아가기"),
            ("goto", "라벨로 이동"),
            ("stop", "매크로 종료"),
        ):
            self.action.addItem(label, key)
        self.action.setCurrentIndex(max(0, self.action.findData(p.get("action", "break"))))
        self.form.addRow("동작", self.action)

        self.target = QComboBox()
        self.target.setEditable(True)
        labels = macro.labels() if macro is not None else []
        self.target.addItems(labels)
        self.target.setCurrentText(str(p.get("target", labels[0] if labels else "")))
        self.form.addRow("이동할 라벨", self.target)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self.form.addRow("", self._hint)

        if not labels:
            self.target.setToolTip("먼저 라벨 단계를 추가하세요.")

        self.action.currentIndexChanged.connect(self._sync_visibility)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        action = self.action.currentData()
        _set_row_visible(self.form, self.target, action == "goto")
        self._hint.setText(
            {
                "break": "가장 가까운 반복 블록을 빠져나옵니다. 보통 이미지 조건 안에 둡니다.",
                "continue": "이번 회차의 남은 단계를 건너뛰고 다음 회차로 갑니다.",
                "restart": "매크로의 첫 단계부터 다시 시작합니다.",
                "goto": "맨 바깥 목록에 있는 라벨로 이동합니다.",
                "stop": "매크로를 즉시 끝냅니다. 전체 반복도 끝납니다.",
            }.get(action, "")
        )
        self.adjustSize()

    def to_params(self) -> dict[str, Any]:
        action = self.action.currentData()
        return {"action": action, "target": self.target.currentText().strip()}


def _set_row_visible(form: QFormLayout, widget: QWidget, visible: bool) -> None:
    widget.setVisible(visible)
    label = form.labelForField(widget)
    if label is not None:
        label.setVisible(visible)


class RandomStepDialog(_BaseStepDialog):
    """A block that runs exactly one of its options, picked at random.

    Only the number of options is set here; what goes in each one is edited in
    the step list, like any other block.
    """

    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "랜덤 선택")
        existing = len(step.children) if step is not None else 2

        self.count = QSpinBox()
        self.count.setRange(2, 10)
        self.count.setValue(max(2, existing))
        self.form.addRow("선택지 개수", self.count)
        self.form.addRow(
            "",
            QLabel(
                "실행할 때마다 선택지 중 하나를 같은 확률로 골라 그 안의 단계만 실행합니다.\n"
                "각 선택지의 내용은 목록에서 '선택지 N' 을 고른 뒤 단계를 추가하세요.\n"
                "개수를 줄이면 뒤쪽 선택지부터 지워집니다."
            ),
        )

    def to_params(self) -> dict[str, Any]:
        # The caller syncs the child options to match this count.
        return {"count": self.count.value()}


class WindowStepDialog(_BaseStepDialog):
    """Pick the application the macro should be typing into.

    Input goes wherever the focus is, so targeting an application means
    bringing its window to the front first.
    """

    def __init__(self, parent: QWidget | None = None, step: Step | None = None, **_: Any) -> None:
        super().__init__(parent, "창 선택")
        self.setMinimumWidth(560)
        p = dict(step.params) if step else {}

        self.action = QComboBox()
        self.action.addItem("창을 앞으로 가져와 활성화", "activate")
        self.action.addItem("창이 나타날 때까지 기다리기만", "wait")
        self.action.setCurrentIndex(max(0, self.action.findData(p.get("action", "activate"))))
        self.form.addRow("동작", self.action)

        self.open_windows = QComboBox()
        refresh = QPushButton("새로 고침")
        refresh.clicked.connect(self._reload_windows)
        picker_row = QHBoxLayout()
        picker_row.addWidget(self.open_windows, 1)
        picker_row.addWidget(refresh)
        picker_holder = QWidget()
        picker_holder.setLayout(picker_row)
        self.form.addRow("열려 있는 창", picker_holder)
        self.open_windows.currentIndexChanged.connect(self._fill_from_selection)

        self.title = QLineEdit(str(p.get("title", "")))
        self.title.setPlaceholderText("제목에 이 글자가 들어가면 일치 (비우면 조건 없음)")
        self.form.addRow("창 제목 포함", self.title)

        self.process = QLineEdit(str(p.get("process", "")))
        self.process.setPlaceholderText("예: notepad.exe (비우면 조건 없음)")
        self.form.addRow("프로세스 이름", self.process)

        self.timeout = QSpinBox()
        self.timeout.setRange(0, 600000)
        self.timeout.setSingleStep(500)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(int(p.get("timeout_ms", 5000)))
        self.timeout.setToolTip("창이 아직 없으면 이 시간만큼 기다립니다.")
        self.form.addRow("최대 대기", self.timeout)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self.form.addRow("", self._hint)

        test = QPushButton("지금 이 조건에 맞는 창 찾아보기")
        test.clicked.connect(self._test_match)
        self.add_widget(test)

        self._reload_windows()

    def _reload_windows(self) -> None:
        self.open_windows.blockSignals(True)
        self.open_windows.clear()
        self.open_windows.addItem("(직접 입력)", None)
        for window in win.list_windows():
            self.open_windows.addItem(window.label(), window)
        self.open_windows.blockSignals(False)

        if self.open_windows.count() == 1:
            self._hint.setText(
                "열려 있는 창을 읽지 못했습니다. 이 기능은 Windows에서만 동작합니다.\n"
                "제목이나 프로세스 이름을 직접 입력해도 됩니다."
                if not win.IS_WINDOWS
                else "창 목록이 비어 있습니다. 대상 프로그램을 띄운 뒤 새로 고침하세요."
            )
        else:
            self._hint.setText("목록에서 고르면 아래 칸이 채워집니다. 직접 고쳐도 됩니다.")

    def _fill_from_selection(self) -> None:
        window = self.open_windows.currentData()
        if window is None:
            return
        self.title.setText(window.title)
        self.process.setText(window.process)

    def _test_match(self) -> None:
        title = self.title.text().strip()
        process = self.process.text().strip()
        if not title and not process:
            QMessageBox.warning(self, "확인", "창 제목이나 프로세스 이름 중 하나는 적어야 합니다.")
            return
        window = win.find(title, process)
        if window is None:
            QMessageBox.information(self, "결과", "지금 조건에 맞는 창이 없습니다.")
        else:
            QMessageBox.information(
                self, "결과",
                f"찾았습니다.\n제목: {window.title}\n프로세스: {window.process}",
            )

    def to_params(self) -> dict[str, Any]:
        return {
            "action": self.action.currentData(),
            "title": self.title.text().strip(),
            "process": self.process.text().strip(),
            "timeout_ms": self.timeout.value(),
        }


DIALOGS = {
    models.MOUSE: MouseStepDialog,
    models.KEY: KeyStepDialog,
    models.DELAY: DelayStepDialog,
    models.IF_IMAGE: ImageConditionDialog,
    models.LOOP: LoopStepDialog,
    models.LABEL: LabelStepDialog,
    models.JUMP: JumpStepDialog,
    models.RANDOM: RandomStepDialog,
    models.WINDOW: WindowStepDialog,
}


def build_image_watch(
    parent: QWidget | None,
    images_dir: Path | None = None,
) -> list[Step] | None:
    """Ask for the watch settings and return the steps, or None if cancelled."""
    dialog = ImageWatchDialog(parent, images_dir=images_dir)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.to_steps()
    return None


def edit_step(
    parent: QWidget | None,
    step_type: str,
    step: Step | None = None,
    images_dir: Path | None = None,
    macro: Macro | None = None,
) -> dict[str, Any] | None:
    """Open the right editor. Returns new params, or None when cancelled."""
    dialog = DIALOGS[step_type](parent, step, images_dir=images_dir, macro=macro)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.to_params()
    return None
