import sys, os, json, glob, math
from collections import defaultdict
import ezdxf
from datetime import datetime, date, timedelta
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel, QDialog, QTextEdit,
    QMessageBox, QGraphicsScene, QGraphicsView, QGraphicsPolygonItem,
    QGraphicsTextItem, QGraphicsItem, QGraphicsRectItem, QGraphicsProxyWidget,
    QFileDialog, QInputDialog, QTableWidgetItem, QToolTip, QListWidgetItem
)
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF
from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPolygonF

from project import Project, Wall, Room, Floor, Zone, CleaningTask, Shift
from room_builder import (build_rooms_from_walls, detect_rooms,
                          extract_wall_centerlines, cleanup_segments, snap_wall_ends)
from zone_manager import manual_distribution, PRIORITY_BALANCED
from cost_calculator import calculate_cost, estimate_required_employees
from report_generator import generate_report
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY, DEFAULT_TRAFFIC_PER_TYPE
from scheduler import plan_cleaning_schedule, schedule_single_shift, compute_recommended_employees
from calendar_export import export_tasks_csv, export_tasks_excel
from dxf_analyzer import load_wall_segments
from tools import PlanView, WallSegmentItem
from shapely.geometry import box

from screens.start_screen import StartScreen
from screens.plan_screen import PlanScreen
from screens.zone_screen import ZoneScreen
from screens.report_screen import ReportScreen
from screens.norms_screen import NormsScreen

PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KleanPlann - Планировщик уборки")
        self.project = None
        self.current_project_path = None
        self._closing = False
        self.last_unscheduled = []
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Экраны
        self.start_screen = StartScreen(self)
        self.plan_screen = PlanScreen(self)
        self.zone_screen = ZoneScreen(self)
        self.report_screen = ReportScreen(self)
        self.norms_screen = NormsScreen(self)

        self.stack.addWidget(self.start_screen)
        self.stack.addWidget(self.plan_screen)
        self.stack.addWidget(self.zone_screen)
        self.stack.addWidget(self.report_screen)
        self.stack.addWidget(self.norms_screen)
        self.stack.setCurrentIndex(0)

        self.temp_dxf_path = None
        self.temp_dxf_segments = []
        self.dxf_info = None
        self.floor_rects = []
        self._exclude_mode_active = False

        # Прокси-атрибуты для виджетов экранов
        self.plan_view = self.plan_screen.plan_view
        self.plan_scene = self.plan_screen.plan_scene
        self.param_total_area = self.plan_screen.param_total_area
        self.param_employees = self.plan_screen.param_employees
        self.param_rate = self.plan_screen.param_rate
        self.weather_combo = self.plan_screen.weather_combo
        self.opacity_slider = self.plan_screen.opacity_slider
        self.fill_mode_combo = self.plan_screen.fill_mode_combo
        self.room_table = self.plan_screen.room_table
        self.sort_combo = self.plan_screen.sort_combo
        self.floor_combo = self.plan_screen.floor_combo
        self.btn_finish_floors = self.plan_screen.btn_finish_floors

        self.zone_view = self.zone_screen.zone_view
        self.zone_scene = self.zone_screen.zone_scene
        self.priority_combo = self.zone_screen.priority_combo
        self.employee_list_widget = self.zone_screen.employee_list_widget
        self.shift_start_edit = self.zone_screen.shift_start_edit
        self.shift_end_edit = self.zone_screen.shift_end_edit
        self.lunch_start_edit = self.zone_screen.lunch_start_edit
        self.lunch_end_edit = self.zone_screen.lunch_end_edit

        self.report_preview = self.report_screen.report_preview

        self.norms_table = self.norms_screen.norms_table

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        if self.project is not None:
            ret = QMessageBox.question(
                self, "Сохранить проект?",
                "Хотите сохранить текущий проект перед выходом?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if ret == QMessageBox.Save:
                self.save_project()
                self._closing = True
                event.accept()
            elif ret == QMessageBox.Discard:
                self._closing = True
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def show_help(self):
        text = """
<h2>KleanPlann — краткое руководство</h2>

<h3>Порядок действий</h3>
<ol>
  <li><b>Новый проект</b> или откройте сохранённый.</li>
  <li>На экране плана загрузите чертёж: <b>Загрузить DXF</b> (рекомендуется)
      или <b>Загрузить план</b> (изображение) + <b>Распознать стены (CV)</b>.</li>
  <li>Укажите общую площадь и количество сотрудников (или нажмите
      <b>Авторасчёт персонала</b>).</li>
  <li>Нажмите <b>Далее</b> — программа распределит комнаты между
      сотрудниками (поровну и по близости).</li>
  <li>Сгенерируйте <b>расписание уборки</b> и сохраните отчёт (DOCX/CSV/Excel).</li>
</ol>

<h3>Инструменты редактора плана</h3>
<ul>
  <li><b>Выбор</b> — клик по комнате открывает её свойства.</li>
  <li><b>Ластик</b> — удаляет стену под курсором.</li>
  <li><b>Линия</b> — рисует стену от точки к точке.</li>
  <li><b>Комната</b> — рисует прямоугольную комнату (4 стены сразу).</li>
  <li><b>Калибровка</b> — выравнивает стены по горизонтали/вертикали.</li>
</ul>

<h3>Хот-кеи</h3>
<ul>
  <li><b>Ctrl+Z</b> — отменить, <b>Ctrl+Y</b> — повторить.</li>
  <li><b>Ctrl</b> при рисовании — привязка угла (кратно 90°).</li>
  <li><b>ПКМ</b> (правая кнопка) — панорамирование, перетаскивание вершин.</li>
  <li><b>Колесо мыши</b> — масштаб.</li>
</ul>
"""
        dlg = QDialog(self)
        dlg.setWindowTitle("Справка")
        dlg.resize(640, 640)
        layout = QVBoxLayout(dlg)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setHtml(text)
        layout.addWidget(text_edit)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(dlg.close)
        layout.addWidget(btn_close)
        dlg.exec()

    # ---------- Проект ----------
    def create_new_project(self, name):
        self.project = Project(name)
        self.current_project_path = None
        self.load_plan_screen()
        self.stack.setCurrentIndex(1)

    def open_project_from_path(self, path):
        self.project = Project.load_from_file(path)
        self.current_project_path = path
        self.load_plan_screen()
        self.stack.setCurrentIndex(1)

    def rename_project_path(self, path, old_name):
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Новое название:", text=old_name)
        if ok and new_name and new_name != old_name:
            new_path = os.path.join(PROJECTS_DIR, f"{new_name}.json")
            try:
                os.rename(path, new_path)
                with open(new_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['name'] = new_name
                data['last_modified'] = datetime.now().isoformat()
                with open(new_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.start_screen.refresh_project_list()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось переименовать: {e}")

    def return_to_start_screen(self):
        if self.project is not None:
            ret = QMessageBox.question(
                self, "Сохранить проект?",
                "Хотите сохранить текущий проект перед выходом?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if ret == QMessageBox.Save:
                self.save_project()
                self.stack.setCurrentIndex(0)
            elif ret == QMessageBox.Discard:
                self.stack.setCurrentIndex(0)
            else:
                return
        else:
            self.stack.setCurrentIndex(0)

    def go_to_norms_screen(self):
        self.load_norms_screen()
        self.stack.setCurrentIndex(4)

    # ---------- Экран плана ----------
    def load_plan_screen(self):
        self.param_total_area.setText(str(self.project.current_floor.total_area_m2))
        self.param_employees.setValue(self.project.employees_count)
        self.param_rate.setValue(self.project.hourly_rate)
        self.weather_combo.setCurrentIndex(0)
        self.opacity_slider.setValue(30)
        self.update_floor_combo()
        self.refresh_plan_view()

    def remove_image(self):
        if not self.project or not self.project.image_paths:
            return
        self.project.image_paths = []
        self.refresh_plan_view()

    def load_image(self):
        if not self.project:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить план", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.project.image_paths = [path]
            self.refresh_plan_view()

    def load_dxf(self):
        if not self.project:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить DXF", "", "DXF Files (*.dxf)")
        if not path:
            return
        try:
            doc, segments, info = load_wall_segments(path)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать DXF: {e}")
            return
        self.temp_dxf_path = path
        self.temp_dxf_segments = segments
        self.dxf_info = info
        self.floor_rects = []
        self.show_temp_dxf_lines()
        self.plan_screen.plan_view.set_tool(6)
        self.btn_finish_floors.setVisible(True)
        t = info.get("wall_thickness_m")
        QMessageBox.information(self, "Разметка этажей",
            f"Слои стен: {', '.join(info['wall_layers'])}\n"
            f"Отрезков: {len(segments)}; толщина стены ≈ {t:.2f} м\n\n"
            "Выделите прямоугольные области для каждого этажа. "
            "Нажмите 'Завершить разметку', когда закончите."
            if t else
            "Выделите прямоугольные области для каждого этажа. "
            "Нажмите 'Завершить разметку', когда закончите.")

    def show_temp_dxf_lines(self):
        scene = self.plan_screen.plan_view.scene()
        for item in scene.items():
            if isinstance(item, object) and hasattr(item, 'pen') and item.pen().color() == QColor(128, 128, 128):
                if isinstance(item, WallSegmentItem):
                    scene.removeItem(item)
        for seg in self.temp_dxf_segments:
            coords = list(seg.coords)
            if len(coords) >= 2:
                for i in range(len(coords) - 1):
                    line = WallSegmentItem(QPointF(coords[i][0], coords[i][1]),
                                          QPointF(coords[i + 1][0], coords[i + 1][1]))
                    line.setPen(QPen(QColor(128, 128, 128), 1))
                    scene.addItem(line)
        rect = scene.itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            self.plan_screen.plan_view.setSceneRect(rect)
            self.plan_screen.plan_view.fitInView(rect, Qt.KeepAspectRatio)

    def on_floor_rect_added(self, x, y, w, h):
        self.floor_rects.append((x, y, w, h))

    def finish_floor_selection(self):
        if not self.floor_rects:
            QMessageBox.warning(self, "Ошибка", "Не выделено ни одного этажа.")
            return
        for fx, fy, fw, fh in self.floor_rects:
            floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors) + 1}")
            floor_box = box(fx, fy, fx + fw, fy + fh)
            floor_lines = []
            for seg in self.temp_dxf_segments:
                if seg.intersects(floor_box):
                    intersection = seg.intersection(floor_box)
                    if not intersection.is_empty:
                        if intersection.geom_type == 'LineString':
                            floor_lines.append(intersection)
                        elif intersection.geom_type == 'MultiLineString':
                            floor_lines.extend(list(intersection.geoms))
            walls = []
            for line in floor_lines:
                coords = list(line.coords)
                if len(coords) >= 2:
                    for i in range(len(coords) - 1):
                        walls.append((coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]))
            if walls:
                all_x = [c for w in walls for c in (w[0], w[2])]
                all_y = [c for w in walls for c in (w[1], w[3])]
                shift_x = -(min(all_x) + max(all_x)) / 2
                shift_y = -(min(all_y) + max(all_y)) / 2
                walls = [(w[0] + shift_x, w[1] + shift_y, w[2] + shift_x, w[3] + shift_y) for w in walls]
                info = self.dxf_info or {}
                thickness = info.get("wall_thickness_m")
                walls = extract_wall_centerlines(walls, wall_thickness=thickness)
                walls = snap_wall_ends(walls, tol=thickness * 1.5 if thickness else None)
                walls = cleanup_segments(walls, min_length=thickness * 2.0 if thickness else None)
                floor.walls = [Wall(*w) for w in walls]
            else:
                floor.walls = []
            info = self.dxf_info or {}
            polygons = detect_rooms(walls, mode="thin", door_gap=1.6, min_area=1.5, min_width=0.7,
                                    simplify_tol=info.get("recommended_simplify_tol_m", 0.08))
            total_area = 0.0
            for i, pts in enumerate(polygons):
                room = Room(i, pts, area_m2=0.0)
                px_area = self._polygon_area(pts)
                room.area_m2 = px_area
                total_area += px_area
                floor.rooms.append(room)
            floor.total_area_m2 = total_area
            self.project.floors.append(floor)
        self.project.current_floor_index = len(self.project.floors) - len(self.floor_rects)
        self.update_floor_combo()
        self.plan_scene.clear()
        self.refresh_plan_view()
        self.btn_finish_floors.setVisible(False)
        self.floor_rects = []
        self.temp_dxf_segments = []
        self.plan_view.set_tool(0)

    def add_floor(self):
        if not self.project:
            return
        floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors) + 1}")
        self.project.floors.append(floor)
        self.project.current_floor_index = len(self.project.floors) - 1
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

    def refresh_plan_view(self):
        scene = self.plan_screen.plan_view.scene()
        scene.clear()
        if self.project and self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0])
            scene.addPixmap(pix)
            self.plan_screen.plan_view.setSceneRect(QRectF(pix.rect()))
        elif not (self.project and (self.project.rooms or self.project.walls)):
            self.plan_screen.plan_view.setSceneRect(0, 0, 800, 600)
            text = scene.addText("Загрузите изображение плана")
            text.setPos(400, 300)
        for wall in self.project.walls:
            seg = WallSegmentItem(QPointF(wall.x1, wall.y1), QPointF(wall.x2, wall.y2))
            scene.addItem(seg)
        self.draw_rooms()
        self.update_room_table()
        if self.project.rooms or self.project.walls:
            points = []
            for wall in self.project.walls:
                points.extend(((wall.x1, wall.y1), (wall.x2, wall.y2)))
            for room in self.project.rooms:
                points.extend(room.points)
            if points:
                xs, ys = zip(*points)
                rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            else:
                rect = scene.itemsBoundingRect()
            if rect.width() > 0 and rect.height() > 0:
                margin = max(rect.width(), rect.height()) * 0.05
                rect.adjust(-margin, -margin, margin, margin)
                scene.setSceneRect(rect)
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda r=QRectF(rect): self.plan_screen.plan_view.fitInView(r, Qt.KeepAspectRatio))

    def draw_rooms(self):
        scene = self.plan_screen.plan_view.scene()
        for item in scene.items():
            if (isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None) or \
               (isinstance(item, QGraphicsTextItem) and item.data(1) == "room_label"):
                scene.removeItem(item)
        alpha = self.opacity_slider.value()
        fill_mode = self.fill_mode_combo.currentData() if hasattr(self, 'fill_mode_combo') else "standard"
        for room in self.project.rooms:
            if fill_mode == "type" and room.room_type:
                base = QColor(128, 128, 128)
                if room.room_type == "санузел":
                    base = QColor(0, 0, 255)
                elif room.room_type == "коридор":
                    base = QColor(0, 128, 0)
                elif room.room_type == "кабинет":
                    base = QColor(255, 215, 0)
                elif room.room_type == "склад":
                    base = QColor(128, 128, 128)
                elif room.room_type == "зал":
                    base = QColor(255, 165, 0)
                elif room.room_type == "кухня":
                    base = QColor(255, 0, 0)
                col = QColor(base.red(), base.green(), base.blue(), alpha)
                brush = QBrush(col)
            elif fill_mode == "type" and not room.room_type:
                brush = QBrush(Qt.black, Qt.DiagCrossPattern)
            else:
                col = QColor(*room.color[:3], alpha)
                brush = QBrush(col)
            pen = QPen(Qt.black, 1)
            pen.setCosmetic(True)
            poly = QPolygonF([QPointF(x, y) for x, y in room.points])
            item = scene.addPolygon(poly, pen, brush)
            item.setData(Qt.UserRole, room.id)
            item.setAcceptHoverEvents(False)
            item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            cx = sum(p[0] for p in room.points) / len(room.points)
            cy = sum(p[1] for p in room.points) / len(room.points)
            bg_color = QColor(0, 0, 255)
            if room.room_type == "коридор":
                bg_color = QColor(0, 128, 0)
            elif room.room_type == "кабинет":
                bg_color = QColor(255, 215, 0)
            elif room.room_type == "склад":
                bg_color = QColor(128, 128, 128)
            elif room.room_type == "зал":
                bg_color = QColor(255, 165, 0)
            elif room.room_type == "кухня":
                bg_color = QColor(255, 0, 0)
            prio_mark = "★ " if room.priority else ""
            disabled_mark = " 🚫" if room.disabled else ""
            label_text = f"{prio_mark}{room.name}{disabled_mark}"
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
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id + 1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(str(int(round(room.area_m2)))))
            self.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def on_room_table_hover(self, row, col):
        item = self.room_table.item(row, 0)
        if item:
            self.plan_view.highlight_room(item.data(Qt.UserRole))

    def on_room_table_double_clicked(self, row, col):
        item = self.room_table.item(row, 0)
        if item:
            self.edit_room_properties(item.data(Qt.UserRole))

    def on_room_table_select(self):
        rows = self.room_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.room_table.item(rows[0].row(), 0)
        if item:
            self.plan_view.set_selected_room(item.data(Qt.UserRole))

    def toggle_room_table(self):
        self.room_table_collapsed = not self.room_table_collapsed
        self.room_table.setVisible(not self.room_table_collapsed)
        self.plan_screen.btn_collapse_table.setText("▲" if self.room_table_collapsed else "▼")

    def sort_rooms(self):
        idx = self.sort_combo.currentIndex()
        if not self.project or not self.project.rooms:
            return
        rooms = self.project.rooms[:]
        if idx == 1:
            rooms.sort(key=lambda r: r.id)
        elif idx == 2:
            rooms.sort(key=lambda r: r.area_m2, reverse=True)
        elif idx == 3:
            rooms.sort(key=lambda r: r.name.lower())
        elif idx == 4:
            rooms.sort(key=lambda r: r.room_type if r.room_type else "яя")
        else:
            return
        self.room_table.setRowCount(0)
        for room in rooms:
            row = self.room_table.rowCount()
            self.room_table.insertRow(row)
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id + 1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(str(int(round(room.area_m2)))))

    def update_room_opacity(self):
        self.draw_rooms()

    def on_scene_changed(self):
        self.project.walls = self.plan_view.collect_walls()
        self.build_rooms_from_project_walls()
        self.draw_rooms()
        self.update_room_table()
        current_floor = self.project.current_floor
        current_floor.total_area_m2 = sum(r.area_m2 for r in current_floor.rooms)
        self.param_total_area.setText(str(current_floor.total_area_m2))

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
            from screens.plan_screen import ROOM_COLORS
            for i, pts in enumerate(contours):
                color = ROOM_COLORS[i % len(ROOM_COLORS)]
                self.project.rooms.append(Room(i, pts, color=color))
                for j in range(len(pts)):
                    x1, y1 = pts[j]
                    x2, y2 = pts[(j + 1) % len(pts)]
                    self.project.walls.append(Wall(x1, y1, x2, y2))
            self._scale_rooms()
            self.refresh_plan_view()
            QMessageBox.information(self, "Готово", f"Распознано {len(contours)} комнат(ы).")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка CV", str(e))

    def build_rooms_from_project_walls(self):
        if not self.project.walls:
            self.project.rooms = []
            return
        walls_list = [(w.x1, w.y1, w.x2, w.y2) for w in self.project.walls]
        polygons = build_rooms_from_walls(walls_list, mode="thin")
        if not polygons:
            self.project.rooms = []
            return
        from screens.plan_screen import ROOM_COLORS
        new_rooms = []
        for i, pts in enumerate(polygons):
            color = ROOM_COLORS[i % len(ROOM_COLORS)]
            new_rooms.append(Room(i, pts, color=color))
        old_rooms = {r.id: r for r in self.project.rooms}
        for new_room in new_rooms:
            center_new = (sum(x for x, y in new_room.points) / len(new_room.points),
                          sum(y for x, y in new_room.points) / len(new_room.points))
            for old_room in old_rooms.values():
                center_old = (sum(x for x, y in old_room.points) / len(old_room.points),
                              sum(y for x, y in old_room.points) / len(old_room.points))
                if math.hypot(center_new[0] - center_old[0], center_new[1] - center_old[1]) < 10:
                    new_room.area_m2 = old_room.area_m2
                    new_room.traffic = old_room.traffic
                    new_room.room_type = old_room.room_type
                    new_room.name = old_room.name
                    new_room.priority = old_room.priority
                    new_room.disabled = old_room.disabled
                    break
        self.project.rooms = new_rooms
        self._scale_rooms()

    def _scale_rooms(self):
        total_area_text = (self.param_total_area.text() or "").strip()
        total_area = float(total_area_text) if total_area_text else 0.0
        if total_area <= 0:
            if self.project.calibration_line:
                from image_processor import calibrate_scale
                scale = calibrate_scale(self.project.calibration_line)
                for room in self.project.rooms:
                    room.area_m2 = self._polygon_area(room.points) * scale * scale
            return
        if self.project.calibration_line:
            from image_processor import calibrate_scale
            scale = calibrate_scale(self.project.calibration_line)
            total_calc = sum(r.area_m2 for r in self.project.rooms)
            if total_calc > 0:
                factor = total_area / total_calc
                for room in self.project.rooms:
                    room.area_m2 *= factor
        else:
            if self.project.rooms:
                total_px = sum(self._polygon_area(r.points) for r in self.project.rooms)
                if total_px > 0:
                    factor = total_area / total_px
                    for room in self.project.rooms:
                        room.area_m2 = self._polygon_area(room.points) * factor
                else:
                    area_per = total_area / len(self.project.rooms)
                    for room in self.project.rooms:
                        room.area_m2 = area_per
        current_floor = self.project.current_floor
        current_floor.total_area_m2 = sum(r.area_m2 for r in current_floor.rooms)
        self.param_total_area.setText(str(current_floor.total_area_m2))

    def _polygon_area(self, points):
        n = len(points)
        area = 0.0
        for i in range(n):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0

    def edit_room_properties(self, room_id):
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QSpinBox,
                                       QDoubleSpinBox, QComboBox, QCheckBox,
                                       QDialogButtonBox)
        room = next((r for r in self.project.rooms if r.id == room_id), None)
        if not room:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Редактирование комнаты")
        layout = QFormLayout(dlg)
        name_edit = QLineEdit(room.name)
        num_spin = QSpinBox()
        num_spin.setRange(1, 999)
        num_spin.setValue(room.id + 1)
        area_spin = QDoubleSpinBox()
        area_spin.setRange(0, 100000)
        area_spin.setValue(room.area_m2)
        traffic_spin = QSpinBox()
        traffic_spin.setRange(0, 10000)
        traffic_spin.setValue(room.traffic)
        type_combo = QComboBox()
        type_combo.addItems([""] + list(COMPLEXITY_FACTOR.keys()))
        type_combo.setCurrentText(room.room_type)
        type_combo.currentTextChanged.connect(
            lambda t: traffic_spin.setValue(DEFAULT_TRAFFIC_PER_TYPE.get(t, 0)) if t else None)
        layout.addRow("Название:", name_edit)
        layout.addRow("Номер:", num_spin)
        layout.addRow("Площадь (м²):", area_spin)
        layout.addRow("Проходимость (чел/ч):", traffic_spin)
        layout.addRow("Тип:", type_combo)
        prio_check = QCheckBox("★ Приоритетная уборка")
        prio_check.setChecked(room.priority)
        layout.addRow(prio_check)
        disabled_check = QCheckBox("🚫 Не назначать уборку")
        disabled_check.setChecked(room.disabled)
        layout.addRow(disabled_check)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        layout.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            room.priority = prio_check.isChecked()
            room.disabled = disabled_check.isChecked()
            new_num = num_spin.value()
            if any(r.id == new_num - 1 and r != room for r in self.project.rooms):
                QMessageBox.warning(self, "Ошибка", "Номер уже используется.")
                return
            room.id = new_num - 1
            room.name = name_edit.text() or f"Комната {room.id + 1}"
            room.area_m2 = area_spin.value()
            room.traffic = traffic_spin.value()
            room.room_type = type_combo.currentText()
            self.draw_rooms()
            self.update_room_table()

    def auto_calculate_staff(self):
        if not self.project or not self.project.all_rooms():
            QMessageBox.warning(self, "Авторасчёт персонала", "Сначала загрузите план с распознанными помещениями.")
            return
        weather_text = self.weather_combo.currentText()
        if "1.2" in weather_text:
            self.project.weather_factor = 1.2
        elif "1.5" in weather_text:
            self.project.weather_factor = 1.5
        elif "1.8" in weather_text:
            self.project.weather_factor = 1.8
        else:
            self.project.weather_factor = 1.0
        result = estimate_required_employees(self.project)
        self.param_employees.setValue(result["employees"])
        self.project.employees_count = result["employees"]
        QMessageBox.information(self, "Авторасчёт персонала",
            f"Рекомендуемое количество сотрудников: {result['employees']}\n"
            f"Расчётная ежедневная нагрузка: {result['daily_minutes']:.0f} мин.\n"
            f"Полезная смена одного сотрудника: {result['capacity_minutes']:.0f} мин.")

    # ---------- Планирование ----------
    def go_to_planning_screen(self, skip_check=False):
        """Переход к генерации расписания."""
        all_rooms = self.project.all_rooms()
        if not all_rooms or not self.project.zones:
            QMessageBox.warning(self, "Ошибка", "Сначала распределите зоны.")
            return
        self._apply_shift_and_lunch()

        if skip_check:
            result = schedule_single_shift(self.project, employees=self.project.employees_count,
                                           allow_partial_schedule=True)
            self.project.cleaning_tasks = result["tasks"]
            self.last_unscheduled = result["unscheduled_rooms_list"]
            self.load_report_screen()
            self.stack.setCurrentIndex(3)
            return

        cold_load = compute_recommended_employees(self.project)
        # Показываем диалог, если рекомендуемое число сотрудников больше текущего
        # ИЛИ если текущий штат не справляется с нагрузкой
        if cold_load > self.project.employees_count:
            recommended = cold_load
            msg = (f"Рекомендуемое количество сотрудников: {recommended}.\n"
                   f"Текущее: {self.project.employees_count}.\n\n"
                   f"Что вы хотите сделать?")
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Недостаточно сотрудников")
            dlg.setText(msg)
            btn_inc = dlg.addButton(f"✅ Увеличить до {recommended}", QMessageBox.AcceptRole)
            btn_keep = dlg.addButton("📋 Продолжить с текущим штатом", QMessageBox.ActionRole)
            btn_select = dlg.addButton("👆 Выбрать комнаты для исключения", QMessageBox.ActionRole)
            dlg.addButton(QMessageBox.Cancel)
            dlg.exec()
            clicked = dlg.clickedButton()
            if clicked == btn_inc:
                new_count = recommended
                self.project.employees_count = new_count
                while len(self.project.employee_names) < new_count:
                    self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names) + 1}")
                self.project.employee_names = self.project.employee_names[:new_count]
                active = [r for r in self.project.all_rooms() if not r.disabled]
                if not active:
                    QMessageBox.warning(self, "Ошибка", "Нет активных комнат для распределения.")
                    return
                percents = [100.0 / new_count] * new_count
                self.project.zones = manual_distribution(active, percents)
                for zone in self.project.zones:
                    zone.floor_index = 0
                result = schedule_single_shift(self.project, employees=new_count,
                                               allow_partial_schedule=True)
                self.project.cleaning_tasks = result["tasks"]
                self.last_unscheduled = result["unscheduled_rooms_list"]
                self.load_report_screen()
                self.stack.setCurrentIndex(3)
                return
            elif clicked == btn_select:
                self._manual_exclude_mode()
                return
            elif clicked == dlg.button(QMessageBox.Cancel):
                return
        else:
            # Даже если рекомендуемое число не больше текущего,
            # проверяем, справляется ли текущий штат с нагрузкой
            # Пробуем сгенерировать расписание и проверяем, есть ли нехватка
            test_result = schedule_single_shift(self.project,
                                                employees=self.project.employees_count,
                                                allow_partial_schedule=True)
            if test_result["unscheduled_rooms"] > 0 or test_result["missed_cleanings"] > 0:
                recommended = max(cold_load, self.project.employees_count + 1)
                msg = (f"Текущий штат ({self.project.employees_count} чел.) не справляется с нагрузкой.\n"
                       f"Рекомендуемое количество сотрудников: {recommended}.\n\n"
                       f"Что вы хотите сделать?")
                dlg = QMessageBox(self)
                dlg.setWindowTitle("Недостаточно сотрудников")
                dlg.setText(msg)
                btn_inc = dlg.addButton(f"✅ Увеличить до {recommended}", QMessageBox.AcceptRole)
                btn_keep = dlg.addButton("📋 Продолжить с текущим штатом", QMessageBox.ActionRole)
                btn_select = dlg.addButton("👆 Выбрать комнаты для исключения", QMessageBox.ActionRole)
                dlg.addButton(QMessageBox.Cancel)
                dlg.exec()
                clicked = dlg.clickedButton()
                if clicked == btn_inc:
                    new_count = recommended
                    self.project.employees_count = new_count
                    while len(self.project.employee_names) < new_count:
                        self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names) + 1}")
                    self.project.employee_names = self.project.employee_names[:new_count]
                    active = [r for r in self.project.all_rooms() if not r.disabled]
                    if not active:
                        QMessageBox.warning(self, "Ошибка", "Нет активных комнат для распределения.")
                        return
                    percents = [100.0 / new_count] * new_count
                    self.project.zones = manual_distribution(active, percents)
                    for zone in self.project.zones:
                        zone.floor_index = 0
                    result = schedule_single_shift(self.project, employees=new_count,
                                                   allow_partial_schedule=True)
                    self.project.cleaning_tasks = result["tasks"]
                    self.last_unscheduled = result["unscheduled_rooms_list"]
                    self.load_report_screen()
                    self.stack.setCurrentIndex(3)
                    return
                elif clicked == btn_select:
                    self._manual_exclude_mode()
                    return
                elif clicked == dlg.button(QMessageBox.Cancel):
                    return
                # Если выбрано "Продолжить" — используем тестовый результат
                self.project.cleaning_tasks = test_result["tasks"]
                self.last_unscheduled = test_result["unscheduled_rooms_list"]
                self.load_report_screen()
                self.stack.setCurrentIndex(3)
                return

        result = schedule_single_shift(self.project, employees=self.project.employees_count,
                                       allow_partial_schedule=True)
        self.project.cleaning_tasks = result["tasks"]
        self.last_unscheduled = result["unscheduled_rooms_list"]
        self.load_report_screen()
        self.stack.setCurrentIndex(3)

    def _manual_exclude_mode(self):
        if self.stack.currentIndex() != 2:
            self.stack.setCurrentIndex(2)
        self._exclude_mode_active = True
        scene = self.zone_view.scene()

        # Удаляем старые элементы режима исключения
        for item in scene.items():
            data2 = item.data(2)
            if data2 in ("exclude_label", "exclude_finish_btn"):
                scene.removeItem(item)
            if isinstance(item, QGraphicsRectItem) and item.data(1) == "zone_hitbox":
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)

        # Перерисовываем зоны без меток
        self.refresh_zone_display()
        for item in scene.items():
            if isinstance(item, QGraphicsRectItem) and item.data(1) == "zone_hitbox":
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)

        # Настраиваем клик по комнатам для исключения
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                room_id = item.data(Qt.UserRole)
                room = next((r for r in self.project.all_rooms() if r.id == room_id), None)
                if room is None:
                    continue
                if room.disabled:
                    color = QColor(255, 0, 0, 120)
                else:
                    color = QColor(0, 255, 0, 70)
                item.setBrush(QBrush(color))
                item.setAcceptHoverEvents(True)
                item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                item._exclude_room_id = room_id

                def make_handler(rid):
                    def handler(ev):
                        r = next((rr for rr in self.project.all_rooms() if rr.id == rid), None)
                        if r:
                            r.disabled = not r.disabled
                        # Обновляем только цвет этой комнаты
                        for it in self.zone_view.scene().items():
                            if isinstance(it, QGraphicsPolygonItem) and it.data(Qt.UserRole) == rid:
                                if r.disabled:
                                    it.setBrush(QBrush(QColor(255, 0, 0, 120)))
                                else:
                                    it.setBrush(QBrush(QColor(0, 255, 0, 70)))
                        ev.accept()
                    return handler

                item.mousePressEvent = make_handler(room_id)

        btn_finish = QPushButton("✅ Готово")
        btn_finish.setFixedSize(130, 40)
        btn_finish.clicked.connect(self._finish_exclude_and_continue)
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(btn_finish)
        proxy.setPos(scene.width() - 160, 10)
        proxy.setZValue(101)
        proxy.setData(2, "exclude_finish_btn")
        proxy.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        scene.addItem(proxy)

    def _finish_exclude_and_continue(self):
        self._exclude_mode_active = False
        scene = self.zone_view.scene()
        for item in scene.items():
            data2 = item.data(2)
            if data2 in ("exclude_label", "exclude_finish_btn"):
                scene.removeItem(item)
        self.refresh_zone_display()

        active_rooms = [r for r in self.project.all_rooms() if not r.disabled]
        if not active_rooms:
            self.project.cleaning_tasks = []
            self.last_unscheduled = []
            self.load_report_screen()
            self.stack.setCurrentIndex(3)
            return

        from zone_manager import manual_distribution
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(active_rooms, percents)
        for zone in self.project.zones:
            zone.floor_index = 0

        # Переходим на экран зон, чтобы виджеты смены существовали
        self.stack.setCurrentIndex(2)
        self.go_to_planning_screen(skip_check=True)

    def _apply_shift_and_lunch(self):
        # Безопасно читаем виджеты, если они существуют
        if hasattr(self, 'shift_start_edit') and self.shift_start_edit is not None:
            shift_start = self.shift_start_edit.text().strip() or "08:00"
        else:
            shift_start = self.project.shifts[0].start_time if self.project.shifts else "08:00"
        if hasattr(self, 'shift_end_edit') and self.shift_end_edit is not None:
            shift_end = self.shift_end_edit.text().strip() or "22:00"
        else:
            shift_end = self.project.shifts[-1].end_time if self.project.shifts else "22:00"
        if hasattr(self, 'lunch_start_edit') and self.lunch_start_edit is not None:
            lunch_start = self.lunch_start_edit.text().strip() or "12:00"
        else:
            lunch_start = self.project.breaks[0][0] if self.project.breaks else "12:00"
        if hasattr(self, 'lunch_end_edit') and self.lunch_end_edit is not None:
            lunch_end = self.lunch_end_edit.text().strip() or "13:00"
        else:
            lunch_end = self.project.breaks[0][1] if self.project.breaks else "13:00"
        self.project.shifts = [Shift("Основная", shift_start, shift_end)]
        self.project.breaks = [(lunch_start, lunch_end)]

    # ---------- Экран зон ----------
    def load_zone_screen(self):
        scene = self.zone_view.scene()
        scene.clear()
        if self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0])
            scene.addPixmap(pix)
            self.zone_view.setSceneRect(QRectF(pix.rect()))
        if self.project.shifts:
            self.shift_start_edit.setText(self.project.shifts[0].start_time)
            self.shift_end_edit.setText(self.project.shifts[-1].end_time)
        if self.project.breaks:
            self.lunch_start_edit.setText(self.project.breaks[0][0])
            self.lunch_end_edit.setText(self.project.breaks[0][1])
        self.employee_list_widget.clear()
        for i in range(self.project.employees_count):
            name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i + 1}"
            item = QListWidgetItem()
            widget = QWidget()
            vbox = QVBoxLayout(widget)
            vbox.setContentsMargins(4, 2, 4, 2)
            name_btn = QPushButton(name)
            name_btn.setFlat(True)
            name_btn.clicked.connect(lambda checked=False, idx=i: self.rename_employee(idx))
            h_name = QHBoxLayout()
            h_name.addWidget(name_btn)
            btn_del = QPushButton("✕")
            btn_del.setFixedSize(24, 24)
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
        self._apply_shift_and_lunch()
        all_rooms = self.project.all_rooms()
        if not all_rooms:
            return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Сначала задайте площадь.")
            return
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        priority = self.priority_combo.currentData() if hasattr(self, 'priority_combo') else PRIORITY_BALANCED
        self.project.zones = manual_distribution(all_rooms, percents, priority=priority)
        self.project.cleaning_tasks = []
        self.refresh_zone_display()
        self.update_employee_labels()

    def refresh_zone_display(self):
        scene = self.zone_view.scene()
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)
        for zone in self.project.zones:
            col = QColor(*zone.color)
            brush = QBrush(col)
            pen = QPen(Qt.black, 1)
            for rid in zone.room_ids:
                room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                if not room:
                    continue
                poly = QPolygonF([QPointF(x, y) for x, y in room.points])
                item = scene.addPolygon(poly, pen, brush)
                item.setData(Qt.UserRole, room.id)
                cx = sum(p[0] for p in room.points) / len(room.points)
                cy = sum(p[1] for p in room.points) / len(room.points)
                label_html = (
                    f"<div style='text-align:center; background-color:{col.name()}; color:white; "
                    f"padding:3px; border:1px solid black; font-size:18px; font-weight:bold;'>{zone.employee_index + 1}</div>"
                    f"<div style='text-align:center; background-color:{col.name()}; color:white; "
                    f"padding:1px 3px; border:1px solid black; font-size:10px;'>{room.name}<br>№{room.id + 1} ({room.area_m2:.1f} м²)</div>"
                )
                text = QGraphicsTextItem()
                text.setHtml(label_html)
                text.setPos(cx - 40, cy - 28)
                text.setData(1, "zone_label")
                text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                text.setAcceptHoverEvents(True)
                text.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(ev.screenPos(), self._get_schedule_tip(rid, emp))
                text.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                text.mousePressEvent = lambda ev, rid=rid: self.change_room_employee(rid)
                scene.addItem(text)
                hit = QGraphicsRectItem(cx - 45, cy - 32, 90, 52)
                hit.setPen(QPen(Qt.NoPen))
                hit.setBrush(QBrush(QColor(0, 0, 0, 1)))
                hit.setData(1, "zone_hitbox")
                hit.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                hit.setAcceptHoverEvents(True)
                hit.setZValue(3)
                hit.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(ev.screenPos(), self._get_schedule_tip(rid, emp))
                hit.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                hit.mousePressEvent = lambda ev, rid=rid: self.change_room_employee(rid)
                scene.addItem(hit)

    def _get_schedule_tip(self, room_id, emp_idx):
        if not hasattr(self.project, 'cleaning_tasks') or not self.project.cleaning_tasks:
            return "Расписание не сгенерировано"
        tasks = [t for t in self.project.cleaning_tasks if t.room_id == room_id and t.employee == emp_idx]
        if not tasks:
            return "Нет назначенных уборок"
        return "\n".join(
            f"{t.start_dt.strftime('%H:%M')} - {t.end_dt.strftime('%H:%M')}"
            for t in sorted(tasks, key=lambda x: x.start_dt)[:10])

    def change_room_employee(self, room_id):
        emp_list = [self.project.employee_names[i] for i in range(self.project.employees_count)]
        current = next((z.employee_index for z in self.project.zones if room_id in z.room_ids), 0)
        item, ok = QInputDialog.getItem(self, "Сменить сотрудника", "Выберите:", emp_list, current, False)
        if not ok:
            return
        new_emp = emp_list.index(item)
        for z in self.project.zones:
            if room_id in z.room_ids:
                z.room_ids.remove(room_id)
        for z in self.project.zones:
            if z.employee_index == new_emp:
                z.room_ids.append(room_id)
                break
        self.refresh_zone_display()
        self.update_employee_labels()

    def update_employee_labels(self):
        for i in range(self.employee_list_widget.count()):
            item = self.employee_list_widget.item(i)
            if not hasattr(item, 'info_label'):
                continue
            zones = [z for z in self.project.zones if z.employee_index == i]
            total_area = 0.0
            room_details = []
            for z in zones:
                for rid in z.room_ids:
                    room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                    if room:
                        total_area += room.area_m2
                        type_str = f" ({room.room_type})" if room.room_type else ""
                        room_details.append(f"- {room.name}{type_str} {room.area_m2:.1f} м²")
            name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i + 1}"
            text = f"{name} ({total_area:.1f} м²)\n" + "\n".join(room_details)
            if total_area > 100:
                text = f"<font color='red'>{text}</font>"
            item.info_label.setText(text)
            if hasattr(item, 'widget_ref'):
                item.widget_ref.adjustSize()
                item.setSizeHint(item.widget_ref.sizeHint())

    # ---------- Экран отчёта ----------
    def load_report_screen(self):
        cost = calculate_cost(self.project)
        text = f"<h2>{self.project.name}</h2>"
        text += f"<p>Общее время уборки: {cost['total_time_hours']} ч</p>"
        text += f"<p>Затраты (штат с переработкой): {cost['cost_with_overtime']} руб</p>"
        text += f"<p>Затраты (наём): {cost['cost_hire']} руб</p>"
        text += f"<p><b>Рекомендация: {cost['recommendation']}</b></p>"

        if hasattr(self, 'last_unscheduled') and self.last_unscheduled:
            text += "<h3 style='color:red;'>Не запланированные уборки</h3>"
            text += "<ul>"
            for u in self.last_unscheduled:
                room_name = u.get('room_name', f"Комната {u['room'][1] + 1}")
                reason = u.get('reason', 'Неизвестная причина')
                critical = " (критичная!)" if u.get('critical', False) else ""
                text += f"<li>{room_name}{critical} – {reason}</li>"
            text += "</ul>"

        text += "<h3>Расписание уборки</h3>"
        tasks_by_emp = {}
        for task in self.project.cleaning_tasks:
            tasks_by_emp.setdefault(task.employee, []).append(task)
        for emp_idx, tasks in tasks_by_emp.items():
            name = self.project.employee_names[emp_idx] if emp_idx < len(self.project.employee_names) else f"Сотрудник {emp_idx + 1}"
            text += f"<h4>{name}</h4>"
            text += "<table border='1' cellspacing='0' cellpadding='4'><tr><th>№</th><th>Комната</th><th>Площадь (м²)</th><th>Начало</th><th>Конец</th><th>Длит.</th></tr>"
            for t in tasks[:50]:
                room = self._find_room_by_id(t.room_id)
                room_name = room.name if room else str(t.room_id)
                if room and room.room_type:
                    room_name += f" ({room.room_type})"
                area = f"{room.area_m2:.0f}" if room else "—"
                dur = (t.end_dt - t.start_dt).seconds // 60
                text += f"<tr><td>{t.room_id + 1}</td><td>{room_name}</td><td>{area}</td><td>{t.start_dt.strftime('%H:%M')}</td><td>{t.end_dt.strftime('%H:%M')}</td><td>{dur} мин</td></tr>"
            text += "</table>"
        self.report_preview.setHtml(text)

    def _find_room_by_id(self, room_id):
        for floor in self.project.floors:
            for room in floor.rooms:
                if room.id == room_id:
                    return room
        return None

    # ---------- Сохранение / Экспорт ----------
    def save_project(self):
        if not self.project:
            return
        if self.current_project_path:
            path = self.current_project_path
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", PROJECTS_DIR, "JSON (*.json)")
        if path:
            self.project.save_to_file(path)
            self.current_project_path = path
            QMessageBox.information(self, "Успех", f"Проект сохранён в {path}")
            self.start_screen.refresh_project_list()

    def generate_docx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", "", "Word (*.docx)")
        if path:
            try:
                generate_report(self.project, path)
                QMessageBox.information(self, "Готово", f"Отчёт сохранён: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

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
    def load_norms_screen(self):
        self.norms_screen.load_norms_screen()

    # ---------- go_to_zone_screen ----------
    def go_to_zone_screen(self):
        if not self.project:
            return
        total_text = (self.param_total_area.text() or "").strip()
        self.project.total_area_m2 = float(total_text) if total_text else 0.0
        self.project.employees_count = self.param_employees.value()
        self.project.hourly_rate = self.param_rate.value()
        weather_text = self.weather_combo.currentText()
        if "1.2" in weather_text:
            self.project.weather_factor = 1.2
        elif "1.5" in weather_text:
            self.project.weather_factor = 1.5
        elif "1.8" in weather_text:
            self.project.weather_factor = 1.8
        else:
            self.project.weather_factor = 1.0
        self._scale_rooms()
        all_rooms = self.project.all_rooms()
        if not all_rooms:
            QMessageBox.warning(self, "Ошибка", "Нет комнат ни на одном этаже.")
            return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Не задана площадь комнат.")
            return
        while len(self.project.employee_names) < self.project.employees_count:
            self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names) + 1}")
        self.project.employee_names = self.project.employee_names[:self.project.employees_count]
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.load_zone_screen()
        self.stack.setCurrentIndex(2)

    # ---------- Дополнительные прокси ----------
    @property
    def room_table_collapsed(self):
        return self.plan_screen.room_table_collapsed

    @room_table_collapsed.setter
    def room_table_collapsed(self, value):
        self.plan_screen.room_table_collapsed = value