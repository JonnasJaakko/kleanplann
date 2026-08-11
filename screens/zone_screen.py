"""Экран распределения зон ответственности."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QLineEdit, QGraphicsScene, QGraphicsView,
    QGraphicsPolygonItem, QGraphicsTextItem, QGraphicsItem, QGraphicsRectItem,
    QGraphicsProxyWidget, QInputDialog, QMessageBox, QToolTip, QSplitter
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPolygonF

from zone_manager import (PRIORITY_BALANCED, PRIORITY_PROXIMITY,
                          PRIORITY_AREA, PRIORITY_COUNT)


class ZoneScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QLabel("Распределение зон ответственности")
        header.setFixedHeight(30)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        splitter_zone = QSplitter(Qt.Horizontal)
        self.zone_scene = QGraphicsScene()
        self.zone_view = QGraphicsView(self.zone_scene)
        self.zone_view.setMinimumSize(300, 300)
        self.zone_view.wheelEvent = lambda ev: self.zone_view.scale(
            1.15 if ev.angleDelta().y() > 0 else 1 / 1.15,
            1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
        splitter_zone.addWidget(self.zone_view)

        ctrl_widget = QWidget()
        ctrl = QVBoxLayout(ctrl_widget)
        ctrl.addWidget(QLabel("Приоритет распределения:"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItem("Сбалансированно", PRIORITY_BALANCED)
        self.priority_combo.addItem("Близость комнат", PRIORITY_PROXIMITY)
        self.priority_combo.addItem("Площадь", PRIORITY_AREA)
        self.priority_combo.addItem("Количество комнат", PRIORITY_COUNT)
        self.priority_combo.currentIndexChanged.connect(self.main.recalculate_zones)
        ctrl.addWidget(self.priority_combo)

        ctrl.addWidget(QLabel("Сотрудники:"))
        self.employee_list_widget = QListWidget()
        self.employee_list_widget.setDragDropMode(QListWidget.InternalMove)
        self.employee_list_widget.setDefaultDropAction(Qt.MoveAction)
        ctrl.addWidget(self.employee_list_widget)
        ctrl.addWidget(QPushButton("Добавить сотрудника", clicked=self.main.add_employee))
        ctrl.addWidget(QPushButton("Пересчитать зоны", clicked=self.main.recalculate_zones))

        ctrl.addWidget(QLabel("Смена:"))
        shift_layout = QHBoxLayout()
        self.shift_start_edit = QLineEdit("08:00")
        self.shift_start_edit.setFixedWidth(50)
        self.shift_end_edit = QLineEdit("22:00")
        self.shift_end_edit.setFixedWidth(50)
        shift_layout.addWidget(QLabel("с"))
        shift_layout.addWidget(self.shift_start_edit)
        shift_layout.addWidget(QLabel("до"))
        shift_layout.addWidget(self.shift_end_edit)
        ctrl.addLayout(shift_layout)

        ctrl.addWidget(QLabel("Обед (HH:MM-HH:MM):"))
        self.lunch_start_edit = QLineEdit("12:00")
        self.lunch_start_edit.setFixedWidth(50)
        self.lunch_end_edit = QLineEdit("13:00")
        self.lunch_end_edit.setFixedWidth(50)
        lunch_layout = QHBoxLayout()
        lunch_layout.addWidget(QLabel("с"))
        lunch_layout.addWidget(self.lunch_start_edit)
        lunch_layout.addWidget(QLabel("до"))
        lunch_layout.addWidget(self.lunch_end_edit)
        ctrl.addLayout(lunch_layout)
        ctrl.addStretch()

        splitter_zone.addWidget(ctrl_widget)
        splitter_zone.setStretchFactor(0, 2)
        splitter_zone.setStretchFactor(1, 1)
        splitter_zone.setSizes([650, 400])
        layout.addWidget(splitter_zone)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.main.stack.setCurrentIndex(1)))
        nav.addStretch()
        nav.addWidget(QPushButton("Далее →", clicked=self.main.go_to_planning_screen))
        layout.addLayout(nav)