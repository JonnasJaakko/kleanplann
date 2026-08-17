"""Финальный экран: зоны ответственности + вертикальная отчётность."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGraphicsScene, QGraphicsView, QGraphicsPolygonItem, QGraphicsTextItem,
    QGraphicsItem, QGraphicsRectItem, QToolTip, QSplitter, QTextEdit,
    QScrollArea, QMenu
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QColor, QBrush, QPolygonF
from zone_manager import PRIORITY_BALANCED, PRIORITY_PROXIMITY, PRIORITY_AREA, PRIORITY_COUNT


class ZoneScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel("Зоны ответственности и отчётность")
        header.setFixedHeight(30); header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        self.zone_scene = QGraphicsScene()
        self.zone_view = QGraphicsView(self.zone_scene)
        self.zone_view.setMinimumSize(300, 300)
        self.zone_view.wheelEvent = lambda ev: self.zone_view.scale(
            1.15 if ev.angleDelta().y() > 0 else 1 / 1.15,
            1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
        splitter.addWidget(self.zone_view)

        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.addWidget(QLabel("<b>Приоритет распределения зон</b>"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItem("Сбалансированно", PRIORITY_BALANCED)
        self.priority_combo.addItem("Близость комнат", PRIORITY_PROXIMITY)
        self.priority_combo.addItem("Площадь", PRIORITY_AREA)
        self.priority_combo.addItem("Количество комнат", PRIORITY_COUNT)
        self.priority_combo.currentIndexChanged.connect(self.main.recalculate_zones)
        pv.addWidget(self.priority_combo)

        self.report_preview = QTextEdit(readOnly=True)
        self.report_preview.setMinimumWidth(420)
        pv.addWidget(self.report_preview, 1)

        export_btn = QPushButton("Экспорт")
        export_btn.setMinimumHeight(38)
        export_btn.clicked.connect(self.show_export_menu)
        pv.addWidget(export_btn)
        back = QPushButton("← Назад в редактор")
        back.setMinimumHeight(34)
        back.clicked.connect(self.main.back_to_editor)
        pv.addWidget(back)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def show_export_menu(self):
        menu = QMenu(self)
        menu.addAction("В Word (.docx)", self.main.generate_docx)
        menu.addAction("В CSV (.csv)", self.main.export_csv)
        menu.addAction("В Excel (.xlsx)", self.main.export_xlsx)
        menu.exec(self.mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.rect().center())

    def load_display(self):
        self.main.refresh_zone_display()
        self.main.load_report_screen()
