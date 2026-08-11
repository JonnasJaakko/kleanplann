"""Экран редактора плана помещения."""
import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolBar,
    QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QSlider,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QSplitter,
    QFileDialog, QMessageBox, QGraphicsScene, QGraphicsPolygonItem,
    QGraphicsTextItem, QGraphicsItem, QGraphicsRectItem, QGraphicsLineItem
)
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF, QTimer
from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPolygonF

from project import Room, Wall, Floor
from room_builder import build_rooms_from_walls
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_TRAFFIC_PER_TYPE
from tools import PlanView, WallSegmentItem

TYPE_COLORS = {
    "санузел": QColor(0, 0, 255),
    "коридор": QColor(0, 128, 0),
    "кабинет": QColor(255, 215, 0),
    "склад": QColor(128, 128, 128),
    "зал": QColor(255, 165, 0),
    "кухня": QColor(255, 0, 0),
}

ROOM_COLORS = [
    (255,0,0,30), (60,180,75,30), (255,225,25,30), (0,130,200,30),
    (245,130,48,30), (145,30,180,30), (70,240,240,30), (240,50,230,30),
    (210,245,60,30), (250,190,190,30), (0,128,128,30), (230,190,255,30)
]


class PlanScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_row = QHBoxLayout()
        header = QLabel("Редактор плана помещения")
        header.setFixedHeight(30)
        header.setAlignment(Qt.AlignCenter)
        header_row.addWidget(header)
        header_row.addStretch()
        btn_help_editor = QPushButton("❓ Справка")
        btn_help_editor.setFixedWidth(90)
        btn_help_editor.clicked.connect(self.main.show_help)
        header_row.addWidget(btn_help_editor)
        layout.addLayout(header_row)

        toolbar = QToolBar("Инструменты")
        self.plan_scene = QGraphicsScene()
        self.plan_view = PlanView(self.plan_scene, self.main)
        self.plan_view.setMinimumSize(300, 300)

        toolbar.addAction("💾 Сохранить", self.main.save_project)
        toolbar.addSeparator()
        toolbar.addAction("Выбор", lambda: self.plan_view.set_tool(0))
        toolbar.addAction("Ластик", lambda: self.plan_view.set_tool(1))
        toolbar.addAction("Линия", lambda: self.plan_view.set_tool(2))
        toolbar.addAction("Комната", lambda: self.plan_view.set_tool(4))
        toolbar.addAction("Калибровка", lambda: self.plan_view.set_tool(3))
        toolbar.addAction("Кисть", lambda: self.plan_view.set_tool(5))
        toolbar.addSeparator()
        toolbar.addAction("Загрузить DXF", self.main.load_dxf)
        layout.addWidget(toolbar)

        splitter_main = QSplitter(Qt.Horizontal)
        splitter_main.addWidget(self.plan_view)

        right_panel_widget = QWidget()
        right_panel = QVBoxLayout(right_panel_widget)
        form = QFormLayout()
        self.param_total_area = QLineEdit()
        self.param_total_area.setPlaceholderText("Например: 1500")
        self.param_employees = QSpinBox()
        self.param_employees.setRange(1, 100)
        self.param_rate = QDoubleSpinBox()
        self.param_rate.setRange(0.01, 10000)
        self.weather_combo = QComboBox()
        self.weather_combo.addItems(["Ясно (x1.0)", "Дождь (x1.2)", "Снег (x1.5)", "Сильный дождь (x1.8)"])
        form.addRow("Общая площадь (м²):", self.param_total_area)
        form.addRow("Кол-во сотрудников:", self.param_employees)
        form.addRow("Зарплата/час (руб):", self.param_rate)
        form.addRow("Погода:", self.weather_combo)
        right_panel.addLayout(form)
        right_panel.addWidget(QPushButton("Авторасчёт персонала", clicked=self.main.auto_calculate_staff))
        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line1)

        fill_layout = QHBoxLayout()
        fill_layout.addWidget(QLabel("Заливка:"))
        self.fill_mode_combo = QComboBox()
        self.fill_mode_combo.addItem("Стандартный", "standard")
        self.fill_mode_combo.addItem("По типу", "type")
        self.fill_mode_combo.currentIndexChanged.connect(self.main.update_room_opacity)
        fill_layout.addWidget(self.fill_mode_combo)
        right_panel.addLayout(fill_layout)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(30)
        self.opacity_slider.valueChanged.connect(self.main.update_room_opacity)
        right_panel.addWidget(QLabel("Прозрачность заливки"))
        right_panel.addWidget(self.opacity_slider)
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line2)

        table_header = QHBoxLayout()
        self.room_table_collapsed = False
        btn_collapse = QPushButton("▼")
        btn_collapse.setFlat(True)
        btn_collapse.setFixedWidth(28)
        btn_collapse.setToolTip("Свернуть/развернуть таблицу")
        btn_collapse.clicked.connect(self.main.toggle_room_table)
        self.btn_collapse_table = btn_collapse
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["⇅", "№", "Пл", "А-Я", "Тип"])
        self.sort_combo.setFixedWidth(52)
        self.sort_combo.setToolTip("Сортировка")
        self.sort_combo.currentIndexChanged.connect(self.main.sort_rooms)
        table_header.addWidget(self.sort_combo)
        table_header.addWidget(QLabel("<b>Комнаты</b>"))
        table_header.addStretch()
        table_header.addWidget(btn_collapse)
        right_panel.addLayout(table_header)

        self.room_table = QTableWidget(0, 4)
        self.room_table.setHorizontalHeaderLabels(["№", "Название", "Тип", "Площадь (м²)"])
        self.room_table.horizontalHeader().setStretchLastSection(True)
        self.room_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.room_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.room_table.verticalHeader().setVisible(False)
        self.room_table.verticalHeader().setDefaultSectionSize(28)
        self.room_table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.room_table.cellEntered.connect(self.main.on_room_table_hover)
        self.room_table.cellDoubleClicked.connect(self.main.on_room_table_double_clicked)
        self.room_table.itemSelectionChanged.connect(self.main.on_room_table_select)
        right_panel.addWidget(self.room_table)
        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        line3.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line3)

        right_panel.addWidget(QPushButton("Загрузить план", clicked=self.main.load_image))
        right_panel.addWidget(QPushButton("Распознать стены (CV)", clicked=self.main.detect_walls_cv))
        right_panel.addWidget(QPushButton("Удалить план", clicked=self.main.remove_image))
        right_panel.addStretch()
        splitter_main.addWidget(right_panel_widget)
        splitter_main.setStretchFactor(0, 1)
        splitter_main.setStretchFactor(1, 2)
        splitter_main.setSizes([400, 650])
        layout.addWidget(splitter_main)

        nav = QHBoxLayout()
        btn_back = QPushButton("← Назад")
        btn_back.clicked.connect(self.main.return_to_start_screen)
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.main.switch_floor)
        btn_add_floor = QPushButton("+ Этаж")
        btn_add_floor.clicked.connect(self.main.add_floor)
        self.btn_finish_floors = QPushButton("✓ Завершить разметку этажей")
        self.btn_finish_floors.setVisible(False)
        self.btn_finish_floors.clicked.connect(self.main.finish_floor_selection)
        nav.addWidget(btn_back)
        nav.addWidget(QLabel("Этаж:"))
        nav.addWidget(self.floor_combo)
        nav.addWidget(btn_add_floor)
        nav.addWidget(self.btn_finish_floors)
        nav.addStretch()
        btn_next = QPushButton("Далее →")
        btn_next.clicked.connect(self.main.go_to_zone_screen)
        nav.addWidget(btn_next)
        layout.addLayout(nav)

        self.plan_view.scene_changed.connect(self.main.on_scene_changed)
        self.plan_view.floor_rect_added.connect(self.main.on_floor_rect_added)