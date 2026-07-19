# screens/plan_screen.py
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolBar, QFormLayout,
    QSpinBox, QLineEdit, QTableWidget, QTableWidgetItem, QTextEdit,
    QDoubleSpinBox, QComboBox, QSlider, QPushButton, QFileDialog, QMessageBox,
    QGraphicsScene, QProgressBar, QDialog, QGraphicsPolygonItem,
    QGraphicsRectItem, QGraphicsLineItem, QApplication
)
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF
from PySide6.QtGui import (
    QPen, QColor, QBrush, QPolygonF, QPixmap, QFont, QAction
)
from tools import PlanView
from shapely.ops import unary_union, linemerge
from shapely.geometry import box, LineString
from project import Floor, Wall, Room
from dxf_importer import import_dxf

ROOM_COLORS = [
    (255,0,0,30), (60,180,75,30), (255,225,25,30), (0,130,200,30),
    (245,130,48,30), (145,30,180,30), (70,240,240,30), (240,50,230,30),
    (210,245,60,30), (250,190,190,30), (0,128,128,30), (230,190,255,30)
]

class PlanScreen(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.temp_dxf_walls = []   # временные стены из DXF
        self.floor_rects = []
        self._setup_ui()

    @property
    def project(self):
        return self.main_window.project

    @project.setter
    def project(self, value):
        self.main_window.project = value

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Редактор плана помещения"))

        # Панель инструментов
        toolbar = QToolBar("Инструменты")
        toolbar.addAction("💾 Сохранить", self.main_window.save_project)
        toolbar.addSeparator()
        toolbar.addAction("Выбор", lambda: self.plan_view.set_tool(0))
        toolbar.addAction("Ластик", lambda: self.plan_view.set_tool(1))
        toolbar.addAction("Линия", lambda: self.plan_view.set_tool(2))
        toolbar.addAction("Комната", lambda: self.plan_view.set_tool(4))
        toolbar.addAction("Калибровка", lambda: self.plan_view.set_tool(3))
        toolbar.addAction("Кисть", lambda: self.plan_view.set_tool(5))
        toolbar.addSeparator()
        toolbar.addAction("Загрузить DXF", self.load_dxf)
        layout.addWidget(toolbar)

        hlay = QHBoxLayout()
        self.plan_scene = QGraphicsScene()
        self.plan_view = PlanView(self.plan_scene, self.main_window)
        self.plan_view.setMinimumSize(800, 600)
        hlay.addWidget(self.plan_view)

        # Правая панель параметров
        right_panel = QVBoxLayout()
        form = QFormLayout()
        self.param_total_area = QLineEdit()
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

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(30)
        self.opacity_slider.valueChanged.connect(self.update_room_opacity)
        right_panel.addWidget(QLabel("Прозрачность заливки"))
        right_panel.addWidget(self.opacity_slider)

        right_panel.addWidget(QPushButton("Загрузить план", clicked=self.main_window.load_image))
        right_panel.addWidget(QPushButton("Распознать стены (CV)", clicked=self.main_window.detect_walls_cv))

        # Сортировка
        sort_layout = QHBoxLayout()
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Без сортировки", "По номеру", "По площади", "По алфавиту", "По типу"])
        self.sort_combo.currentIndexChanged.connect(self.main_window.sort_rooms)
        sort_layout.addWidget(QLabel("Сортировка:"))
        sort_layout.addWidget(self.sort_combo)
        right_panel.addLayout(sort_layout)

        self.room_table = QTableWidget(0, 4)
        self.room_table.setHorizontalHeaderLabels(["№", "Название", "Тип", "Площадь (м²)"])
        self.room_table.horizontalHeader().setStretchLastSection(True)
        self.room_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.room_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.room_table.verticalHeader().setVisible(False)
        self.room_table.verticalHeader().setDefaultSectionSize(28)
        self.room_table.cellEntered.connect(self.main_window.on_room_table_hover)
        self.room_table.cellDoubleClicked.connect(self.main_window.on_room_table_double_clicked)
        right_panel.addWidget(self.room_table)
        right_panel.addStretch()
        hlay.addLayout(right_panel)
        layout.addLayout(hlay)

        # Нижняя панель навигации и этажей
        nav = QHBoxLayout()
        btn_back = QPushButton("← Назад")
        btn_back.clicked.connect(lambda: self.main_window.stack.setCurrentIndex(0))
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.main_window.switch_floor)
        btn_add_floor = QPushButton("+ Этаж", clicked=self.main_window.add_floor)
        self.btn_finish_floors = QPushButton("✓ Завершить разметку этажей")
        self.btn_finish_floors.setVisible(False)
        self.btn_finish_floors.clicked.connect(self.finish_floor_selection)
        nav.addWidget(btn_back)
        nav.addWidget(QLabel("Этаж:"))
        nav.addWidget(self.floor_combo)
        nav.addWidget(btn_add_floor)
        nav.addWidget(self.btn_finish_floors)
        nav.addStretch()
        btn_next = QPushButton("Далее →")
        btn_next.clicked.connect(self.main_window.go_to_zone_screen)
        nav.addWidget(btn_next)
        layout.addLayout(nav)

        # Подключение сигналов сцены
        self.plan_view.scene_changed.connect(self.main_window.on_scene_changed)
        self.plan_view.floor_rect_added.connect(self.on_floor_rect_added)

    # --- Вспомогательные методы ---
    def update_room_opacity(self):
        self.main_window.draw_rooms()

    def update_floor_combo(self):
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        for floor in self.main_window.project.floors:
            self.floor_combo.addItem(floor.name)
        self.floor_combo.setCurrentIndex(self.main_window.project.current_floor_index)
        self.floor_combo.blockSignals(False)

    # --- Методы для работы с DXF ---
    def load_dxf(self):
        if not self.main_window.project: return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить DXF", "", "DXF Files (*.dxf)")
        if not path: return

        progress_dlg = QDialog(self)
        progress_dlg.setWindowTitle("Загрузка DXF")
        progress_dlg.setFixedSize(300, 100)
        layout = QVBoxLayout(progress_dlg)
        label = QLabel("Чтение файла...")
        layout.addWidget(label)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        layout.addWidget(progress_bar)
        progress_dlg.show()

        def update_progress(pct, msg):
            progress_bar.setValue(pct)
            label.setText(msg)
            QApplication.processEvents()

        data = import_dxf(path, progress_callback=update_progress)
        progress_dlg.close()

        if not data['walls']:
            QMessageBox.warning(self, "Ошибка",
                "Не удалось извлечь стены из DXF.\n\n"
                "Возможные причины:\n"
                "- стены не найдены (проверьте через Инспектор DXF)\n"
                "- масштаб чертежа не в миллиметрах\n\n"
                "Вы можете загрузить план как изображение и обвести стены вручную.")
            return

        self.temp_dxf_walls = data['walls']
        self.floor_rects = []

        self.show_temp_dxf_walls()

        self.plan_view.set_tool(6)  # SelectFloor
        self.btn_finish_floors.setVisible(True)
        QMessageBox.information(self, "Разметка этажей",
            "Выделите прямоугольные области для каждого этажа. Нажмите 'Завершить разметку', когда закончите.")

    def show_temp_dxf_walls(self):
        scene = self.plan_view.scene()
        # Удаляем старые временные линии
        for item in scene.items():
            if isinstance(item, QGraphicsLineItem) and item.pen().color() == QColor(128,128,128):
                scene.removeItem(item)
        for tw in self.temp_dxf_walls:
            if len(tw.polyline) >= 2:
                for i in range(len(tw.polyline)-1):
                    p1 = tw.polyline[i]
                    p2 = tw.polyline[i+1]
                    line = QGraphicsLineItem(QLineF(p1[0], p1[1], p2[0], p2[1]))
                    line.setPen(QPen(QColor(128,128,128), 1))
                    scene.addItem(line)
        rect = scene.itemsBoundingRect()
        if rect.width() > 0:
            self.plan_view.setSceneRect(rect)
            self.plan_view.fitInView(rect, Qt.KeepAspectRatio)

    def on_floor_rect_added(self, x, y, w, h):
        self.floor_rects.append((x, y, w, h))
        rect_item = QGraphicsRectItem(x, y, w, h)
        rect_item.setPen(QPen(Qt.red, 2))
        rect_item.setBrush(QBrush(QColor(255,0,0,50)))
        self.plan_scene.addItem(rect_item)

    def finish_floor_selection(self):
        if not self.floor_rects:
            QMessageBox.warning(self, "Ошибка", "Не выделено ни одного этажа.")
            return

        project = self.main_window.project
        # Преобразуем временные стены в LineString для фильтрации
        temp_segments = []
        for tw in self.temp_dxf_walls:
            pts = tw.polyline
            for i in range(len(pts)-1):
                temp_segments.append(LineString([pts[i], pts[i+1]]))

        for idx, (fx, fy, fw, fh) in enumerate(self.floor_rects):
            floor = Floor(index=len(project.floors), name=f"Этаж {len(project.floors)+1}")
            floor._builder = self.main_window.build_rooms_for_floor
            floor_box = box(fx, fy, fx+fw, fy+fh)

            # Собираем все линии, пересекающие этаж
            floor_lines = []
            for seg in temp_segments:
                if seg.intersects(floor_box):
                    intersection = seg.intersection(floor_box)
                    if not intersection.is_empty:
                        if intersection.geom_type == 'LineString':
                            floor_lines.append(intersection)
                        elif intersection.geom_type == 'MultiLineString':
                            floor_lines.extend(list(intersection.geoms))

            # Объединяем линии в непрерывные стены
            if floor_lines:
                from shapely.ops import linemerge
                merged = unary_union(floor_lines)
                merged = linemerge(merged)
                if merged.geom_type == 'LineString':
                    merged_lines = [merged]
                elif merged.geom_type == 'MultiLineString':
                    merged_lines = list(merged.geoms)
                else:
                    merged_lines = []

                # Преобразуем объединённые линии в стены
                floor_walls = []
                for line in merged_lines:
                    coords = list(line.coords)
                    for i in range(len(coords) - 1):
                        floor_walls.append(Wall(coords[i][0], coords[i][1],
                                                coords[i+1][0], coords[i+1][1]))
            else:
                floor_walls = []

            # Центрирование стен этажа
            if floor_walls:
                xs = []; ys = []
                for w in floor_walls:
                    xs.extend([w.x1, w.x2])
                    ys.extend([w.y1, w.y2])
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                center_x = (min_x + max_x) / 2
                center_y = (min_y + max_y) / 2
                for w in floor_walls:
                    w.x1 -= center_x
                    w.y1 -= center_y
                    w.x2 -= center_x
                    w.y2 -= center_y

            floor.walls = floor_walls
            # Строим комнаты из сдвинутых стен
            self.main_window.build_rooms_for_floor(floor)
            project.floors.append(floor)

        project.current_floor_index = len(project.floors) - len(self.floor_rects)
        self.update_floor_combo()
        self.plan_scene.clear()
        self.main_window.refresh_plan_view()

        # Принудительно центрируем вид на новом этаже
        rect = self.plan_scene.itemsBoundingRect()
        if rect.width() > 0:
            self.plan_view.fitInView(rect, Qt.KeepAspectRatio)

        self.btn_finish_floors.setVisible(False)
        self.floor_rects = []
        self.temp_dxf_walls = []
        self.plan_view.set_tool(0)