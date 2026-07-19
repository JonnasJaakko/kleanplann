import sys, os, json, glob, math
from collections import Counter, defaultdict
import ezdxf
from datetime import datetime, date, timedelta
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsPolygonItem, QGraphicsLineItem, QGraphicsEllipseItem,
    QGraphicsTextItem, QGraphicsItem, QToolBar, QInputDialog, QFormLayout,
    QSpinBox, QLineEdit, QTableWidget, QTableWidgetItem, QTextEdit,
    QDoubleSpinBox, QDialog, QDialogButtonBox, QSlider, QComboBox,
    QToolTip, QHeaderView, QAbstractItemView, QGraphicsRectItem,
    QProgressBar
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QLineF
from PySide6.QtGui import (
    QPixmap, QPen, QColor, QBrush, QPolygonF, QPainter, QCursor,
    QFont, QAction
)
import numpy as np
from shapely.geometry import LineString, box

from project import Project, Wall, Room, Floor, Zone, CleaningTask
from room_builder import build_rooms_from_walls, nearest_point_on_segment, split_walls_at_intersections
from zone_manager import manual_distribution
from cost_calculator import calculate_cost
from report_generator import generate_report
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY
from scheduler import plan_cleaning_schedule
from calendar_export import export_tasks_csv, export_tasks_excel
from dxf_importer import import_dxf
from tools import PlanView, WallSegmentItem, WallVertexItem, UndoStack

PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)

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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KleanPlann - Планировщик уборки")
        self.project = None
        self.current_project_path = None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.start_screen = QWidget(); self.setup_start_screen(); self.stack.addWidget(self.start_screen)
        self.plan_screen = QWidget(); self.setup_plan_screen(); self.stack.addWidget(self.plan_screen)
        self.zone_screen = QWidget(); self.setup_zone_screen(); self.stack.addWidget(self.zone_screen)
        self.report_screen = QWidget(); self.setup_report_screen(); self.stack.addWidget(self.report_screen)
        self.norms_screen = QWidget(); self.setup_norms_screen(); self.stack.addWidget(self.norms_screen)
        self.stack.setCurrentIndex(0)

        self.temp_dxf_walls = []
        self.floor_rects = []
        self.is_large_project = False

    # ---------- Стартовый экран ----------
    def setup_start_screen(self):
        layout = QVBoxLayout(self.start_screen)
        layout.addWidget(QLabel("Добро пожаловать в KleanPlann"))
        layout.addWidget(QLabel("Недавние проекты:"))
        self.project_list = QListWidget(); layout.addWidget(self.project_list)
        btn_new = QPushButton("Новый проект"); btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Открыть проект"); btn_open.clicked.connect(self.open_project)
        btn_norms = QPushButton("Нормативы СанПиН"); btn_norms.clicked.connect(self.go_to_norms_screen)
        btn_inspect = QPushButton("Инспектор DXF"); btn_inspect.clicked.connect(self.inspect_dxf_file)
        layout.addWidget(btn_new); layout.addWidget(btn_open); layout.addWidget(btn_norms); layout.addWidget(btn_inspect)
        self.refresh_project_list()

    def refresh_project_list(self):
        self.project_list.clear()
        files = glob.glob(os.path.join(PROJECTS_DIR, "*.json"))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:10]:
            name = os.path.basename(f).replace('.json','')
            item = QListWidgetItem(name); item.setData(Qt.UserRole, f)
            self.project_list.addItem(item)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Название помещения:")
        if ok and name:
            self.project = Project(name); self.current_project_path = None
            self.load_plan_screen(); self.stack.setCurrentIndex(1)

    def open_project(self):
        item = self.project_list.currentItem()
        if item:
            path = item.data(Qt.UserRole); self.project = Project.load_from_file(path)
            self.current_project_path = path
            self.load_plan_screen(); self.stack.setCurrentIndex(1)
        else: QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")

    def go_to_norms_screen(self):
        self.load_norms_screen(); self.stack.setCurrentIndex(4)

    # ---------- Экран плана ----------
    def setup_plan_screen(self):
        layout = QVBoxLayout(self.plan_screen)
        layout.addWidget(QLabel("Редактор плана помещения"))
        toolbar = QToolBar("Инструменты")
        self.plan_scene = QGraphicsScene()
        self.plan_view = PlanView(self.plan_scene, self)
        self.plan_view.setMinimumSize(800, 600)

        toolbar.addAction("💾 Сохранить", self.save_project)
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
        hlay.addWidget(self.plan_view)

        right_panel = QVBoxLayout()
        form = QFormLayout()
        self.param_total_area = QLineEdit(); self.param_employees = QSpinBox(); self.param_employees.setRange(1,100)
        self.param_rate = QDoubleSpinBox(); self.param_rate.setRange(0.01,10000)
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

        right_panel.addWidget(QPushButton("Загрузить план", clicked=self.load_image))
        right_panel.addWidget(QPushButton("Распознать стены (CV)", clicked=self.detect_walls_cv))

        sort_layout = QHBoxLayout()
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Без сортировки", "По номеру", "По площади", "По алфавиту", "По типу"])
        self.sort_combo.currentIndexChanged.connect(self.sort_rooms)
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
        self.room_table.cellEntered.connect(self.on_room_table_hover)
        self.room_table.cellDoubleClicked.connect(self.on_room_table_double_clicked)
        right_panel.addWidget(self.room_table)
        right_panel.addStretch()
        hlay.addLayout(right_panel)
        layout.addLayout(hlay)

        nav = QHBoxLayout()
        btn_back = QPushButton("← Назад"); btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.switch_floor)
        btn_add_floor = QPushButton("+ Этаж"); btn_add_floor.clicked.connect(self.add_floor)
        self.btn_finish_floors = QPushButton("✓ Завершить разметку этажей")
        self.btn_finish_floors.setVisible(False)
        self.btn_finish_floors.clicked.connect(self.finish_floor_selection)
        nav.addWidget(btn_back)
        nav.addWidget(QLabel("Этаж:"))
        nav.addWidget(self.floor_combo)
        nav.addWidget(btn_add_floor)
        nav.addWidget(self.btn_finish_floors)
        nav.addStretch()
        btn_next = QPushButton("Далее →"); btn_next.clicked.connect(self.go_to_zone_screen)
        nav.addWidget(btn_next)
        layout.addLayout(nav)

        self.plan_view.scene_changed.connect(self.on_scene_changed)
        self.plan_view.floor_rect_added.connect(self.on_floor_rect_added)

    def load_image(self):
        if not self.project: return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить план", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.project.image_paths = [path]; self.refresh_plan_view()

    def load_dxf(self):
        if not self.project: return
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

        max_temp_walls = 10000
        if len(self.temp_dxf_walls) > max_temp_walls:
            QMessageBox.warning(self, "Предупреждение",
                f"Слишком много стен ({len(self.temp_dxf_walls)}). Будут обработаны первые {max_temp_walls}.")
            self.temp_dxf_walls = self.temp_dxf_walls[:max_temp_walls]

        all_segments = []
        for tw in self.temp_dxf_walls:
            pts = tw.polyline
            for i in range(len(pts)-1):
                all_segments.append(LineString([pts[i], pts[i+1]]))

        for idx, (fx, fy, fw, fh) in enumerate(self.floor_rects):
            floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors)+1}")
            floor_box = box(fx, fy, fx+fw, fy+fh)

            floor_lines = []
            for seg in all_segments:
                if seg.bounds[2] < fx or seg.bounds[0] > fx+fw or \
                   seg.bounds[3] < fy or seg.bounds[1] > fy+fh:
                    continue
                if seg.intersects(floor_box):
                    intersection = seg.intersection(floor_box)
                    if not intersection.is_empty:
                        if intersection.geom_type == 'LineString':
                            floor_lines.append(intersection)
                        elif intersection.geom_type == 'MultiLineString':
                            floor_lines.extend(list(intersection.geoms))

            if len(floor_lines) > 5000:
                floor_lines = floor_lines[:5000]

            for line in floor_lines:
                coords = list(line.coords)
                if len(coords) >= 2:
                    floor.walls.append(Wall(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1]))

            self.build_rooms_for_floor(floor)
            self.project.floors.append(floor)

        self.project.current_floor_index = len(self.project.floors) - len(self.floor_rects)
        self.update_floor_combo()
        self.plan_scene.clear()
        self.refresh_plan_view()
        self.btn_finish_floors.setVisible(False)
        self.floor_rects = []
        self.temp_dxf_walls = []
        self.plan_view.set_tool(0)

    def build_rooms_for_floor(self, floor):
        if not floor.walls:
            floor.rooms = []
            return
        walls_list = [(w.x1, w.y1, w.x2, w.y2) for w in floor.walls]
        if len(floor.walls) < 1000:
            walls_list = split_walls_at_intersections(walls_list)
        else:
            from shapely.ops import unary_union
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                lines = [LineString([(x1,y1), (x2,y2)]) for x1,y1,x2,y2 in walls_list]
                merged = unary_union(lines)
                simplified = merged.simplify(0.2, preserve_topology=True)
                if simplified.geom_type == 'LineString':
                    coords = list(simplified.coords)
                    walls_list = [(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) for i in range(len(coords)-1)]
                elif simplified.geom_type == 'MultiLineString':
                    walls_list = []
                    for line in simplified.geoms:
                        coords = list(line.coords)
                        walls_list.extend([(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) for i in range(len(coords)-1)])
        polygons = build_rooms_from_walls(walls_list)
        if not polygons:
            floor.rooms = []
            return
        new_rooms = []
        for i, pts in enumerate(polygons):
            color = ROOM_COLORS[i % len(ROOM_COLORS)]
            new_rooms.append(Room(i, pts, color=color))
        floor.rooms = new_rooms
        floor.total_area_m2 = sum(self._polygon_area(r.points) for r in new_rooms)

    def add_floor(self):
        if not self.project: return
        floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors)+1}")
        self.project.floors.append(floor)
        self.project.current_floor_index = len(self.project.floors)-1
        self.update_floor_combo()
        self.param_total_area.setText("0")
        self.refresh_plan_view()

    def update_floor_combo(self):
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        for floor in self.project.floors:
            self.floor_combo.addItem(floor.name)
        self.floor_combo.setCurrentIndex(self.project.current_floor_index)
        self.floor_combo.blockSignals(False)

    def switch_floor(self, idx):
        if idx >= 0 and idx < len(self.project.floors):
            self.project.current_floor_index = idx
            floor = self.project.current_floor
            self.param_total_area.setText(str(floor.total_area_m2))
            self.refresh_plan_view()

    def load_plan_screen(self):
        self.param_total_area.setText(str(self.project.current_floor.total_area_m2))
        self.param_employees.setValue(self.project.employees_count)
        self.param_rate.setValue(self.project.hourly_rate)
        self.weather_combo.setCurrentIndex(0)
        self.opacity_slider.setValue(30)
        self.update_floor_combo()
        self.refresh_plan_view()

    def refresh_plan_view(self):
        scene = self.plan_view.scene(); scene.clear()
        if self.project and self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0]); scene.addPixmap(pix)
            self.plan_view.setSceneRect(QRectF(pix.rect()))
        else:
            self.plan_view.setSceneRect(0,0,800,600)
            text = scene.addText("Загрузите изображение плана"); text.setPos(400,300)
        for wall in self.project.walls:
            seg = WallSegmentItem(QPointF(wall.x1, wall.y1), QPointF(wall.x2, wall.y2))
            scene.addItem(seg)
        self.draw_rooms()
        self.update_room_table()
        rect = scene.itemsBoundingRect()
        if rect.width() > 0:
            self.plan_view.fitInView(rect, Qt.KeepAspectRatio)

    def draw_rooms(self):
        scene = self.plan_view.scene()
        for item in scene.items():
            if (isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None) or \
               (isinstance(item, QGraphicsTextItem) and item.data(1) == "room_label"):
                scene.removeItem(item)
        alpha = self.opacity_slider.value()
        for room in self.project.rooms:
            col = QColor(*room.color[:3], alpha)
            brush = QBrush(col)
            pen = QPen(Qt.black, 1)
            poly = QPolygonF([QPointF(x,y) for x,y in room.points])
            item = scene.addPolygon(poly, pen, brush)
            item.setData(Qt.UserRole, room.id)
            cx = sum(p[0] for p in room.points)/len(room.points)
            cy = sum(p[1] for p in room.points)/len(room.points)
            bg_color = TYPE_COLORS.get(room.room_type, QColor(Qt.red)) if room.room_type else QColor(Qt.red)
            label_text = room.name
            text_item = QGraphicsTextItem()
            text_item.setHtml(f"<div style='text-align:center; background-color:{bg_color.name()}; color:white; padding:2px; border:1px solid black;'>{label_text}</div>")
            text_item.setPos(cx - 30, cy - 10)
            text_item.setData(1, "room_label")
            text_item.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            scene.addItem(text_item)

    def update_room_table(self):
        self.room_table.setRowCount(0)
        if not self.project or not self.project.rooms:
            return
        for room in self.project.rooms:
            row = self.room_table.rowCount()
            self.room_table.insertRow(row)
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(f"{room.area_m2:.1f}"))
            self.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def on_room_table_hover(self, row, col):
        item = self.room_table.item(row, 0)
        if item:
            room_id = item.data(Qt.UserRole)
            self.plan_view.highlight_room(room_id)

    def on_room_table_double_clicked(self, row, col):
        item = self.room_table.item(row, 0)
        if item:
            room_id = item.data(Qt.UserRole)
            self.edit_room_properties(room_id)

    def sort_rooms(self):
        criteria = self.sort_combo.currentText()
        if not self.project or not self.project.rooms:
            return
        rooms = self.project.rooms[:]
        if criteria == "По номеру": rooms.sort(key=lambda r: r.id)
        elif criteria == "По площади": rooms.sort(key=lambda r: r.area_m2, reverse=True)
        elif criteria == "По алфавиту": rooms.sort(key=lambda r: r.name.lower())
        elif criteria == "По типу": rooms.sort(key=lambda r: r.room_type if r.room_type else "яя")
        else: return
        self.room_table.setRowCount(0)
        for room in rooms:
            row = self.room_table.rowCount()
            self.room_table.insertRow(row)
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(f"{room.area_m2:.1f}"))
            self.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def update_room_opacity(self):
        self.draw_rooms()

    def on_scene_changed(self):
        self.project.walls = self.plan_view.collect_walls()
        self.build_rooms_from_project_walls()
        # Автоматически применяем общую площадь, если она задана
        if self.param_total_area.text():
            self._scale_rooms()
        self.draw_rooms()
        self.update_room_table()

    def build_rooms_from_project_walls(self):
        self.build_rooms_for_floor(self.project.current_floor)

    def detect_walls_cv(self):
        if not self.project or not self.project.image_paths:
            QMessageBox.warning(self, "Ошибка", "Загрузите изображение.")
            return
        try:
            from image_processor import load_image, detect_walls as detect_walls_cv_func
            img = load_image(self.project.image_paths[0])
            contours = detect_walls_cv_func(img)
            self.project.rooms = []
            self.project.walls = []
            for i, pts in enumerate(contours):
                color = ROOM_COLORS[i % len(ROOM_COLORS)]
                self.project.rooms.append(Room(i, pts, color=color))
                for j in range(len(pts)):
                    x1,y1 = pts[j]; x2,y2 = pts[(j+1)%len(pts)]
                    self.project.walls.append(Wall(x1,y1,x2,y2))
            self._scale_rooms()
            self.refresh_plan_view()
            QMessageBox.information(self, "Готово", f"Распознано {len(contours)} комнат(ы).")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка CV", str(e))

    def _scale_rooms(self):
        total_area = float(self.param_total_area.text() or 0)
        if total_area <= 0:
            return
        if self.project.calibration_line:
            from image_processor import calibrate_scale
            scale = calibrate_scale(self.project.calibration_line)
            total_calc = 0
            for room in self.project.rooms:
                px_area = self._polygon_area(room.points)
                room.area_m2 = px_area * scale * scale
                total_calc += room.area_m2
            if total_calc > 0:
                factor = total_area / total_calc
                for room in self.project.rooms: room.area_m2 *= factor
        else:
            if self.project.rooms:
                total_px = sum(self._polygon_area(r.points) for r in self.project.rooms)
                if total_px > 0:
                    factor = total_area / total_px
                    for room in self.project.rooms:
                        room.area_m2 = self._polygon_area(room.points) * factor
                else:
                    area_per = total_area / len(self.project.rooms)
                    for room in self.project.rooms: room.area_m2 = area_per
        current_floor = self.project.current_floor
        current_floor.total_area_m2 = total_area  # сохраняем введённое значение

    def _polygon_area(self, points):
        n = len(points); area = 0.0
        for i in range(n):
            x1,y1 = points[i]; x2,y2 = points[(i+1)%n]
            area += x1*y2 - x2*y1
        return abs(area)/2.0

    def straighten_walls(self):
        if not self.project: return
        changed = False
        for wall in self.project.walls:
            x1, y1, x2, y2 = wall.x1, wall.y1, wall.x2, wall.y2
            if abs(x1 - x2) < 1e-6: continue
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
            if min(abs(angle - 0), abs(angle - 180)) < 5:
                wall.y2 = wall.y1; changed = True
            elif abs(angle - 90) < 5:
                wall.x2 = wall.x1; changed = True
        if changed:
            self.refresh_plan_view()
            QMessageBox.information(self, "Калибровка", "Стены выровнены.")

    def edit_room_properties(self, room_id):
        room = next((r for r in self.project.rooms if r.id == room_id), None)
        if not room: return
        dlg = QDialog(self); dlg.setWindowTitle(f"Редактирование комнаты")
        layout = QFormLayout(dlg)
        name_edit = QLineEdit(room.name)
        num_spin = QSpinBox(); num_spin.setRange(1, 999); num_spin.setValue(room.id+1)
        area_spin = QDoubleSpinBox(); area_spin.setRange(0, 100000); area_spin.setValue(room.area_m2)
        traffic_spin = QSpinBox(); traffic_spin.setRange(0, 10000); traffic_spin.setValue(room.traffic)
        type_combo = QComboBox(); type_combo.addItems([""] + list(COMPLEXITY_FACTOR.keys()))
        type_combo.setCurrentText(room.room_type)
        layout.addRow("Название:", name_edit)
        layout.addRow("Номер:", num_spin)
        layout.addRow("Площадь (м²):", area_spin)
        layout.addRow("Проходимость (чел/ч):", traffic_spin)
        layout.addRow("Тип:", type_combo)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        layout.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            new_num = num_spin.value()
            if any(r.id == new_num-1 and r != room for r in self.project.rooms):
                QMessageBox.warning(self, "Ошибка", "Номер уже используется.")
                return
            room.id = new_num - 1
            room.name = name_edit.text() or f"Комната {room.id+1}"
            room.area_m2 = area_spin.value()
            room.traffic = traffic_spin.value()
            room.room_type = type_combo.currentText()
            self.draw_rooms()
            self.update_room_table()

    def go_to_zone_screen(self):
        if not self.project: return
        self.project.total_area_m2 = float(self.param_total_area.text() or 0)
        self.project.employees_count = self.param_employees.value()
        self.project.hourly_rate = self.param_rate.value()
        weather_text = self.weather_combo.currentText()
        if "1.2" in weather_text: self.project.weather_factor = 1.2
        elif "1.5" in weather_text: self.project.weather_factor = 1.5
        elif "1.8" in weather_text: self.project.weather_factor = 1.8
        else: self.project.weather_factor = 1.0
        self._scale_rooms()
        all_rooms = self.project.all_rooms()
        if not all_rooms:
            QMessageBox.warning(self, "Ошибка", "Нет комнат ни на одном этаже.")
            return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Не задана площадь комнат. Укажите общую площадь или выполните калибровку.")
            return
        while len(self.project.employee_names) < self.project.employees_count:
            self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names)+1}")
        self.project.employee_names = self.project.employee_names[:self.project.employees_count]
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.load_zone_screen()
        self.stack.setCurrentIndex(2)

    # ---------- Экран зон ----------
    def setup_zone_screen(self):
        layout = QVBoxLayout(self.zone_screen)
        layout.addWidget(QLabel("Распределение зон ответственности"))
        top = QHBoxLayout()
        self.zone_scene = QGraphicsScene()
        self.zone_view = QGraphicsView(self.zone_scene)
        self.zone_view.setMinimumSize(600, 400)
        self.zone_view.wheelEvent = lambda ev: self.zone_view.scale(1.15 if ev.angleDelta().y()>0 else 1/1.15, 1.15 if ev.angleDelta().y()>0 else 1/1.15)
        top.addWidget(self.zone_view, 2)
        ctrl = QVBoxLayout()
        ctrl.addWidget(QLabel("Сотрудники:"))
        self.employee_list_widget = QListWidget()
        self.employee_list_widget.setDragDropMode(QListWidget.InternalMove)
        self.employee_list_widget.setDefaultDropAction(Qt.MoveAction)
        ctrl.addWidget(self.employee_list_widget)
        ctrl.addWidget(QPushButton("Добавить сотрудника", clicked=self.add_employee))
        ctrl.addWidget(QPushButton("Пересчитать зоны", clicked=self.recalculate_zones))
        self.zone_floor_combo = QComboBox()
        self.zone_floor_combo.addItem("Все этажи")
        self.zone_floor_combo.currentIndexChanged.connect(self.refresh_zone_display)
        ctrl.addWidget(QLabel("Этаж:"))
        ctrl.addWidget(self.zone_floor_combo)
        top.addLayout(ctrl)
        layout.addLayout(top)
        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.stack.setCurrentIndex(1)))
        nav.addStretch()
        nav.addWidget(QPushButton("Далее →", clicked=self.go_to_planning_screen))
        layout.addLayout(nav)

    def load_zone_screen(self):
        scene = self.zone_view.scene(); scene.clear()
        if self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0]); scene.addPixmap(pix)
            self.zone_view.setSceneRect(QRectF(pix.rect()))
        self.employee_list_widget.clear()
        for i in range(self.project.employees_count):
            name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i+1}"
            item = QListWidgetItem()
            widget = QWidget()
            vbox = QVBoxLayout(widget)
            vbox.setContentsMargins(4,2,4,2)
            name_btn = QPushButton(name)
            name_btn.setFlat(True)
            name_btn.clicked.connect(lambda checked=False, idx=i: self.rename_employee(idx))
            h_name = QHBoxLayout()
            h_name.addWidget(name_btn)
            btn_del = QPushButton("✕"); btn_del.setFixedSize(24,24)
            btn_del.clicked.connect(lambda checked=False, it=item: self.remove_employee(it))
            h_name.addWidget(btn_del)
            vbox.addLayout(h_name)
            info_label = QLabel("")
            info_label.setWordWrap(True)
            vbox.addWidget(info_label)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.UserRole, i)
            item.info_label = info_label
            item.name_btn = name_btn
            item.widget_ref = widget
            self.employee_list_widget.addItem(item)
            self.employee_list_widget.setItemWidget(item, widget)
        self.recalculate_zones()
        self.zone_floor_combo.blockSignals(True)
        self.zone_floor_combo.clear()
        self.zone_floor_combo.addItem("Все этажи")
        for floor in self.project.floors:
            self.zone_floor_combo.addItem(floor.name)
        self.zone_floor_combo.setCurrentIndex(0)
        self.zone_floor_combo.blockSignals(False)

    def rename_employee(self, index):
        current_name = self.project.employee_names[index] if index < len(self.project.employee_names) else ""
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Имя сотрудника:", text=current_name)
        if ok and new_name:
            self.project.employee_names[index] = new_name
            item = self.employee_list_widget.item(index)
            if item and hasattr(item, 'name_btn'):
                item.name_btn.setText(new_name)

    def add_employee(self):
        self.project.employees_count += 1
        self.project.employee_names.append(f"Сотрудник {self.project.employees_count}")
        self.load_zone_screen()

    def remove_employee(self, item):
        row = self.employee_list_widget.row(item)
        if row >= 0:
            self.employee_list_widget.takeItem(row)
            del self.project.employee_names[row]
            self.project.employees_count -= 1
            self.recalculate_zones()

    def recalculate_zones(self):
        all_rooms = self.project.all_rooms()
        if not all_rooms: return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Сначала задайте площадь.")
            return
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.refresh_zone_display()
        self.update_employee_labels()

    def refresh_zone_display(self):
        scene = self.zone_view.scene()
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)
        selected_floor = self.zone_floor_combo.currentText()
        for zone in self.project.zones:
            col = QColor(*zone.color); brush = QBrush(col); pen = QPen(Qt.black, 1)
            for rid in zone.room_ids:
                room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                if room:
                    room_floor = None
                    for i, floor in enumerate(self.project.floors):
                        if room in floor.rooms:
                            room_floor = floor.name
                            break
                    if selected_floor != "Все этажи" and room_floor != selected_floor:
                        continue
                    poly = QPolygonF([QPointF(x,y) for x,y in room.points])
                    item = scene.addPolygon(poly, pen, brush)
                    item.setData(Qt.UserRole, zone.id)
                    cx = sum(p[0] for p in room.points)/len(room.points)
                    cy = sum(p[1] for p in room.points)/len(room.points)
                    text = QGraphicsTextItem()
                    text.setHtml(f"<div style='text-align:center; background-color:{col.name()}; color:white; padding:2px; border:1px solid black;'>{zone.employee_index+1}</div>")
                    text.setPos(cx-12, cy-10)
                    text.setData(1, "zone_label")
                    text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                    text.setAcceptHoverEvents(True)
                    text.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(ev.screenPos(), self._get_schedule_tip(rid, emp))
                    text.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                    text.mousePressEvent = lambda ev, rid=rid: self.change_room_employee(rid)
                    scene.addItem(text)

    def _get_schedule_tip(self, room_id, emp_idx):
        if not hasattr(self.project, 'cleaning_tasks') or not self.project.cleaning_tasks:
            return "Расписание не сгенерировано"
        tasks = [t for t in self.project.cleaning_tasks if t.room_id == room_id and t.employee == emp_idx]
        if not tasks: return "Нет назначенных уборок"
        lines = []
        for t in sorted(tasks, key=lambda x: x.start_dt)[:10]:
            lines.append(f"{t.start_dt.strftime('%H:%M')} - {t.end_dt.strftime('%H:%M')}")
        return "\n".join(lines)

    def change_room_employee(self, room_id):
        emp_list = [self.project.employee_names[i] for i in range(self.project.employees_count)]
        current = next((z.employee_index for z in self.project.zones if room_id in z.room_ids), 0)
        item, ok = QInputDialog.getItem(self, "Сменить сотрудника", "Выберите:", emp_list, current, False)
        if ok:
            new_emp = emp_list.index(item)
            for z in self.project.zones:
                if room_id in z.room_ids: z.room_ids.remove(room_id)
            for z in self.project.zones:
                if z.employee_index == new_emp:
                    z.room_ids.append(room_id)
                    break
            self.refresh_zone_display()
            self.update_employee_labels()

    def update_employee_labels(self):
        for i in range(self.employee_list_widget.count()):
            item = self.employee_list_widget.item(i)
            if hasattr(item, 'info_label'):
                zones = [z for z in self.project.zones if z.employee_index == i]
                total_area = 0.0
                room_details = []
                for z in zones:
                    for rid in z.room_ids:
                        room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                        if room:
                            total_area += room.area_m2
                            # Определяем этаж
                            floor_name = "?"
                            for f_idx, floor in enumerate(self.project.floors):
                                if room in floor.rooms:
                                    floor_name = floor.name
                                    break
                            type_str = f" ({room.room_type})" if room.room_type else ""
                            room_details.append(f"- [{floor_name}] {room.name}{type_str} {room.area_m2:.1f} м²")
                name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i+1}"
                text = f"{name} ({total_area:.1f} м²)\n" + "\n".join(room_details)
                if total_area > 100: text = f"<font color='red'>{text}</font>"
                item.info_label.setText(text)
                if hasattr(item, 'widget_ref'):
                    item.widget_ref.adjustSize()
                    item.setSizeHint(item.widget_ref.sizeHint())

    def go_to_planning_screen(self):
        all_rooms = self.project.all_rooms()
        if not all_rooms or not self.project.zones:
            QMessageBox.warning(self, "Ошибка", "Сначала распределите зоны.")
            return
        self.project.cleaning_tasks = plan_cleaning_schedule(self.project)
        self.load_report_screen()
        self.stack.setCurrentIndex(3)

    # ---------- Экран отчёта ----------
    def setup_report_screen(self):
        layout = QVBoxLayout(self.report_screen)
        layout.addWidget(QLabel("Отчёт и анализ затрат"))
        self.report_preview = QTextEdit(readOnly=True)
        layout.addWidget(self.report_preview)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("Сохранить проект", clicked=self.save_project))
        btn_layout.addWidget(QPushButton("Создать DOCX", clicked=self.generate_docx))
        btn_layout.addWidget(QPushButton("Экспорт CSV", clicked=self.export_csv))
        btn_layout.addWidget(QPushButton("Экспорт Excel", clicked=self.export_xlsx))
        layout.addLayout(btn_layout)
        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.stack.setCurrentIndex(2)))
        layout.addLayout(nav)

    def load_report_screen(self):
        cost = calculate_cost(self.project)
        text = f"<h2>{self.project.name}</h2>"
        text += f"<p>Общее время уборки: {cost['total_time_hours']} ч</p>"
        text += f"<p>Затраты (штат с переработкой): {cost['cost_with_overtime']} руб</p>"
        text += f"<p>Затраты (наём): {cost['cost_hire']} руб</p>"
        text += f"<p><b>Рекомендация: {cost['recommendation']}</b></p>"
        text += "<h3>Расписание уборки</h3>"
        tasks_by_emp = {}
        for task in self.project.cleaning_tasks:
            tasks_by_emp.setdefault(task.employee, []).append(task)
        for emp_idx, tasks in tasks_by_emp.items():
            name = self.project.employee_names[emp_idx] if emp_idx < len(self.project.employee_names) else f"Сотрудник {emp_idx+1}"
            text += f"<h4>{name}</h4>"
            text += "<table border='1' cellspacing='0' cellpadding='4'><tr><th>Комната</th><th>Начало</th><th>Конец</th><th>Длит.</th></tr>"
            for t in tasks[:50]:
                room = self._find_room_by_id(t.room_id)
                room_name = room.name if room else str(t.room_id)
                dur = (t.end_dt - t.start_dt).seconds // 60
                text += f"<tr><td>{room_name}</td><td>{t.start_dt.strftime('%H:%M')}</td><td>{t.end_dt.strftime('%H:%M')}</td><td>{dur} мин</td></tr>"
            text += "</table>"
        self.report_preview.setHtml(text)

    def _find_room_by_id(self, room_id):
        for floor in self.project.floors:
            for room in floor.rooms:
                if room.id == room_id:
                    return room
        return None

    def save_project(self):
        if not self.project: return
        if self.current_project_path: path = self.current_project_path
        else: path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", PROJECTS_DIR, "JSON (*.json)")
        if path:
            self.project.save_to_file(path); self.current_project_path = path
            QMessageBox.information(self, "Успех", f"Проект сохранён в {path}")

    def generate_docx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", "", "Word (*.docx)")
        if path:
            try:
                generate_report(self.project, path)
                QMessageBox.information(self, "Готово", f"Отчёт сохранён: {path}")
            except Exception as e: QMessageBox.critical(self, "Ошибка", str(e))

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт CSV", "", "CSV (*.csv)")
        if path:
            export_tasks_csv(self.project, path)
            QMessageBox.information(self, "Готово", f"График сохранён в {path}")

    def export_xlsx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт Excel", "", "Excel (*.xlsx)")
        if path:
            export_tasks_excel(self.project, path)
            QMessageBox.information(self, "Готово", f"График сохранён в {path}")

    # ---------- Экран нормативов ----------
    def setup_norms_screen(self):
        layout = QVBoxLayout(self.norms_screen)
        layout.addWidget(QLabel("Нормативы СанПиН по типам помещений"))
        self.norms_table = QTableWidget(0, 3)
        self.norms_table.setHorizontalHeaderLabels(["Тип", "Коэффициент сложности", "Частота (раз/день)"])
        layout.addWidget(self.norms_table)
        btn_save = QPushButton("Сохранить"); btn_save.clicked.connect(self.save_norms)
        layout.addWidget(btn_save)
        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.stack.setCurrentIndex(0)))
        layout.addLayout(nav)

    def load_norms_screen(self):
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
            except: pass
        QMessageBox.information(self, "Успех", "Нормативы обновлены.")

    # ---------- Инспектор DXF ----------
    def inspect_dxf_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите DXF для анализа", "", "DXF Files (*.dxf)")
        if not path: return
        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            layers = set()
            type_counts = Counter()
            for e in msp:
                layers.add(e.dxf.layer)
                type_counts[e.dxftype()] += 1
            insunits = doc.header.get("$INSUNITS", 0)
            units_map = {0: "не заданы", 1: "дюймы", 4: "мм", 5: "см", 6: "м"}
            units_str = units_map.get(insunits, f"код {insunits}")
            info = f"<h3>Инспекция DXF: {os.path.basename(path)}</h3>"
            info += f"<p><b>Единицы чертежа (INSUNITS):</b> {units_str}</p>"
            info += "<h4>Слои:</h4><ul>" + "".join(f"<li>{layer}</li>" for layer in sorted(layers)) + "</ul>"
            info += "<h4>Типы объектов:</h4><ul>"
            for t, cnt in type_counts.most_common():
                info += f"<li>{t}: {cnt}</li>"
            info += "</ul>"
            info += "<p>На основе этих данных настройте параметры в config загрузки DXF.</p>"
            dlg = QDialog(self)
            dlg.setWindowTitle("Инспектор DXF")
            dlg.resize(500, 600)
            layout = QVBoxLayout(dlg)
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setHtml(info)
            layout.addWidget(text_edit)
            btn_close = QPushButton("Закрыть")
            btn_close.clicked.connect(dlg.close)
            layout.addWidget(btn_close)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать DXF: {e}")