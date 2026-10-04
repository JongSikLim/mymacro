"""Editors for every step type."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
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
from ..models import Macro, Step
from ..paths import IMAGE_DIR
from .pickers import CoordPicker, RegionPicker

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
        """Hide our windows, then show the frozen full-screen overlay."""
        dialog = self.window()
        parent_window = dialog.parent().window() if dialog.parent() is not None else None
        dialog.hide()
        if parent_window is not None:
            parent_window.hide()

        def show_overlay() -> None:
            picker = RegionPicker()
            self._picker = picker  # keep a reference alive while it is open

            def finished(region) -> None:
                if parent_window is not None:
                    parent_window.show()
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                handler(region)

            picker.selected.connect(finished)
            picker.show()
            picker.raise_()
            picker.activateWindow()

        # Give the window manager time to actually hide the windows, otherwise
        # they end up inside the screenshot.
        QTimer.singleShot(300, show_overlay)

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

    def to_params(self) -> dict[str, Any]:
        params = self.template.to_params()
        params.update(
            {
                "timeout_ms": self.timeout.value(),
                "click_on_match": self.click_on_match.isChecked(),
                "match_offset": [self.offset_x.value(), self.offset_y.value()],
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


DIALOGS = {
    models.MOUSE: MouseStepDialog,
    models.KEY: KeyStepDialog,
    models.DELAY: DelayStepDialog,
    models.IF_IMAGE: ImageConditionDialog,
    models.LOOP: LoopStepDialog,
    models.LABEL: LabelStepDialog,
    models.JUMP: JumpStepDialog,
}


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
