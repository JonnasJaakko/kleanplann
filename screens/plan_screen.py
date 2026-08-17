"""Редактор плана: компактный основной рабочий экран проекта."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolBar,
    QLineEdit, QSpinBox, QComboBox, QSlider, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QSplitter, QDialog, QFormLayout, QDoubleSpinBox,
    QDialogButtonBox, QMessageBox, QTimeEdit, QGraphicsScene
)
from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor

from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_TRAFFIC_PER_TYPE
from tools import PlanView

ROOM_COLORS = [
    (255,0,0,30), (60,180,75,30), (255,225,25,30), (0,130,200,30),
    (245,130,48,30), (145,30,180,30), (70,240,240,30), (240,50,230,30),
    (210,245,60,30), (250,190,190,30), (0,128,128,30), (230,190,255,30)
]


class ProjectSettingsDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        p = main_window.project
        self.setWindowTitle("Настройки проекта")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.total_area = QDoubleSpinBox()
        self.total_area.setRange(0, 10_000_000)
        self.total_area.setDecimals(1)
        self.total_area.setSuffix(" м²")
        self.total_area.setValue(float(getattr(p, 'total_area_m2', 0.0)))

        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.01, 1_000_000)
        self.rate.setDecimals(2)
        self.rate.setSuffix(" руб/ч")
        self.rate.setValue(float(getattr(p, 'hourly_rate', 200.0)))

        self.premium = QDoubleSpinBox()
        self.premium.setRange(0, 500)
        self.premium.setDecimals(1)
        self.premium.setSuffix(" %")
        self.premium.setValue(float(getattr(p, 'overtime_premium_percent', 50.0)))

        self.cleaning_type = QComboBox()
        self.cleaning_type.addItems(["поддерживающая", "генеральная"])
        self.cleaning_type.setCurrentText(getattr(p, 'cleaning_type', 'поддерживающая'))

        self.weather = QComboBox()
        self.weather.addItems(["Ясно (x1.0)", "Дождь (x1.2)", "Снег (x1.5)", "Сильный дождь (x1.8)"])
        factor = float(getattr(p, 'weather_factor', 1.0))
        idx = {1.0: 0, 1.2: 1, 1.5: 2, 1.8: 3}.get(factor, 0)
        self.weather.setCurrentIndex(idx)

        self.shift_start = QTimeEdit(QTime.fromString(p.shifts[0].start_time if p.shifts else "08:00", "HH:mm"))
        self.shift_start.setDisplayFormat("HH:mm")
        self.shift_end = QTimeEdit(QTime.fromString(p.shifts[0].end_time if p.shifts else "17:00", "HH:mm"))
        self.shift_end.setDisplayFormat("HH:mm")
        self.lunch_start = QTimeEdit(QTime.fromString(p.breaks[0][0] if p.breaks else "12:00", "HH:mm"))
        self.lunch_start.setDisplayFormat("HH:mm")
        self.lunch_end = QTimeEdit(QTime.fromString(p.breaks[0][1] if p.breaks else "13:00", "HH:mm"))
        self.lunch_end.setDisplayFormat("HH:mm")

        form.addRow("Общая площадь:", self.total_area)
        form.addRow("Зарплата в час:", self.rate)
        form.addRow("Надбавка за переработку:", self.premium)
        form.addRow("Тип уборки:", self.cleaning_type)
        form.addRow("Погода:", self.weather)
        form.addRow("Смена с:", self.shift_start)
        form.addRow("Смена до:", self.shift_end)
        form.addRow("Обед с:", self.lunch_start)
        form.addRow("Обед до:", self.lunch_end)
        layout.addLayout(form)

        hint = QLabel("При генеральной уборке коэффициенты трудоёмкости удваиваются.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        delete_btn = QPushButton("Удалить план")
        delete_btn.setStyleSheet("color:#b00020;")
        delete_btn.clicked.connect(self._delete_plan)
        layout.addWidget(delete_btn)

    def _delete_plan(self):
        if QMessageBox.question(self, "Удалить план", "Удалить загруженный план, комнаты, зоны и расписание?") == QMessageBox.Yes:
            self.main.delete_plan_data()
            self.accept()

    def values(self):
        weather_text = self.weather.currentText()
        if "1.2" in weather_text: factor = 1.2
        elif "1.5" in weather_text: factor = 1.5
        elif "1.8" in weather_text: factor = 1.8
        else: factor = 1.0
        return {
            "total_area": self.total_area.value(),
            "rate": self.rate.value(),
            "premium": self.premium.value(),
            "cleaning_type": self.cleaning_type.currentText(),
            "weather_factor": factor,
            "shift_start": self.shift_start.time().toString("HH:mm"),
            "shift_end": self.shift_end.time().toString("HH:mm"),
            "lunch_start": self.lunch_start.time().toString("HH:mm"),
            "lunch_end": self.lunch_end.time().toString("HH:mm"),
        }


class PlanScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        title = QLabel("Редактор плана помещения")
        title.setFixedHeight(30)
        title.setAlignment(Qt.AlignCenter)
        header.addWidget(title)
        header.addStretch()
        help_btn = QPushButton("❓ Справка")
        help_btn.clicked.connect(self.main.show_help)
        header.addWidget(help_btn)
        layout.addLayout(header)

        toolbar = QToolBar("Инструменты")
        toolbar.addAction("💾 Сохранить", self.main.save_project)
        toolbar.addAction("⚙ Настройки проекта", self.main.open_project_settings)
        toolbar.addSeparator()
        toolbar.addAction("Выбор", lambda: self.plan_view.set_tool(0))
        toolbar.addAction("Ластик", lambda: self.plan_view.set_tool(1))
        toolbar.addAction("Линия", lambda: self.plan_view.set_tool(2))
        toolbar.addAction("Комната", lambda: self.plan_view.set_tool(4))
        toolbar.addAction("Калибровка", lambda: self.plan_view.set_tool(3))
        toolbar.addAction("Кисть", lambda: self.plan_view.set_tool(5))
        toolbar.addSeparator()
        toolbar.addAction("📐 Загрузить план", self.main.load_plan_universal)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.plan_scene = QGraphicsScene()
        self.plan_view = PlanView(self.plan_scene, self.main)
        splitter.addWidget(self.plan_view)

        right = QWidget()
        rp = QVBoxLayout(right)
        rp.addWidget(QLabel("<b>Параметры расчёта</b>"))
        emp_row = QHBoxLayout()
        emp_row.addWidget(QLabel("Сотрудников:"))
        self.param_employees = QSpinBox()
        self.param_employees.setRange(1, 100)
        emp_row.addWidget(self.param_employees)
        rp.addLayout(emp_row)
        rp.addWidget(QPushButton("Авторасчёт персонала", clicked=self.main.auto_calculate_staff))

        line = QFrame(); line.setFrameShape(QFrame.HLine); rp.addWidget(line)
        fill = QHBoxLayout()
        fill.addWidget(QLabel("Заливка:"))
        self.fill_mode_combo = QComboBox()
        self.fill_mode_combo.addItem("Стандартный", "standard")
        self.fill_mode_combo.addItem("По типу", "type")
        self.fill_mode_combo.currentIndexChanged.connect(self.main.update_room_opacity)
        fill.addWidget(self.fill_mode_combo)
        rp.addLayout(fill)
        rp.addWidget(QLabel("Прозрачность заливки"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255); self.opacity_slider.setValue(30)
        self.opacity_slider.valueChanged.connect(self.main.update_room_opacity)
        rp.addWidget(self.opacity_slider)

        line = QFrame(); line.setFrameShape(QFrame.HLine); rp.addWidget(line)
        table_head = QHBoxLayout()
        self.sort_combo = QComboBox(); self.sort_combo.addItems(["⇅", "№", "Пл", "А-Я", "Тип"]); self.sort_combo.setFixedWidth(52)
        self.sort_combo.currentIndexChanged.connect(self.main.sort_rooms)
        table_head.addWidget(self.sort_combo); table_head.addWidget(QLabel("<b>Комнаты</b>")); table_head.addStretch()
        self.btn_collapse_table = QPushButton("▼"); self.btn_collapse_table.setFixedWidth(28); self.btn_collapse_table.setFlat(True)
        self.btn_collapse_table.clicked.connect(self.main.toggle_room_table)
        table_head.addWidget(self.btn_collapse_table)
        rp.addLayout(table_head)
        self.room_table = QTableWidget(0, 4)
        self.room_table.setHorizontalHeaderLabels(["№", "Название", "Тип", "Площадь (м²)"])
        self.room_table.horizontalHeader().setStretchLastSection(True)
        self.room_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.room_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.room_table.verticalHeader().setVisible(False)
        self.room_table.cellEntered.connect(self.main.on_room_table_hover)
        self.room_table.cellDoubleClicked.connect(self.main.on_room_table_double_clicked)
        self.room_table.itemSelectionChanged.connect(self.main.on_room_table_select)
        rp.addWidget(self.room_table)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        bottom = QHBoxLayout()
        back = QPushButton("← Назад"); back.clicked.connect(self.main.return_to_start_screen)
        bottom.addWidget(back)
        bottom.addWidget(QLabel("Этаж:"))
        self.floor_combo = QComboBox(); self.floor_combo.currentIndexChanged.connect(self.main.switch_floor)
        bottom.addWidget(self.floor_combo)
        add_floor = QPushButton("+ Этаж"); add_floor.clicked.connect(self.main.add_floor); bottom.addWidget(add_floor)
        self.btn_finish_floors = QPushButton("✓ Завершить разметку этажей"); self.btn_finish_floors.setVisible(False)
        self.btn_finish_floors.clicked.connect(self.main.finish_floor_selection); bottom.addWidget(self.btn_finish_floors)
        bottom.addStretch()
        create = QPushButton("Создать расписание →")
        create.setMinimumHeight(38)
        create.clicked.connect(self.main.go_to_planning_screen)
        bottom.addWidget(create)
        layout.addLayout(bottom)

        self.plan_view.scene_changed.connect(self.main.on_scene_changed)
        self.plan_view.floor_rect_added.connect(self.main.on_floor_rect_added)
