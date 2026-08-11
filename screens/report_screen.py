"""Экран отчёта и анализа затрат."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
)
from PySide6.QtCore import Qt


class ReportScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QLabel("Отчёт и анализ затрат")
        header.setFixedHeight(30)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        self.report_preview = QTextEdit(readOnly=True)
        layout.addWidget(self.report_preview)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("Сохранить проект", clicked=self.main.save_project))
        btn_layout.addWidget(QPushButton("Создать DOCX", clicked=self.main.generate_docx))
        btn_layout.addWidget(QPushButton("Экспорт CSV", clicked=self.main.export_csv))
        btn_layout.addWidget(QPushButton("Экспорт Excel", clicked=self.main.export_xlsx))
        layout.addLayout(btn_layout)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.main.stack.setCurrentIndex(2)))
        layout.addLayout(nav)