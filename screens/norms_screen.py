from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox
)
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY

class NormsScreen(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Нормативы СанПиН по типам помещений"))
        self.norms_table = QTableWidget(0, 3)
        self.norms_table.setHorizontalHeaderLabels([
            "Тип", "Коэффициент сложности", "Частота (раз/день)"
        ])
        layout.addWidget(self.norms_table)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self.save_norms)
        layout.addWidget(btn_save)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.main_window.stack.setCurrentIndex(0)))
        layout.addLayout(nav)

    def load_norms(self):
        self.norms_table.setRowCount(0)
        for room_type, coeff in COMPLEXITY_FACTOR.items():
            row = self.norms_table.rowCount()
            self.norms_table.insertRow(row)
            self.norms_table.setItem(row, 0, QTableWidgetItem(room_type))
            self.norms_table.setItem(row, 1, QTableWidgetItem(str(coeff)))
            freq = DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1)
            self.norms_table.setItem(row, 2, QTableWidgetItem(str(freq)))

    def save_norms(self):
        for row in range(self.norms_table.rowCount()):
            room_type = self.norms_table.item(row, 0).text()
            try:
                coeff = float(self.norms_table.item(row, 1).text())
                freq = int(self.norms_table.item(row, 2).text())
                COMPLEXITY_FACTOR[room_type] = coeff
                DEFAULT_FREQUENCY_PER_DAY[room_type] = freq
            except (ValueError, AttributeError):
                pass
        QMessageBox.information(self, "Успех", "Нормативы обновлены.")