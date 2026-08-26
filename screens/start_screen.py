"""Стартовый экран — список недавних проектов."""
import os, json, glob
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QInputDialog, QMessageBox, QMenu
)
from PySide6.QtCore import Qt

PROJECTS_DIR = "projects"


class StartScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Добро пожаловать в KleanPlann"))
        layout.addWidget(QLabel("Недавние проекты:"))
        self.project_list = QListWidget()
        layout.addWidget(self.project_list)
        self.project_list.itemClicked.connect(self.on_project_clicked)
        self.project_list.itemDoubleClicked.connect(self.open_project)
        self.project_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self._project_context_menu)
        btn_new = QPushButton("Новый проект")
        btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Открыть проект")
        btn_open.clicked.connect(self.open_project)
        btn_norms = QPushButton("Нормативы СанПиН")
        btn_norms.clicked.connect(self.main.go_to_norms_screen)
        layout.addWidget(btn_new)
        layout.addWidget(btn_open)
        layout.addWidget(btn_norms)
        self.refresh_project_list()

    def refresh_project_list(self):
        self.project_list.clear()
        files = glob.glob(os.path.join(PROJECTS_DIR, "*.json"))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:10]:
            name = os.path.basename(f).replace('.json', '')
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                last_modified = data.get('last_modified', '')
                if last_modified:
                    try:
                        dt = datetime.fromisoformat(last_modified)
                        date_str = dt.strftime("%d.%m.%Y %H:%M")
                    except Exception:
                        date_str = ""
                else:
                    date_str = ""
                display = f"{name}\nизменён {date_str}" if date_str else name
            except Exception:
                display = name
                date_str = ""
            item = QListWidgetItem()
            item.setData(Qt.UserRole, f)
            item.setData(Qt.UserRole + 1, name)
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_widget.setMinimumHeight(40)
            row_layout.setContentsMargins(8, 0, 8, 8)
            row_layout.setSpacing(6)
            label = QLabel(display)
            label.setStyleSheet("font-size: 14px; font-weight: bold; color: black;")
            row_layout.addWidget(label)
            row_layout.addStretch()
            btn_edit = QPushButton("✏")
            btn_edit.setFixedSize(28, 28)
            btn_edit.setFlat(True)
            btn_edit.setToolTip("Переименовать")
            btn_edit.clicked.connect(
                lambda checked=False, p=f, n=name: self.main.rename_project_path(p, n))
            row_layout.addWidget(btn_edit)
            item.setSizeHint(row_widget.sizeHint())
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, row_widget)

    def on_project_clicked(self, item):
        pass

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Название помещения:")
        if ok and name:
            self.main.create_new_project(name)

    def open_project(self):
        item = self.project_list.currentItem()
        if item:
            path = item.data(Qt.UserRole)
            self.main.open_project_from_path(path)
        else:
            QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")

    def _project_context_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if not item:
            return
        menu = QMenu()
        open_action = menu.addAction("Открыть")
        open_action.triggered.connect(lambda: self._open_project_item(item))
        rename_action = menu.addAction("Переименовать")
        rename_action.triggered.connect(lambda: self._rename_project_item(item))
        delete_action = menu.addAction("Удалить")
        delete_action.setData("delete")
        menu.triggered.connect(
            lambda action: self._delete_project_item(item) if action.text() == "Удалить" else None)
        # QAction не имеет setForeground(). Используем стандартное оформление QMenu,
        # чтобы контекстное меню не зависело от внутренних API QAction.
        menu.exec(self.project_list.viewport().mapToGlobal(pos))

    def _open_project_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self.main.open_project_from_path(path)

    def _rename_project_item(self, item):
        path = item.data(Qt.UserRole)
        old_name = item.data(Qt.UserRole + 1)
        self.main.rename_project_path(path, old_name)

    def _delete_project_item(self, item):
        path = item.data(Qt.UserRole)
        name = item.data(Qt.UserRole + 1)
        ret = QMessageBox.question(
            self, "Удалить проект",
            f"Вы уверены, что хотите удалить проект «{name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            try:
                os.remove(path)
                self.refresh_project_list()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось удалить: {e}")