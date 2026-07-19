import sys, os, json, glob, math
from collections import Counter
import ezdxf
from datetime import datetime, date, timedelta
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QToolBar, QInputDialog, QFormLayout,
    QSpinBox, QLineEdit, QTableWidget, QTableWidgetItem, QTextEdit,
    QDoubleSpinBox, QDialog, QDialogButtonBox, QComboBox, QSlider,
    QProgressBar, QMenu, QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QGraphicsPolygonItem, QGraphicsLineItem, QGraphicsEllipseItem,
    QGraphicsTextItem, QGraphicsItem, QToolTip, QGraphicsRectItem
)
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF
from PySide6.QtGui import (
    QPen, QColor, QBrush, QPolygonF, QPixmap, QFont, QAction, QPainter, QCursor
)
from shapely.geometry import LineString, box, Polygon
from shapely.ops import unary_union, polygonize, snap

from project import Project, Wall, Room, Floor, Zone, CleaningTask
from room_builder import build_rooms_from_walls, nearest_point_on_segment, split_walls_at_intersections
from zone_manager import manual_distribution
from cost_calculator import calculate_cost
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY
from scheduler import plan_cleaning_schedule
from dxf_importer import import_dxf

from screens.plan_screen import PlanScreen
from screens.zone_screen import ZoneScreen
from screens.report_screen import ReportScreen
from screens.norms_screen import NormsScreen

PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)

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

        self.start_screen = QWidget()
        self.setup_start_screen()
        self.plan_screen = PlanScreen(self)
        self.zone_screen = ZoneScreen(self)
        self.report_screen = ReportScreen(self)
        self.norms_screen = NormsScreen(self)

        self.stack.addWidget(self.start_screen)   # 0
        self.stack.addWidget(self.plan_screen)    # 1
        self.stack.addWidget(self.zone_screen)    # 2
        self.stack.addWidget(self.report_screen)  # 3
        self.stack.addWidget(self.norms_screen)   # 4
        self.stack.setCurrentIndex(0)

    # ---------- Стартовый экран ----------
    def setup_start_screen(self):
        layout = QVBoxLayout(self.start_screen)
        layout.addWidget(QLabel("Добро пожаловать в KleanPlann"))
        layout.addWidget(QLabel("Недавние проекты:"))
        self.project_list = QListWidget()
        layout.addWidget(self.project_list)

        btn_new = QPushButton("Новый проект")
        btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Открыть проект")
        btn_open.clicked.connect(self.open_project)
        btn_norms = QPushButton("Нормативы СанПиН")
        btn_norms.clicked.connect(lambda: self.stack.setCurrentIndex(4))
        btn_inspect = QPushButton("Инспектор DXF")
        btn_inspect.clicked.connect(self.inspect_dxf_file)

        layout.addWidget(btn_new)
        layout.addWidget(btn_open)
        layout.addWidget(btn_norms)
        layout.addWidget(btn_inspect)
        self.refresh_project_list()

    def refresh_project_list(self):
        self.project_list.clear()
        files = glob.glob(os.path.join(PROJECTS_DIR, "*.json"))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:10]:
            name = os.path.basename(f).replace('.json','')
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, f)
            self.project_list.addItem(item)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Название помещения:")
        if ok and name:
            self.project = Project(name)
            self.current_project_path = None
            for floor in self.project.floors:
                floor._builder = self.build_rooms_for_floor
            self.load_plan_screen()
            self.stack.setCurrentIndex(1)

    def open_project(self):
        item = self.project_list.currentItem()
        if item:
            path = item.data(Qt.UserRole)
            self.project = Project.load_from_file(path)
            self.current_project_path = path
            for floor in self.project.floors:
                floor._builder = self.build_rooms_for_floor
            self.load_plan_screen()
            self.stack.setCurrentIndex(1)
        else:
            QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")

    # ---------- Экран плана (делегаты) ----------
    def load_plan_screen(self):
        floor = self.project.current_floor
        ps = self.plan_screen
        ps.param_total_area.setText(str(floor.total_area_m2))
        ps.param_employees.setValue(self.project.employees_count)
        ps.param_rate.setValue(self.project.hourly_rate)
        ps.weather_combo.setCurrentIndex(0)
        ps.opacity_slider.setValue(30)
        ps.update_floor_combo()
        self.refresh_plan_view()

    def refresh_plan_view(self):
        scene = self.plan_screen.plan_scene
        scene.clear()
        if self.project and self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0])
            scene.addPixmap(pix)
            self.plan_screen.plan_view.setSceneRect(QRectF(pix.rect()))
        else:
            # Не фиксируем размер сцены – он будет определён автоматически
            self.plan_screen.plan_view.setSceneRect(QRectF())   # сброс фиксированного прямоугольника

        for wall in self.project.walls:
            from tools import WallSegmentItem
            seg = WallSegmentItem(QPointF(wall.x1, wall.y1), QPointF(wall.x2, wall.y2))
            scene.addItem(seg)

        self.draw_rooms()
        self.update_room_table()

        # Устанавливаем sceneRect по реальному содержимому и центрируем
        rect = scene.itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            self.plan_screen.plan_view.setSceneRect(rect)       # ограничиваем сцену содержимым
            self.plan_screen.plan_view.fitInView(rect, Qt.KeepAspectRatio)

    def draw_rooms(self):
        scene = self.plan_screen.plan_scene
        for item in scene.items():
            if (isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None) or \
               (isinstance(item, QGraphicsTextItem) and item.data(1) == "room_label"):
                scene.removeItem(item)
        alpha = self.plan_screen.opacity_slider.value()
        for room in self.project.rooms:
            col = QColor(*room.color[:3], alpha)
            brush = QBrush(col)
            pen = QPen(Qt.black, 1)
            poly = QPolygonF([QPointF(x,y) for x,y in room.points])
            item = scene.addPolygon(poly, pen, brush)
            item.setData(Qt.UserRole, room.id)
            cx = sum(p[0] for p in room.points)/len(room.points)
            cy = sum(p[1] for p in room.points)/len(room.points)
            bg_color = QColor(0,0,255) if room.room_type else QColor(Qt.red)
            label_text = room.name
            text_item = QGraphicsTextItem()
            text_item.setHtml(f"<div style='text-align:center; background-color:{bg_color.name()}; color:white; padding:2px; border:1px solid black;'>{label_text}</div>")
            text_item.setPos(cx - 30, cy - 10)
            text_item.setData(1, "room_label")
            text_item.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            scene.addItem(text_item)

    def update_room_table(self):
        self.plan_screen.room_table.setRowCount(0)
        if not self.project or not self.project.rooms:
            return
        for room in self.project.rooms:
            row = self.plan_screen.room_table.rowCount()
            self.plan_screen.room_table.insertRow(row)
            self.plan_screen.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.plan_screen.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.plan_screen.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.plan_screen.room_table.setItem(row, 3, QTableWidgetItem(f"{room.area_m2:.1f}"))
            self.plan_screen.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def on_scene_changed(self):
        new_walls = self.plan_screen.plan_view.collect_walls()
        old_walls_set = {(w.x1, w.y1, w.x2, w.y2) for w in self.project.walls}
        new_walls_set = {(w.x1, w.y1, w.x2, w.y2) for w in new_walls}
        added = new_walls_set - old_walls_set
        removed = old_walls_set - new_walls_set
        changed = added | removed
        changed_walls = [Wall(x1, y1, x2, y2) for x1, y1, x2, y2 in changed]
        self.project.walls = new_walls
        self.project.current_floor.mark_dirty()
        self.build_rooms_for_floor(self.project.current_floor, changed_walls if changed_walls else None)
        if self.plan_screen.param_total_area.text():
            self._scale_rooms()
        self.draw_rooms()
        self.update_room_table()

    def build_rooms_for_floor(self, floor, changed_walls=None):
        if not floor.walls:
            floor.rooms = []
            return
        if not floor._dirty and changed_walls is None:
            return

        # Если были ручные изменения или стен меньше 500, используем полный алгоритм
        if (changed_walls is not None) or (len(floor.walls) < 500):
            walls_list = [(w.x1, w.y1, w.x2, w.y2) for w in floor.walls]
            walls_list = split_walls_at_intersections(walls_list)
            polygons = build_rooms_from_walls(walls_list)
            if not polygons:
                floor.rooms = []
                return
            new_rooms = []
            for i, pts in enumerate(polygons):
                color = ROOM_COLORS[i % len(ROOM_COLORS)]
                new_rooms.append(Room(i, pts, color=color))
            # Переносим старые свойства
            old_rooms = {r.id: r for r in floor.rooms}
            for new_room in new_rooms:
                center_new = (sum(x for x,y in new_room.points)/len(new_room.points),
                              sum(y for x,y in new_room.points)/len(new_room.points))
                for old_room in old_rooms.values():
                    center_old = (sum(x for x,y in old_room.points)/len(old_room.points),
                                  sum(y for x,y in old_room.points)/len(old_room.points))
                    if math.hypot(center_new[0]-center_old[0], center_new[1]-center_old[1]) < 0.5:
                        new_room.area_m2 = old_room.area_m2
                        new_room.traffic = old_room.traffic
                        new_room.room_type = old_room.room_type
                        new_room.name = old_room.name
                        break
            floor.rooms = new_rooms
        else:
            # Быстрый режим для больших DXF-проектов
            lines = [LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in floor.walls]
            merged = unary_union(lines)
            merged = snap(merged, merged, 0.2)
            if merged.geom_type == 'LineString':
                line_col = [merged]
            elif merged.geom_type == 'MultiLineString':
                line_col = list(merged.geoms)
            else:
                line_col = []
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                polygons = list(polygonize(line_col))
            rooms = []
            for i, poly in enumerate(polygons):
                if poly.is_valid and poly.area > 0.5:
                    pts = list(poly.exterior.coords)
                    if len(pts) > 1 and pts[0] == pts[-1]:
                        pts = pts[:-1]
                    rooms.append(Room(i, pts, area_m2=poly.area))
            floor.rooms = rooms

        floor.total_area_m2 = sum(r.area_m2 for r in floor._cached_rooms)

    def _scale_rooms(self):
        total_area = float(self.plan_screen.param_total_area.text() or 0)
        if total_area <= 0:
            return
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
        current_floor.total_area_m2 = total_area

    def _polygon_area(self, points):
        n = len(points); area = 0.0
        for i in range(n):
            x1,y1 = points[i]; x2,y2 = points[(i+1)%n]
            area += x1*y2 - x2*y1
        return abs(area)/2.0

    def go_to_zone_screen(self):
        if not self.project: return
        self.project.total_area_m2 = float(self.plan_screen.param_total_area.text() or 0)
        self.project.employees_count = self.plan_screen.param_employees.value()
        self.project.hourly_rate = self.plan_screen.param_rate.value()
        weather_text = self.plan_screen.weather_combo.currentText()
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
            QMessageBox.warning(self, "Ошибка", "Не задана площадь комнат.")
            return
        while len(self.project.employee_names) < self.project.employees_count:
            self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names)+1}")
        self.project.employee_names = self.project.employee_names[:self.project.employees_count]
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.zone_screen.load_zone()
        self.stack.setCurrentIndex(2)

    # ---------- Экран зон ----------
    def add_employee(self):
        self.project.employees_count += 1
        self.project.employee_names.append(f"Сотрудник {self.project.employees_count}")
        self.zone_screen.load_zone()

    def remove_employee(self, item):
        row = self.zone_screen.employee_list_widget.row(item)
        if row >= 0:
            self.zone_screen.employee_list_widget.takeItem(row)
            del self.project.employee_names[row]
            self.project.employees_count -= 1
            self.recalculate_zones()

    def rename_employee(self, index):
        current_name = self.project.employee_names[index] if index < len(self.project.employee_names) else ""
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Имя сотрудника:", text=current_name)
        if ok and new_name:
            self.project.employee_names[index] = new_name
            item = self.zone_screen.employee_list_widget.item(index)
            if item and hasattr(item, 'name_btn'):
                item.name_btn.setText(new_name)

    def recalculate_zones(self):
        all_rooms = self.project.all_rooms()
        if not all_rooms: return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Сначала задайте площадь.")
            return
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.zone_screen.refresh_zone_display()
        self.update_employee_labels()

    def update_employee_labels(self):
        for i in range(self.zone_screen.employee_list_widget.count()):
            item = self.zone_screen.employee_list_widget.item(i)
            if hasattr(item, 'info_label'):
                zones = [z for z in self.project.zones if z.employee_index == i]
                total_area = 0.0
                room_details = []
                for z in zones:
                    for rid in z.room_ids:
                        room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                        if room:
                            total_area += room.area_m2
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
            self.zone_screen.refresh_zone_display()
            self.update_employee_labels()

    def _get_schedule_tip(self, room_id, emp_idx):
        if not hasattr(self.project, 'cleaning_tasks') or not self.project.cleaning_tasks:
            return "Расписание не сгенерировано"
        tasks = [t for t in self.project.cleaning_tasks if t.room_id == room_id and t.employee == emp_idx]
        if not tasks: return "Нет назначенных уборок"
        lines = []
        for t in sorted(tasks, key=lambda x: x.start_dt)[:10]:
            lines.append(f"{t.start_dt.strftime('%H:%M')} - {t.end_dt.strftime('%H:%M')}")
        return "\n".join(lines)

    def go_to_planning_screen(self):
        all_rooms = self.project.all_rooms()
        if not all_rooms or not self.project.zones:
            QMessageBox.warning(self, "Ошибка", "Сначала распределите зоны.")
            return
        self.project.cleaning_tasks = plan_cleaning_schedule(self.project)
        self.report_screen.load_report()
        self.stack.setCurrentIndex(3)

    # ---------- Прочие методы ----------
    def save_project(self):
        if not self.project: return
        if self.current_project_path:
            path = self.current_project_path
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", PROJECTS_DIR, "JSON (*.json)")
        if path:
            self.project.save_to_file(path)
            self.current_project_path = path
            QMessageBox.information(self, "Успех", f"Проект сохранён в {path}")

    def load_image(self):
        if not self.project: return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить план", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.project.image_paths = [path]
            self.refresh_plan_view()

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

    def on_room_table_hover(self, row, col):
        item = self.plan_screen.room_table.item(row, 0)
        if item:
            room_id = item.data(Qt.UserRole)
            self.plan_screen.plan_view.highlight_room(room_id)

    def on_room_table_double_clicked(self, row, col):
        item = self.plan_screen.room_table.item(row, 0)
        if item:
            room_id = item.data(Qt.UserRole)
            self.edit_room_properties(room_id)

    def edit_room_properties(self, room_id):
        room = next((r for r in self.project.rooms if r.id == room_id), None)
        if not room: return
        dlg = QDialog(self); dlg.setWindowTitle("Редактирование комнаты")
        layout = QFormLayout(dlg)
        name_edit = QLineEdit(room.name)
        num_spin = QSpinBox(); num_spin.setRange(1,999); num_spin.setValue(room.id+1)
        area_spin = QDoubleSpinBox(); area_spin.setRange(0,100000); area_spin.setValue(room.area_m2)
        traffic_spin = QSpinBox(); traffic_spin.setRange(0,10000); traffic_spin.setValue(room.traffic)
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

    def switch_floor(self, idx):
        if idx >= 0 and idx < len(self.project.floors):
            self.project.current_floor_index = idx
            floor = self.project.current_floor
            self.plan_screen.param_total_area.setText(str(floor.total_area_m2))
            self.refresh_plan_view()

    def add_floor(self):
        if not self.project: return
        floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors)+1}")
        floor._builder = self.build_rooms_for_floor
        self.project.floors.append(floor)
        self.project.current_floor_index = len(self.project.floors)-1
        self.plan_screen.update_floor_combo()
        self.plan_screen.param_total_area.setText("0")
        self.refresh_plan_view()

    def finish_floor_selection(self):
        self.plan_screen.finish_floor_selection()

    def on_floor_rect_added(self, x, y, w, h):
        self.plan_screen.on_floor_rect_added(x, y, w, h)

    def sort_rooms(self):
        criteria = self.plan_screen.sort_combo.currentText()
        if not self.project or not self.project.rooms:
            return
        rooms = self.project.rooms[:]
        if criteria == "По номеру": rooms.sort(key=lambda r: r.id)
        elif criteria == "По площади": rooms.sort(key=lambda r: r.area_m2, reverse=True)
        elif criteria == "По алфавиту": rooms.sort(key=lambda r: r.name.lower())
        elif criteria == "По типу": rooms.sort(key=lambda r: r.room_type if r.room_type else "яя")
        else: return
        self.plan_screen.room_table.setRowCount(0)
        for room in rooms:
            row = self.plan_screen.room_table.rowCount()
            self.plan_screen.room_table.insertRow(row)
            self.plan_screen.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.plan_screen.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.plan_screen.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.plan_screen.room_table.setItem(row, 3, QTableWidgetItem(f"{room.area_m2:.1f}"))
            self.plan_screen.room_table.item(row, 0).setData(Qt.UserRole, room.id)

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