"""Main editor window: step tree, recorder, player and log."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import diagnostics, models
from ..hotkeys import GlobalHotkeys
from ..models import Macro, Step
from ..paths import IMAGE_DIR, MACRO_DIR, ensure_dirs
from ..player import Player
from ..recorder import Recorder
from .step_dialogs import edit_step

HOTKEY_RECORD = "<f8>"
HOTKEY_RUN = "<f9>"
HOTKEY_STOP = "<f10>"

# How deeply blocks may nest. Past this the tree stops being readable, and a
# macro that needs more is better split into two.
MAX_DEPTH = 5


class PlayerThread(QThread):
    log = Signal(str)
    finished_run = Signal()

    def __init__(self, macro: Macro, stop_event: threading.Event) -> None:
        super().__init__()
        self.macro = macro
        self.stop_event = stop_event

    def run(self) -> None:  # noqa: D102
        try:
            Player(self.macro, self.stop_event, self.log.emit).run()
        except Exception as exc:
            self.log.emit(f"실행 스레드 오류: {exc}")
        finally:
            self.finished_run.emit()


class MainWindow(QMainWindow):
    _hotkey_record = Signal()
    _hotkey_run = Signal()
    _hotkey_stop = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MyMacro")
        self.resize(1020, 740)

        ensure_dirs()
        self.macro = Macro()
        self.current_path: Path | None = None
        # item id -> (kind, container list, index, depth)
        self._nodes: dict[int, tuple[str, list, int, int]] = {}

        self._stop_event = threading.Event()
        self._player_thread: PlayerThread | None = None
        self._recorder: Recorder | None = None
        self._record_target: tuple[list, int] = (self.macro.steps, 0)

        self._build_ui()
        self._build_menu()
        self._install_hotkeys()
        self._refresh_tree()
        self._report_environment()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_run_bar())

        splitter = QSplitter(Qt.Orientation.Vertical)

        editor = QWidget()
        editor_layout = QHBoxLayout(editor)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["단계", "사용"])
        self.tree.setColumnWidth(0, 660)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_selected())
        self.tree.itemChanged.connect(self._on_item_changed)
        editor_layout.addWidget(self.tree, 1)
        editor_layout.addLayout(self._build_step_buttons())
        splitter.addWidget(editor)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(5000)
        splitter.addWidget(self.log_view)
        splitter.setSizes([500, 200])

        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("F8 녹화 / F9 실행 / F10 중지")

    def _build_run_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self.record_button = QPushButton("● 녹화 시작 (F8)")
        self.record_button.clicked.connect(self.toggle_record)
        bar.addWidget(self.record_button)

        self.run_button = QPushButton("▶ 실행 (F9)")
        self.run_button.clicked.connect(self.run_macro)
        bar.addWidget(self.run_button)

        self.stop_button = QPushButton("■ 중지 (F10)")
        self.stop_button.clicked.connect(self.stop_all)
        self.stop_button.setEnabled(False)
        bar.addWidget(self.stop_button)

        bar.addSpacing(20)
        bar.addWidget(QLabel("전체 반복"))
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(0, 100000)
        self.repeat_spin.setValue(1)
        self.repeat_spin.setSpecialValueText("무한")
        self.repeat_spin.setToolTip("0으로 두면 F10으로 멈출 때까지 반복합니다.")
        bar.addWidget(self.repeat_spin)

        bar.addWidget(QLabel("반복 간격"))
        self.repeat_delay_spin = QSpinBox()
        self.repeat_delay_spin.setRange(0, 600000)
        self.repeat_delay_spin.setSingleStep(100)
        self.repeat_delay_spin.setSuffix(" ms")
        bar.addWidget(self.repeat_delay_spin)

        self.minimize_on_run = QCheckBox("실행 중 창 숨기기")
        self.minimize_on_run.setChecked(True)
        bar.addWidget(self.minimize_on_run)

        bar.addStretch(1)
        return bar

    def _build_step_buttons(self) -> QVBoxLayout:
        column = QVBoxLayout()
        entries = [
            ("마우스 추가", lambda: self._add_step(models.MOUSE)),
            ("키보드 추가", lambda: self._add_step(models.KEY)),
            ("딜레이 추가", lambda: self._add_step(models.DELAY)),
            (None, None),
            ("이미지 조건 추가", lambda: self._add_step(models.IF_IMAGE)),
            ("반복 블록 추가", lambda: self._add_step(models.LOOP)),
            ("라벨 추가", lambda: self._add_step(models.LABEL)),
            ("흐름 제어 추가", lambda: self._add_step(models.JUMP)),
            (None, None),
            ("수정", self._edit_selected),
            ("복제", self._duplicate_selected),
            ("삭제", self._delete_selected),
            (None, None),
            ("위로", lambda: self._move_selected(-1)),
            ("아래로", lambda: self._move_selected(1)),
        ]
        for label, handler in entries:
            if label is None:
                column.addSpacing(12)
                continue
            button = QPushButton(label)
            button.clicked.connect(handler)
            column.addWidget(button)
        column.addStretch(1)
        return column

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("파일")
        for label, shortcut, handler in (
            ("새 매크로", "Ctrl+N", self.new_macro),
            ("열기...", "Ctrl+O", self.open_macro),
            ("저장", "Ctrl+S", self.save_macro),
            ("다른 이름으로 저장...", "Ctrl+Shift+S", self.save_macro_as),
        ):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            file_menu.addAction(action)

        tools_menu = self.menuBar().addMenu("도구")
        diag_action = QAction("입력 진단...", self)
        diag_action.triggered.connect(self.show_diagnostics)
        tools_menu.addAction(diag_action)

    def _install_hotkeys(self) -> None:
        # pynput fires on its own thread, so bounce through signals.
        self._hotkey_record.connect(self.toggle_record)
        self._hotkey_run.connect(self.run_macro)
        self._hotkey_stop.connect(self.stop_all)
        self._hotkeys = GlobalHotkeys(
            {
                HOTKEY_RECORD: self._hotkey_record.emit,
                HOTKEY_RUN: self._hotkey_run.emit,
                HOTKEY_STOP: self._hotkey_stop.emit,
            }
        )
        self._hotkeys.start()

    # --------------------------------------------------------------- tree
    def _refresh_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._nodes.clear()
        for index, step in enumerate(self.macro.steps):
            self.tree.addTopLevelItem(self._make_item(step, self.macro.steps, index, 0))
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _make_item(self, step: Step, container: list, index: int, depth: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{index + 1}. {step.describe()}", ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(1, Qt.CheckState.Checked if step.enabled else Qt.CheckState.Unchecked)
        if not step.enabled:
            item.setForeground(0, QColor("#999999"))
        elif step.type == models.LOOP:
            item.setForeground(0, QColor("#1e8e5a"))
        elif step.type in (models.LABEL, models.JUMP):
            item.setForeground(0, QColor("#b8860b"))
        self._nodes[id(item)] = ("step", container, index, depth)

        for label, sub_steps in step.branches():
            group = QTreeWidgetItem([label, ""])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            group.setForeground(0, QColor("#3a7bd5"))
            self._nodes[id(group)] = ("group", sub_steps, -1, depth + 1)
            for sub_index, sub_step in enumerate(sub_steps):
                group.addChild(self._make_item(sub_step, sub_steps, sub_index, depth + 1))
            item.addChild(group)
        return item

    def _selected_node(self) -> tuple[str, list, int, int] | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        return self._nodes.get(id(items[0]))

    def _target_container(self) -> tuple[list, int, int]:
        """Where a new step goes: (container, insert index, depth)."""
        node = self._selected_node()
        if node is None:
            return self.macro.steps, len(self.macro.steps), 0
        kind, container, index, depth = node
        if kind == "group":
            return container, len(container), depth
        return container, index + 1, depth

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 1:
            return
        node = self._nodes.get(id(item))
        if node is None or node[0] != "step":
            return
        _, container, index, _ = node
        container[index].enabled = item.checkState(1) == Qt.CheckState.Checked
        self._refresh_tree()

    # --------------------------------------------------------- step edits
    def _add_step(self, step_type: str) -> None:
        container, insert_at, depth = self._target_container()

        if step_type in models.CONTAINER_TYPES and depth >= MAX_DEPTH:
            QMessageBox.information(
                self, "중첩 제한",
                f"블록은 {MAX_DEPTH}단계까지만 겹칠 수 있습니다.\n"
                "더 바깥 목록을 선택한 뒤 추가하세요.",
            )
            return
        if step_type == models.LABEL and container is not self.macro.steps:
            QMessageBox.information(
                self, "라벨 위치",
                "라벨은 맨 바깥 목록에만 둘 수 있습니다.\n"
                "블록 안에서는 '루프 탈출'이나 '매크로 처음으로'를 쓰세요.",
            )
            return

        params = edit_step(self, step_type, None, IMAGE_DIR, self.macro)
        if params is None:
            return
        step = Step(type=step_type, params=params)
        container.insert(insert_at, step)
        self._refresh_tree()
        self._log(f"단계 추가: {step.describe()}")

    def _edit_selected(self) -> None:
        node = self._selected_node()
        if node is None or node[0] != "step":
            return
        _, container, index, _ = node
        step = container[index]
        params = edit_step(self, step.type, step, IMAGE_DIR, self.macro)
        if params is None:
            return
        step.params = params
        self._refresh_tree()

    def _duplicate_selected(self) -> None:
        node = self._selected_node()
        if node is None or node[0] != "step":
            return
        _, container, index, _ = node
        container.insert(index + 1, container[index].copy())
        self._refresh_tree()

    def _delete_selected(self) -> None:
        node = self._selected_node()
        if node is None or node[0] != "step":
            return
        _, container, index, _ = node
        step = container[index]
        if step.branches() and any(sub for _, sub in step.branches()):
            reply = QMessageBox.question(
                self, "삭제 확인",
                f"'{step.describe()}' 안의 단계도 함께 지워집니다. 계속할까요?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        del container[index]
        self._refresh_tree()

    def _move_selected(self, offset: int) -> None:
        node = self._selected_node()
        if node is None or node[0] != "step":
            return
        _, container, index, _ = node
        new_index = index + offset
        if not 0 <= new_index < len(container):
            return
        container[index], container[new_index] = container[new_index], container[index]
        self._refresh_tree()
        self._select_step(container, new_index)

    def _select_step(self, container: list, index: int) -> None:
        for item_id, (kind, cont, idx, _) in self._nodes.items():
            if kind == "step" and cont is container and idx == index:
                item = self._find_item(item_id)
                if item is not None:
                    self.tree.setCurrentItem(item)
                return

    def _find_item(self, item_id: int) -> QTreeWidgetItem | None:
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if id(item) == item_id:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    # ----------------------------------------------------------- recording
    def toggle_record(self) -> None:
        if self._player_thread is not None:
            return
        if self._recorder is not None and self._recorder.running:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        container, insert_at, _ = self._target_container()
        self._record_target = (container, insert_at)
        self._recorder = Recorder(ignore_keys=("f8", "f9", "f10"))
        self._recorder.start()
        self.record_button.setText("● 녹화 중지 (F8)")
        self.record_button.setStyleSheet("background-color: #c0392b; color: white;")
        self.stop_button.setEnabled(True)
        self._log("녹화 시작. 평소처럼 조작한 뒤 F8을 누르세요.")
        self.statusBar().showMessage("녹화 중... F8로 중지")

    def _stop_record(self) -> None:
        if self._recorder is None:
            return
        steps = self._recorder.stop()
        self._recorder = None
        container, insert_at = self._record_target
        for offset, step in enumerate(steps):
            container.insert(insert_at + offset, step)
        self.record_button.setText("● 녹화 시작 (F8)")
        self.record_button.setStyleSheet("")
        self.stop_button.setEnabled(False)
        self._refresh_tree()
        self._log(f"녹화 종료. {len(steps)}개 단계를 추가했습니다.")
        self.statusBar().showMessage("F8 녹화 / F9 실행 / F10 중지")

    # ------------------------------------------------------------- running
    def run_macro(self) -> None:
        if self._recorder is not None and self._recorder.running:
            return
        if self._player_thread is not None:
            return
        if not self.macro.steps:
            QMessageBox.information(self, "실행", "실행할 단계가 없습니다.")
            return

        missing = self._missing_labels()
        if missing:
            QMessageBox.warning(
                self, "라벨 없음",
                "다음 라벨로 이동하는 단계가 있는데 그 라벨이 없습니다:\n  "
                + ", ".join(sorted(missing)),
            )
            return

        self.macro.repeat = self.repeat_spin.value()
        self.macro.repeat_delay_ms = self.repeat_delay_spin.value()

        self._stop_event.clear()
        self._player_thread = PlayerThread(self.macro, self._stop_event)
        self._player_thread.log.connect(self._log)
        self._player_thread.finished_run.connect(self._on_run_finished)

        self.run_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.log_view.clear()
        self._log("실행 시작. F10 또는 마우스를 화면 왼쪽 위 모서리로 보내면 멈춥니다.")
        if self.minimize_on_run.isChecked():
            self.showMinimized()
        self._player_thread.start()

    def _missing_labels(self) -> set[str]:
        """Goto targets that do not exist, collected from every nesting level."""
        known = set(self.macro.labels())
        wanted: set[str] = set()

        def walk(steps: list[Step]) -> None:
            for step in steps:
                if step.type == models.JUMP and step.params.get("action") == "goto":
                    wanted.add(str(step.params.get("target", "")))
                for _, sub in step.branches():
                    walk(sub)

        walk(self.macro.steps)
        return {name for name in wanted if name and name not in known}

    def stop_all(self) -> None:
        if self._recorder is not None and self._recorder.running:
            self._stop_record()
            return
        self._stop_event.set()

    def _on_run_finished(self) -> None:
        self._player_thread = None
        self.run_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.minimize_on_run.isChecked():
            self.showNormal()
            self.raise_()
        self.statusBar().showMessage("F8 녹화 / F9 실행 / F10 중지")

    # ------------------------------------------------------------ file I/O
    def new_macro(self) -> None:
        self.macro = Macro()
        self.current_path = None
        self.repeat_spin.setValue(1)
        self.repeat_delay_spin.setValue(0)
        self._refresh_tree()
        self.setWindowTitle("MyMacro")

    def open_macro(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "매크로 열기", str(MACRO_DIR), "매크로 (*.json)"
        )
        if not path:
            return
        try:
            self.macro = Macro.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "열기 실패", str(exc))
            return
        self.current_path = Path(path)
        self.repeat_spin.setValue(self.macro.repeat)
        self.repeat_delay_spin.setValue(self.macro.repeat_delay_ms)
        self._refresh_tree()
        self.setWindowTitle(f"MyMacro - {self.current_path.name}")
        self._log(f"열었습니다: {path}")

    def save_macro(self) -> None:
        if self.current_path is None:
            self.save_macro_as()
            return
        self._write(self.current_path)

    def save_macro_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "매크로 저장", str(MACRO_DIR / "macro.json"), "매크로 (*.json)"
        )
        if not path:
            return
        self.current_path = Path(path)
        self._write(self.current_path)

    def _write(self, path: Path) -> None:
        self.macro.repeat = self.repeat_spin.value()
        self.macro.repeat_delay_ms = self.repeat_delay_spin.value()
        self.macro.name = path.stem
        try:
            self.macro.save(path)
        except Exception as exc:
            QMessageBox.critical(self, "저장 실패", str(exc))
            return
        self.setWindowTitle(f"MyMacro - {path.name}")
        self._log(f"저장했습니다: {path}")

    # -------------------------------------------------------- diagnostics
    def _report_environment(self) -> None:
        for line in diagnostics.report_lines():
            self._log(line)
        self._log("")

    def show_diagnostics(self) -> None:
        text = "\n".join(diagnostics.report_lines())
        front = diagnostics.foreground_window()
        if front is not None:
            text += f"\n현재 포커스 창: {front.title!r}"
        text += (
            "\n\n입력이 특정 프로그램에만 안 들어간다면:"
            "\n1. 그 프로그램이 관리자 권한으로 실행 중인지 확인하세요."
            "\n   맞다면 MyMacro도 관리자 권한으로 실행해야 합니다."
            "\n2. DirectInput을 쓰는 게임은 SendInput을 무시할 수 있습니다."
            "\n3. 백신이 입력 주입을 차단하기도 합니다."
            "\n\n자세한 자동 검사는 check_input.bat 을 실행하세요."
        )
        QMessageBox.information(self, "입력 진단", text)

    # --------------------------------------------------------------- misc
    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_event.set()
        if self._recorder is not None:
            self._recorder.stop()
        self._hotkeys.stop()
        if self._player_thread is not None:
            self._player_thread.wait(2000)
        super().closeEvent(event)
