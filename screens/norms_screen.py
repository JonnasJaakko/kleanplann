"""Экран нормативов СанПиН."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt

from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY


class NormsScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QLabel("Нормативы СанПиН по типам помещений")
        header.setFixedHeight(30)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        self.norms_table = QTableWidget(0, 3)
        self.norms_table.setHorizontalHeaderLabels(["Тип", "Коэффициент сложности", "Частота (раз/день)"])
        layout.addWidget(self.norms_table)

        btn_layout_norms = QHBoxLayout()
        btn_add_type = QPushButton("+ Добавить тип")
        btn_add_type.clicked.connect(self.add_norm_type)
        btn_remove_type = QPushButton("✕ Удалить тип")
        btn_remove_type.clicked.connect(self.remove_norm_type)
        btn_layout_norms.addWidget(btn_add_type)
        btn_layout_norms.addWidget(btn_remove_type)
        layout.addLayout(btn_layout_norms)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self.save_norms)
        layout.addWidget(btn_save)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.main.stack.setCurrentIndex(0)))
        layout.addLayout(nav)

    def load_norms_screen(self):
        self.norms_table.setRowCount(0)
        for room_type, coeff in COMPLEXITY_FACTOR.items():
            row = self.norms_table.rowCount()
            self.norms_table.insertRow(row)
            self.norms_table.setItem(row, 0, QTableWidgetItem(room_type))
            self.norms_table.setItem(row, 1, QTableWidgetItem(str(coeff)))
            self.norms_table.setItem(row, 2, QTableWidgetItem(str(DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1))))

    def add_norm_type(self):
        name, ok = QInputDialog.getText(self, "Добавить тип", "Название типа помещения:")
        if ok and name and name not in COMPLEXITY_FACTOR:
            COMPLEXITY_FACTOR[name] = 1.0
            DEFAULT_FREQUENCY_PER_DAY[name] = 1
            self.load_norms_screen()

    def remove_norm_type(self):
        row = self.norms_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Ошибка", "Выберите тип для удаления.")
            return
        room_type = self.norms_table.item(row, 0).text()
        if room_type in COMPLEXITY_FACTOR:
            del COMPLEXITY_FACTOR[room_type]
        if room_type in DEFAULT_FREQUENCY_PER_DAY:
            del DEFAULT_FREQUENCY_PER_DAY[room_type]
        self.load_norms_screen()

    def save_norms(self):
        for row in range(self.norms_table.rowCount()):
            room_type = self.norms_table.item(row, 0).text()
            try:
                coeff = float(self.norms_table.item(row, 1).text())
                freq = int(self.norms_table.item(row, 2).text())
                COMPLEXITY_FACTOR[room_type] = coeff
                DEFAULT_FREQUENCY_PER_DAY[room_type] = freq
            except Exception:
                pass
        QMessageBox.information(self, "Успех", "Нормативы обновлены.")