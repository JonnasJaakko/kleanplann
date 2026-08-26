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
from zone_manager import (manual_distribution, distribute_project_zones, PRIORITY_BALANCED,
                          PRIORITY_PROXIMITY, PRIORITY_AREA, PRIORITY_COUNT, PRIORITY_TIME)
from cost_calculator import calculate_cost, estimate_required_employees
from report_generator import generate_report, ReportGenerationError
from schedule_validator import validate_schedule
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY, DEFAULT_TRAFFIC_PER_TYPE
from scheduler import plan_cleaning_schedule, plan_cleaning_period, schedule_single_shift, compute_recommended_employees
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
        self.norms_screen = NormsScreen(self)

        self.stack.addWidget(self.start_screen)
        self.stack.addWidget(self.plan_screen)
        self.stack.addWidget(self.zone_screen)
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
        self.param_employees = self.plan_screen.param_employees
        self.floor_area = self.plan_screen.floor_area
        self.opacity_slider = self.plan_screen.opacity_slider
        self.fill_mode_combo = self.plan_screen.fill_mode_combo
        self.room_table = self.plan_screen.room_table
        self.sort_combo = self.plan_screen.sort_combo
        self.floor_combo = self.plan_screen.floor_combo
        self.btn_finish_floors = self.plan_screen.btn_finish_floors

        self.zone_view = self.zone_screen.zone_view
        self.zone_scene = self.zone_screen.zone_scene
        self.priority_combo = self.zone_screen.priority_combo
        self.report_preview = self.zone_screen.report_preview
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
  <li>В редакторе откройте <b>Настройки проекта</b> и задайте площадь, оплату,
      надбавку за переработку, тип уборки, погоду, смену и обед.</li>
  <li>Нажмите <b>Загрузить план</b> и выберите DXF или одно/несколько изображений.
      Для изображений можно автоматически распознать стены.</li>
  <li>Укажите количество сотрудников или нажмите <b>Авторасчёт персонала</b>.</li>
  <li>Нажмите <b>Создать расписание</b>. На следующем экране зоны и отчётность
      находятся вместе; изменение режима автоматически пересоздаёт расписание.</li>
  <li>Режим <b>Время</b> выравнивает рабочую нагрузку сотрудников и старается синхронизировать их время окончания.</li>
  <li>В отчёте нажмите на комнату, чтобы изменить сотрудника и желаемое начало уборки.
      Чекбокс <b>Зафиксировать</b> сохраняет выбранный слот при следующих пересчётах.</li>
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
        self.stack.setCurrentIndex(3)

    # ---------- Экран плана ----------
    def load_plan_screen(self):
        self.param_employees.setValue(max(1, int(self.project.employees_count)))
        self._sync_floor_area_widget()
        # Совместимость со старыми проектами: привязываем сохранённые
        # изображения к соответствующим этажам.
        for i, path in enumerate(getattr(self.project, 'image_paths', []) or []):
            if i < len(self.project.floors) and not getattr(self.project.floors[i], 'image_path', ''):
                self.project.floors[i].image_path = path
        self.update_floor_combo()
        self.refresh_plan_view()

    def remove_image(self):
        self.delete_plan_data()

    def delete_plan_data(self):
        if not self.project:
            return
        self.project.image_paths = []
        self.project.floors = [Floor(0, "Этаж 1")]
        self.project.current_floor_index = 0
        self.project.zones = []
        self.project.cleaning_tasks = []
        self.project.is_dxf_loaded = False
        self.temp_dxf_path = None
        self.temp_dxf_segments = []
        self.floor_rects = []
        self.refresh_plan_view()
        self.update_floor_combo()

    def open_project_settings(self):
        from screens.plan_screen import ProjectSettingsDialog
        if not self.project: return
        dlg=ProjectSettingsDialog(self)
        if dlg.exec()==QDialog.Accepted:
            v=dlg.values(); p=self.project
            p.total_area_m2=v['total_area']; p.salary_type=v['salary_type']; p.salary_value=v['salary_value']; p.hourly_rate=v['salary_value'] if v['salary_type']=='hour' else p.hourly_rate
            p.overtime_type=v['overtime_type']; p.overtime_value=v['overtime_value']; p.overtime_premium_percent=v['overtime_value'] if v['overtime_type']=='percent' else 0.0
            p.cleaning_type=v['cleaning_type']; p.weather_factor=v['weather_factor']; p.shifts=[Shift('Основная',v['shift_start'],v['shift_end'])]; p.breaks=[(v['lunch_start'],v['lunch_end'])]; p.overtime_limit=v['overtime_limit']
            self.refresh_plan_view()

    def load_plan_universal(self):
        if not self.project:
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Загрузить план")
        dlg.setText("Выберите формат плана")
        dxf_btn = dlg.addButton("DXF", QMessageBox.AcceptRole)
        img_btn = dlg.addButton("Изображения", QMessageBox.ActionRole)
        cancel_btn = dlg.addButton(QMessageBox.Cancel)
        dlg.exec()
        clicked = dlg.clickedButton()
        if clicked == dxf_btn:
            self.load_dxf()
        elif clicked == img_btn:
            self.load_images()

    def load_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Загрузить планы этажей", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not paths:
            return
        choice = QMessageBox(self)
        choice.setWindowTitle("Распознавание стен")
        choice.setText("Распознать стены автоматически?")
        one = choice.addButton("Да, этот план", QMessageBox.AcceptRole)
        all_btn = choice.addButton("Да, для всех планов", QMessageBox.AcceptRole)
        no = choice.addButton("Нет", QMessageBox.RejectRole)
        choice.exec()
        clicked = choice.clickedButton()
        detect_all = clicked == all_btn
        detect_one = clicked == one
        self.project.image_paths = list(paths)
        self.project.floors = []
        for i, path in enumerate(paths):
            floor = Floor(i, f"Этаж {i + 1}")
            floor.image_path = path
            self.project.floors.append(floor)
            if detect_all or (detect_one and i == 0):
                try:
                    from image_processor import load_image, detect_floor_plan
                    from screens.plan_screen import ROOM_COLORS
                    result = detect_floor_plan(load_image(path))
                    for rid, candidate in enumerate(result["rooms"]):
                        pts = candidate["points"]
                        rtype = candidate.get("room_type", "")
                        room = Room(rid, pts, color=ROOM_COLORS[rid % len(ROOM_COLORS)], room_type=rtype, floor_index=i)
                        room.traffic = DEFAULT_TRAFFIC_PER_TYPE.get(rtype, 10)
                        floor.rooms.append(room)
                        for j in range(len(pts)):
                            x1,y1=pts[j]; x2,y2=pts[(j+1)%len(pts)]
                            floor.walls.append(Wall(x1,y1,x2,y2))
                except Exception as e:
                    QMessageBox.warning(self, "Ошибка распознавания", f"{os.path.basename(path)}: {e}")
            floor.total_area_m2=sum(float(r.area_m2) for r in floor.rooms)
        self.project.current_floor_index = 0
        self.project.total_area_m2 = sum(f.total_area_m2 for f in self.project.floors)
        self.project.is_dxf_loaded = False
        if self.project.total_area_m2 > 0 and self.project.all_rooms():
            self._scale_all_rooms()
        self.update_floor_combo(); self.refresh_plan_view()
        if detect_one and len(paths) > 1:
            QMessageBox.information(self, "Планы загружены", "Распознавание выполнено только для первого плана. Для остальных этажей можно запустить распознавание вручную.")


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
                room = Room(i, pts, area_m2=0.0, floor_index=floor.index)
                px_area = self._polygon_area(pts)
                room.area_m2 = px_area
                total_area += px_area
                floor.rooms.append(room)
            floor.total_area_m2 = total_area
            self.project.floors.append(floor)
        self.project.total_area_m2 = sum(f.total_area_m2 for f in self.project.floors)
        self.project.current_floor_index = len(self.project.floors) - len(self.floor_rects)
        self.update_floor_combo()
        self.plan_scene.clear()
        self.refresh_plan_view()
        self.btn_finish_floors.setVisible(False)
        self.floor_rects = []
        self.temp_dxf_segments = []
        self.plan_view.set_tool(0)

    def add_floor(self):
        if not self.project: return
        idx=len(self.project.floors); floor=Floor(idx,f'Этаж {idx+1}'); self.project.floors.append(floor); self.project.current_floor_index=idx
        self.update_floor_combo(); self._sync_floor_area_widget(); self.refresh_plan_view()

    def delete_floor(self):
        if not self.project or len(self.project.floors)<=1:
            QMessageBox.warning(self,'Удаление этажа','В проекте должен остаться хотя бы один этаж.'); return
        floor=self.project.current_floor
        if QMessageBox.question(self,'Удалить этаж',f'Точно удалить «{floor.name}»? Все комнаты, стены, изображение, назначения и расписание этого этажа будут удалены.')!=QMessageBox.Yes: return
        fi=self.project.current_floor_index
        del self.project.floors[fi]
        for i,f in enumerate(self.project.floors):
            f.index=i
            for r in f.rooms: r.floor_index=i
        self.project.current_floor_index=min(fi,len(self.project.floors)-1)
        self.project.zones=[z for z in self.project.zones if getattr(z,'floor_index',0)!=fi]
        new_assignments={}
        for k,v in self.project.manual_assignments.items():
            try: kfi,rid=map(int,k.split(':',1))
            except: continue
            if kfi==fi: continue
            if kfi>fi: kfi-=1
            new_assignments[f'{kfi}:{rid}']=v
        self.project.manual_assignments=new_assignments
        self.project.cleaning_tasks=[t for t in self.project.cleaning_tasks if t.floor_index!=fi]
        for t in self.project.cleaning_tasks:
            if t.floor_index>fi: t.floor_index-=1
        self.update_floor_combo(); self._sync_floor_area_widget(); self.refresh_plan_view()

    def update_floor_area(self,value):
        if self.project and self.project.floors:
            self.project.current_floor.total_area_m2=float(value)
            self._scale_rooms()
            self.project.total_area_m2=sum(float(f.total_area_m2) for f in self.project.floors)

    def _sync_floor_area_widget(self):
        if not self.project: return
        self.floor_area.blockSignals(True); self.floor_area.setValue(float(self.project.current_floor.total_area_m2)); self.floor_area.blockSignals(False)

    def update_floor_combo(self):
        combos = [self.floor_combo]
        if hasattr(self, 'zone_screen') and hasattr(self.zone_screen, 'floor_combo'):
            combos.append(self.zone_screen.floor_combo)
        for combo in combos:
            combo.blockSignals(True); combo.clear()
            for floor in self.project.floors:
                combo.addItem(floor.name)
            combo.setCurrentIndex(self.project.current_floor_index); combo.blockSignals(False)

    def switch_floor(self, idx):
        if idx >= 0 and idx < len(self.project.floors):
            self.project.current_floor_index = idx
            floor = self.project.current_floor
            self.refresh_plan_view()
            self._sync_floor_area_widget()
            if self.stack.currentIndex() == 2:
                self.refresh_zone_display()
                self.load_report_screen()

    def refresh_plan_view(self):
        scene = self.plan_screen.plan_view.scene()
        scene.clear()
        floor_image = getattr(self.project.current_floor, "image_path", None) if self.project and self.project.floors else None
        if floor_image:
            pix = QPixmap(floor_image)
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
            elif room.room_type == "лестница":
                bg_color = QColor(120, 80, 40)
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
            self.room_table.setItem(row, 3, QTableWidgetItem(f'{room.area_m2:.1f}'))
            self.room_table.setItem(row, 4, QTableWidgetItem(str(int(room.traffic))))
            self.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def on_room_table_hover(self, row, col):
        item = self.room_table.item(row, 0)
        if item:
            self.plan_view.highlight_room(item.data(Qt.UserRole))


    def on_room_table_clicked(self,row,col):
        item=self.room_table.item(row,0)
        if item: self.edit_room_properties(item.data(Qt.UserRole), self.project.current_floor_index)

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
            self.room_table.setItem(row, 3, QTableWidgetItem(f'{room.area_m2:.1f}'))
            self.room_table.setItem(row, 4, QTableWidgetItem(str(int(room.traffic))))

    def update_room_opacity(self):
        self.draw_rooms()

    def on_scene_changed(self):
        self.project.walls = self.plan_view.collect_walls()
        self.build_rooms_from_project_walls()
        self.draw_rooms()
        self.update_room_table()
        current_floor = self.project.current_floor
        current_floor.total_area_m2 = sum(r.area_m2 for r in current_floor.rooms)

    def straighten_walls(self):
        """Snap close endpoints and align nearly horizontal or vertical walls."""
        if not self.project or not self.project.walls:
            return
        walls = self.project.walls
        tolerance, angle_tolerance = 12.0, 0.12
        points = [(w.x1, w.y1) for w in walls] + [(w.x2, w.y2) for w in walls]
        groups = []
        for point in points:
            for group in groups:
                gx = sum(p[0] for p in group) / len(group); gy = sum(p[1] for p in group) / len(group)
                if math.hypot(point[0] - gx, point[1] - gy) <= tolerance:
                    group.append(point); break
            else:
                groups.append([point])
        def snap(point):
            group = min(groups, key=lambda g: min(math.hypot(point[0]-p[0], point[1]-p[1]) for p in g))
            return (sum(p[0] for p in group) / len(group), sum(p[1] for p in group) / len(group))
        for wall in walls:
            x1, y1 = snap((wall.x1, wall.y1)); x2, y2 = snap((wall.x2, wall.y2))
            dx, dy = x2 - x1, y2 - y1
            if abs(dy) <= max(tolerance, abs(dx) * angle_tolerance):
                y1 = y2 = (y1 + y2) / 2
            elif abs(dx) <= max(tolerance, abs(dy) * angle_tolerance):
                x1 = x2 = (x1 + x2) / 2
            wall.x1, wall.y1, wall.x2, wall.y2 = x1, y1, x2, y2
        self.refresh_plan_view()
        

    def detect_walls_cv(self):
        if not self.project or not getattr(self.project.current_floor,'image_path',''):
            QMessageBox.warning(self, "Ошибка", "Для текущего этажа нет изображения.")
            return
        try:
            from image_processor import load_image, detect_floor_plan
            img = load_image(self.project.current_floor.image_path)
            result = detect_floor_plan(img)
            self.project.rooms = []
            self.project.walls = []
            from screens.plan_screen import ROOM_COLORS
            for i, candidate in enumerate(result["rooms"]):
                pts = candidate["points"]; rtype = candidate.get("room_type", "")
                color = ROOM_COLORS[i % len(ROOM_COLORS)]
                room = Room(i, pts, color=color, room_type=rtype, floor_index=self.project.current_floor_index)
                room.traffic = DEFAULT_TRAFFIC_PER_TYPE.get(rtype, 10)
                self.project.rooms.append(room)
                for j in range(len(pts)):
                    x1, y1 = pts[j]; x2, y2 = pts[(j + 1) % len(pts)]
                    self.project.walls.append(Wall(x1, y1, x2, y2))
            self.project.current_floor.total_area_m2=sum(r.area_m2 for r in self.project.rooms)
            self.project.total_area_m2=sum(f.total_area_m2 for f in self.project.floors)
            self._scale_rooms()
            self.refresh_plan_view()
            QMessageBox.information(self, "Готово", f"Распознано {len(self.project.rooms)} помещений.")
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
        # Each floor has its own physical area and must be scaled independently.
        floor = self.project.current_floor
        total_area = float(getattr(floor, "total_area_m2", 0.0) or 0.0)
        rooms = [r for r in floor.rooms if not getattr(r, "disabled", False)]
        total_px = sum(self._polygon_area(r.points) for r in rooms)
        if total_area <= 0 or total_px <= 0:
            return
        factor = total_area / total_px
        for room in rooms:
            room.area_m2 = self._polygon_area(room.points) * factor
        self.project.total_area_m2 = sum(float(f.total_area_m2) for f in self.project.floors)

    def _scale_all_rooms(self):
        current = self.project.current_floor_index
        for index in range(len(self.project.floors)):
            self.project.current_floor_index = index
            self._scale_rooms()
        self.project.current_floor_index = current

    def _polygon_area(self, points):
        n = len(points)
        area = 0.0
        for i in range(n):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0

    def edit_room_properties(self, room_id, floor_index=None):
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QSpinBox,
                                       QDoubleSpinBox, QComboBox, QCheckBox,
                                       QDialogButtonBox)
        fi = self.project.current_floor_index if floor_index is None else int(floor_index)
        floor = self.project.floors[fi] if 0 <= fi < len(self.project.floors) else None
        room = next((r for r in floor.rooms if r.id == room_id), None) if floor else None
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
            if any(r.id == new_num - 1 and r != room for r in floor.rooms):
                QMessageBox.warning(self, "Ошибка", "Номер уже используется.")
                return
            room.id = new_num - 1
            room.name = name_edit.text() or f"Комната {room.id + 1}"
            room.area_m2 = area_spin.value()
            room.traffic = traffic_spin.value()
            room.room_type = type_combo.currentText()
            self.draw_rooms()
            self.update_room_table()

    def auto_classify_rooms(self):
        """Автоматически назначает типы помещений по геометрии и распознаванию изображения."""
        if not self.project or not self.project.current_floor.rooms:
            QMessageBox.warning(self, "Автоопределение типов", "На текущем этаже нет помещений.")
            return
        floor = self.project.current_floor
        try:
            from image_processor import load_image, detect_floor_plan
            candidates = []
            if floor.image_path and os.path.exists(floor.image_path):
                candidates = detect_floor_plan(load_image(floor.image_path))["rooms"]
        except Exception:
            candidates = []

        def center(points):
            return (sum(x for x,y in points)/len(points), sum(y for x,y in points)/len(points))
        def area_px(points):
            return abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points)))/2)
        # Сначала переносим уверенные типы CV на ближайшие уже существующие комнаты.
        used=set()
        for cand in candidates:
            cp=center(cand["points"])
            best=None
            for r in floor.rooms:
                if r.id in used: continue
                rc=center(r.points); d=math.hypot(cp[0]-rc[0],cp[1]-rc[1])
                if best is None or d<best[0]: best=(d,r)
            if best and best[0] < max(30.0, math.sqrt(max(1,cand.get("area_px",1)))*0.35):
                r=best[1]; r.room_type=cand.get("room_type",""); used.add(r.id)

        rooms=floor.rooms
        median_area = sorted([max(0.01,r.area_m2) for r in rooms])[len(rooms)//2] if rooms else 1
        centers={r.id:center(r.points) for r in rooms}
        # Геометрические правила поверх CV. Площадь 10 м² — именно реальное
        # условие для кандидата в санузел после калибровки.
        for r in rooms:
            xs=[p[0] for p in r.points]; ys=[p[1] for p in r.points]
            bw=max(xs)-min(xs); bh=max(ys)-min(ys); aspect=max(bw,bh)/max(1e-6,min(bw,bh))
            compact=max(0.0,min(1.0,r.area_m2/max(0.01,bw*bh)))
            if r.room_type=="лестница":
                pass
            elif r.area_m2 < 10.0:
                # Близость к коридору: расстояние между центрами не должно быть
                # больше примерно двух диагоналей маленькой комнаты.
                near_corridor=False
                for other in rooms:
                    if other is r or other.room_type != "коридор": continue
                    d=math.hypot(centers[r.id][0]-centers[other.id][0], centers[r.id][1]-centers[other.id][1])
                    if d <= max(bw,bh)*3.0: near_corridor=True; break
                if near_corridor or r.room_type=="санузел": r.room_type="санузел"
            elif aspect >= 3.0 or (aspect >= 2.2 and compact < 0.75):
                r.room_type="коридор"
            elif r.area_m2 >= max(40.0, median_area*2.5):
                r.room_type="зал"
            elif r.room_type not in COMPLEXITY_FACTOR or not r.room_type:
                r.room_type="кабинет"
            if r.room_type in DEFAULT_TRAFFIC_PER_TYPE and (r.traffic <= 0 or r.traffic == 10):
                r.traffic=DEFAULT_TRAFFIC_PER_TYPE[r.room_type]
        floor.total_area_m2=sum(r.area_m2 for r in floor.rooms)
        self.project.total_area_m2=sum(f.total_area_m2 for f in self.project.floors)
        self.draw_rooms(); self.update_room_table(); self._sync_floor_area_widget()
        QMessageBox.information(self,"Автоопределение типов",f"Обработано помещений: {len(rooms)}.")

    def auto_calculate_staff(self):
        if not self.project or not self.project.all_rooms():
            QMessageBox.warning(self, "Авторасчёт персонала", "Сначала загрузите план с распознанными помещениями.")
            return
        result = estimate_required_employees(self.project)
        recommended = int(result["employees"])
        self.param_employees.setValue(recommended)
        self.project.employees_count = recommended
        while len(self.project.employee_names) < recommended:
            self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names)+1}")
        self.project.employee_names = self.project.employee_names[:recommended]

        # После автоматической оценки сразу строим соответствующие зоны.
        active = [r for r in self.project.all_rooms() if not getattr(r, 'disabled', False)]
        priority = self.project.priority_mode or PRIORITY_BALANCED
        self.project.zones = self._distribute_active_rooms(active, priority)
        self._apply_manual_assignments()

        tested = result.get("tested", {})
        tested_text = ", ".join(f"{n}: {'✓' if ok else '✗'}" for n, ok in sorted(tested.items()))
        minimum = int(result.get("minimum_by_capacity", recommended))
        QMessageBox.information(self, "Авторасчёт персонала",
            f"Рекомендуемый штат: {recommended} чел.\n"
            f"Минимально выполнимый: {result.get('minimum_feasible') or '—'} чел.\n"
            f"Без переработки: {result.get('zero_overtime_employees') or '—'} чел.\n\n"
            f"Нормативная трудоёмкость: {result['daily_minutes']:.0f} мин. в день\n"
            f"Рабочая ёмкость одного сотрудника: {result['capacity_minutes']:.0f} мин.\n"
            f"Теоретический нижний порог: {minimum} чел.\n\n"
            f"Проверенные варианты: {tested_text or 'нет данных'}\n\n"
            f"Причина: {result.get('reason', '')}")

    # ---------- Планирование ----------
    def go_to_planning_screen(self, skip_check=False):
        if not self.project: return
        active=[r for r in self.project.all_rooms() if not getattr(r,'disabled',False)]
        if not active: QMessageBox.warning(self,'Ошибка','Нет активных помещений.'); return
        self.project.employees_count=self.param_employees.value()
        while len(self.project.employee_names)<self.project.employees_count: self.project.employee_names.append(f'Сотрудник {len(self.project.employee_names)+1}')
        self.project.employee_names=self.project.employee_names[:self.project.employees_count]
        priority=self.project.priority_mode or PRIORITY_BALANCED
        self.project.zones=self._distribute_active_rooms(active, priority); self._apply_manual_assignments()
        if skip_check: self._generate_schedule_and_show(); return
        from cost_calculator import estimate_required_employees
        recommended=estimate_required_employees(self.project)['employees']
        # Проверяем только если штат потенциально недостаточен.
        if recommended>self.project.employees_count:
            dlg=QMessageBox(self); dlg.setWindowTitle('Недостаточно сотрудников'); dlg.setText(f'Расчёт показывает, что для объекта желательно {recommended} сотрудников. Сейчас: {self.project.employees_count}.')
            inc=dlg.addButton(f'Увеличить до {recommended}',QMessageBox.AcceptRole); keep=dlg.addButton('Продолжить с текущим',QMessageBox.ActionRole); exc=dlg.addButton('Исключить комнаты',QMessageBox.ActionRole); dlg.addButton(QMessageBox.Cancel); dlg.exec(); c=dlg.clickedButton()
            if c==inc:
                self.param_employees.setValue(recommended); self.project.employees_count=recommended; self.project.employee_names=[f'Сотрудник {i+1}' for i in range(recommended)]; self.project.zones=self._distribute_active_rooms(active, priority); self._apply_manual_assignments(); self._generate_schedule_and_show(); return
            if c==exc: self._manual_exclude_mode(); return
            if c is None or c==dlg.button(QMessageBox.Cancel): return
        self._generate_schedule_and_show()

    def _generate_schedule_and_show(self):
        # Production-сценарий: расписание всегда только на выбранную дату.
        result = schedule_single_shift(
            self.project,
            target_date=self.project.start_date,
            employees=self.project.employees_count,
            allow_partial_schedule=True,
        )
        self.project.cleaning_tasks = list(result.get("tasks", []))
        self.last_unscheduled = list(result.get("unscheduled_rooms_list", []))
        self.refresh_zone_display()
        self.load_report_screen()
        self.stack.setCurrentIndex(2)

    def _distribute_active_rooms(self, active_rooms, priority):
        """Единое распределение зон с учётом норматива, погоды и типа уборки."""
        if not self.project:
            return []
        # disabled-комнаты уже исключаются helper'ом. active_rooms оставлен в
        # сигнатуре для обратной совместимости с GUI.
        return distribute_project_zones(self.project, self.project.employees_count, priority)

    def back_to_editor(self):
        self.stack.setCurrentIndex(1)

    def toggle_excluded_room_at_scene(self, scene_pos):
        if not self._exclude_mode_active or not self.project: return
        floor=self.project.current_floor
        for room in floor.rooms:
            poly=QPolygonF([QPointF(x,y) for x,y in room.points])
            if poly.containsPoint(scene_pos, Qt.OddEvenFill):
                room.disabled=not room.disabled
                for item in self.zone_view.scene().items():
                    if isinstance(item,QGraphicsPolygonItem) and item.data(Qt.UserRole)==room.id and item.data(2)==floor.index:
                        item.setBrush(QBrush(QColor(220,50,50,150) if room.disabled else QColor(50,190,70,130)))
                        item.setPen(QPen(QColor(120,0,0) if room.disabled else QColor(0,100,0),2))
                return True
        return False

    def _manual_exclude_mode(self):
        if self.stack.currentIndex() != 2:
            self.stack.setCurrentIndex(2)
        self._exclude_mode_active = True
        scene = self.zone_view.scene()
        # The confirmation action is a normal UI button, not a scene widget.
        # This avoids invalid Qt scene objects while the schedule is refreshed.
        self.zone_screen.exclude_finish_button.setVisible(True)
        self.refresh_zone_display()

        # Настраиваем клик по комнатам для исключения
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                room_id = item.data(Qt.UserRole); floor_index = item.data(2) if item.data(2) is not None else self.project.current_floor_index
                room = next((r for r in self.project.current_floor.rooms if r.id == room_id), None)
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

                item.setData(2, floor_index)
                # Клики перехватывает ZoneView, чтобы не зависеть от monkey-patch
                # mousePressEvent у QGraphicsPolygonItem.

    def _finish_exclude_and_continue(self):
        self._exclude_mode_active = False
        self.zone_screen.exclude_finish_button.setVisible(False)
        self.refresh_zone_display()

        active_rooms = [r for r in self.project.all_rooms() if not r.disabled]
        if not active_rooms:
            self.project.cleaning_tasks = []
            self.last_unscheduled = []
            self.load_report_screen()
            self.stack.setCurrentIndex(2)
            return

        self.project.zones = self._distribute_active_rooms(active_rooms, self.project.priority_mode)
        self._apply_manual_assignments()

        # Повторно строим расписание без повторного диалога о нехватке персонала.
        self._generate_schedule_and_show()

    def _apply_shift_and_lunch(self):
        if not self.project.shifts:
            self.project.shifts = [Shift("Основная", "08:00", "17:00")]
        if not self.project.breaks:
            self.project.breaks = [("12:00", "13:00")]

    # ---------- Экран зон / отчёт ----------
    def load_zone_screen(self):
        self.update_floor_combo()
        self.refresh_zone_display()
        self.load_report_screen()

    def recalculate_zones(self):
        if not self.project or not self.project.all_rooms():
            return
        active = [r for r in self.project.all_rooms() if not getattr(r, "disabled", False)]
        if not active:
            self.project.zones = []; self.project.cleaning_tasks = []; self.load_report_screen(); return
        self.project.employees_count = self.param_employees.value()
        priority = self.priority_combo.currentData()
        self.project.priority_mode = priority
        self.project.zones = self._distribute_active_rooms(active, priority)
        self._apply_manual_assignments()
        self._generate_schedule_and_show()

    def refresh_zone_display(self):
        scene=self.zone_view.scene(); scene.clear(); floor=self.project.current_floor if self.project and self.project.floors else None
        if not floor:return
        if getattr(floor,'image_path',''):
            pix=QPixmap(floor.image_path); scene.addPixmap(pix); self.zone_view.setSceneRect(QRectF(pix.rect()))
        for room in floor.rooms:
            zone=next((z for z in self.project.zones if getattr(z,'floor_index',0)==floor.index and room.id in z.room_ids),None)
            color=QColor(*(zone.color if zone else room.color)); poly=QPolygonF([QPointF(x,y) for x,y in room.points]); item=scene.addPolygon(poly,QPen(Qt.black,1),QBrush(color)); item.setData(Qt.UserRole,room.id); item.setData(2,floor.index); item.setAcceptHoverEvents(True)
            emp=zone.employee_index+1 if zone else 0; cx=sum(x for x,y in room.points)/len(room.points); cy=sum(y for x,y in room.points)/len(room.points)
            label=QGraphicsTextItem(f'{emp}\n{room.name}\n№{room.id+1} ({room.area_m2:.0f} м²)'); label.setDefaultTextColor(Qt.white); label.setPos(cx-40,cy-28); label.setFlag(QGraphicsItem.ItemIgnoresTransformations); label.setData(1,'zone_label'); label.setData(Qt.UserRole,room.id); label.setData(2,floor.index); label.setAcceptHoverEvents(True)
            label.hoverEnterEvent=lambda ev,rid=room.id,fi=floor.index,emp=(zone.employee_index if zone else 0): QToolTip.showText(ev.screenPos(),self._get_schedule_tip(rid,emp,fi))
            label.hoverLeaveEvent=lambda ev: QToolTip.hideText()
            label.mousePressEvent=lambda ev,rid=room.id,fi=floor.index:self.edit_room_schedule(fi, rid, 0)
            label.setCursor(Qt.PointingHandCursor)
            scene.addItem(label)
            item.hoverEnterEvent=lambda ev,rid=room.id,fi=floor.index,emp=(zone.employee_index if zone else 0): QToolTip.showText(ev.screenPos(),self._get_schedule_tip(rid,emp,fi))
            item.hoverLeaveEvent=lambda ev: QToolTip.hideText()
            item.mousePressEvent=lambda ev,rid=room.id,fi=floor.index:self.edit_room_schedule(fi, rid, 0)
            item.setCursor(Qt.PointingHandCursor)
        rect=scene.itemsBoundingRect()
        if rect.width()>0 and rect.height()>0:self.zone_view.setSceneRect(rect); self.zone_view.fitInView(rect,Qt.KeepAspectRatio)

    def highlight_zone_employee(self,floor_index,room_id):
        if floor_index is None:return
        zone=next((z for z in self.project.zones if getattr(z,'floor_index',0)==int(floor_index) and room_id in z.room_ids),None)
        if not zone:return
        for item in self.zone_view.scene().items():
            if isinstance(item,QGraphicsPolygonItem) and item.data(2)==int(floor_index):
                rid=item.data(Qt.UserRole); col=item.brush().color(); col.setAlpha(210 if rid in zone.room_ids else 35); item.setBrush(QBrush(col))
    def clear_zone_employee_highlight(self):
        if not self.project:return
        fi=self.project.current_floor_index
        for item in self.zone_view.scene().items():
            if isinstance(item,QGraphicsPolygonItem) and item.data(2)==fi:
                rid=item.data(Qt.UserRole); z=next((z for z in self.project.zones if getattr(z,'floor_index',0)==fi and rid in z.room_ids),None); item.setBrush(QBrush(QColor(*(z.color if z else (200,200,200,60)))))

    def _get_schedule_tip(self, room_id, emp_idx, floor_index=None):
        tasks=[t for t in self.project.cleaning_tasks if t.room_id==room_id and t.employee==emp_idx and (floor_index is None or t.floor_index==floor_index)]
        if not tasks:return 'Нет назначенных уборок'
        return 'Уборки комнаты:\n'+'\n'.join(f'{t.start_dt:%H:%M}–{t.end_dt:%H:%M}' for t in sorted(tasks,key=lambda x:x.start_dt))

    def handle_report_room_link(self, url):
        """Обрабатывает клики по комнате в отчёте справа."""
        try:
            parts = str(url.toString()).split(':', 1)[1].split('/')
            fi, rid = int(parts[0]), int(parts[1])
            occurrence = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            return
        self.edit_room_schedule(fi, rid, occurrence)

    def edit_room_schedule(self, floor_index, room_id, selected_occurrence=0):
        """Редактор сотрудника/времени для конкретной нормативной уборки.

        В диалоге пользователь может:
        - выбрать сотрудника;
        - задать новое время;
        - включить «Зафиксировать», чтобы конкретный слот больше не двигался
          при последующих пересчётах.
        """
        if not self.project:
            return
        fi = int(floor_index)
        floor = self.project.floors[fi] if 0 <= fi < len(self.project.floors) else None
        room = next((r for r in floor.rooms if r.id == int(room_id)), None) if floor else None
        if not room:
            return

        tasks = sorted(
            [t for t in self.project.cleaning_tasks if t.floor_index == fi and t.room_id == int(room_id)],
            key=lambda t: t.start_dt,
        )
        # Если нормативную уборку ещё не удалось поставить, сообщаем об этом:
        # пользователь всё равно может назначить предпочтение для следующего пересчёта.
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QComboBox, QTimeEdit, QCheckBox, QDialogButtonBox,
            QSpinBox
        )
        from PySide6.QtCore import QTime

        dlg = QDialog(self)
        dlg.setWindowTitle(f'Настройка уборки: {room.name} (этаж {fi + 1})')
        dlg.setMinimumWidth(460)
        form = QFormLayout(dlg)

        occurrence_box = QComboBox()
        task_count = max(1, len(tasks))
        for idx in range(task_count):
            if idx < len(tasks):
                t = tasks[idx]
                occurrence_box.addItem(
                    f'Уборка {idx + 1}: {t.start_dt:%H:%M}–{t.end_dt:%H:%M}', idx
                )
            else:
                occurrence_box.addItem(f'Уборка {idx + 1}: новая', idx)
        occurrence_box.setCurrentIndex(max(0, min(int(selected_occurrence), occurrence_box.count() - 1)))
        form.addRow('Уборка:', occurrence_box)

        employee_box = QComboBox()
        names = self.project.employee_names[:self.project.employees_count]
        employee_box.addItems(names)
        form.addRow('Сотрудник:', employee_box)

        time_edit = QTimeEdit()
        time_edit.setDisplayFormat('HH:mm')
        time_edit.setTime(QTime(9, 0))
        # QTimeEdit не поддерживает setSingleStep(); оставляем точный ввод времени.
        # При необходимости пользователь может указать любую минуту, а scheduler
        # сам проверит допустимость окна, обед и зафиксированные ограничения.
        form.addRow('Желаемое начало:', time_edit)

        fixed_check = QCheckBox('Зафиксировать время и сотрудника')
        fixed_check.setToolTip('При повторном пересчёте эта уборка не будет перенесена или переназначена.')
        form.addRow('', fixed_check)

        info = QLabel()
        info.setWordWrap(True)
        info.setStyleSheet('color:#555;')
        form.addRow('', info)

        def load_occurrence(idx):
            task = tasks[idx] if idx < len(tasks) else None
            if task:
                emp = int(task.employee)
                employee_box.setCurrentIndex(max(0, min(emp, employee_box.count() - 1)))
                time_edit.setTime(QTime(task.start_dt.hour, task.start_dt.minute))
                lock = self.project.schedule_locks.get(f'{fi}:{room_id}:{idx}', {}) if hasattr(self.project, 'schedule_locks') else {}
                fixed_check.setChecked(bool(lock.get('fixed', getattr(task, 'fixed', False))))
                info.setText(
                    f'Длительность: {(task.end_dt - task.start_dt).total_seconds() / 60:.0f} мин. '
                    f'Текущее окончание: {task.end_dt:%H:%M}.'
                )
            else:
                zone = next((z for z in self.project.zones if getattr(z, 'floor_index', 0) == fi and room_id in z.room_ids), None)
                emp = zone.employee_index if zone else 0
                employee_box.setCurrentIndex(max(0, min(emp, employee_box.count() - 1)))
                lock = self.project.schedule_locks.get(f'{fi}:{room_id}:{idx}', {}) if hasattr(self.project, 'schedule_locks') else {}
                if lock.get('start'):
                    try:
                        h, m = map(int, str(lock['start']).split(':'))
                        time_edit.setTime(QTime(h, m))
                    except Exception:
                        pass
                fixed_check.setChecked(bool(lock.get('fixed', False)))
                duration = get_room_duration_for_gui(self.project, room)
                info.setText(f'Нормативная длительность: {duration} мин.')

        def get_room_duration_for_gui(project, room_obj):
            from sanitarnorm import get_cleaning_time_minutes
            return int(round(get_cleaning_time_minutes(
                room_obj.room_type, room_obj.area_m2,
                getattr(project, 'weather_factor', 1.0),
                getattr(project, 'cleaning_type', 'поддерживающая'),
            )))

        occurrence_box.currentIndexChanged.connect(load_occurrence)
        load_occurrence(occurrence_box.currentData() or 0)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec() != QDialog.Accepted:
            return

        occurrence = int(occurrence_box.currentData() or 0)
        employee = int(employee_box.currentIndex())
        start = time_edit.time().toString('HH:mm')
        # Храним предпочтение всегда. Если чекбокс снят, scheduler может сдвинуть
        # задачу при оптимизации, но начинает расчёт с выбранного времени.
        key = f'{fi}:{room_id}'
        self.project.manual_assignments[key] = employee
        if not hasattr(self.project, 'schedule_locks'):
            self.project.schedule_locks = {}
        self.project.schedule_locks[f'{fi}:{room_id}:{occurrence}'] = {
            'employee': employee,
            'start': start,
            'fixed': fixed_check.isChecked(),
        }
        # Перед повторным расчётом заново собрать зоны, но оставить ручные назначения.
        active = [r for r in self.project.all_rooms() if not getattr(r, 'disabled', False)]
        self.project.zones = self._distribute_active_rooms(active, self.project.priority_mode or PRIORITY_BALANCED)
        self._apply_manual_assignments()
        self._generate_schedule_and_show()

    def change_room_employee(self, room_id):
        """Совместимость со старым вызовом из карты: открываем новый редактор."""
        self.edit_room_schedule(self.project.current_floor_index, room_id, 0)

    def _apply_manual_assignments(self):
        if not self.project.zones:return
        for key,emp in self.project.manual_assignments.items():
            try: fi,rid=map(int,key.split(':',1))
            except: continue
            if not 0<=emp<self.project.employees_count: continue
            source=next((z for z in self.project.zones if getattr(z,'floor_index',0)==fi and rid in z.room_ids),None)
            target=next((z for z in self.project.zones if getattr(z,'floor_index',0)==fi and z.employee_index==emp),None)
            if source is target: continue
            if source: source.room_ids.remove(rid)
            if target: target.room_ids.append(rid)
            else:
                from zone_manager import ZONE_COLORS
                zid=max([z.id for z in self.project.zones],default=-1)+1
                self.project.zones.append(Zone(zid,f'Сотрудник {emp+1} — этаж {fi+1}',[rid],ZONE_COLORS[emp%len(ZONE_COLORS)],emp,fi))

    def load_report_screen(self):
        if not self.project:
            return
        cost = calculate_cost(self.project)
        tasks = list(self.project.cleaning_tasks or [])
        active_rooms = [r for r in self.project.all_rooms() if not getattr(r, "disabled", False)]
        scheduled_keys = {(t.floor_index, t.room_id) for t in tasks}
        missed = [r for r in active_rooms if (next((i for i,f in enumerate(self.project.floors) if r in f.rooms),0), r.id) not in scheduled_keys]
        disabled = [r for r in self.project.all_rooms() if getattr(r, "disabled", False)]
        def duration(minutes):
            minutes=int(round(minutes)); return f"{minutes//60} ч {minutes%60:02d} мин ({minutes/60:.2f} ч)"
        salary_names={'hour':'почасовая','fixed_shift':'за смену','per_sqm':'за м²'}
        overtime_names={'percent':'процентная надбавка','per_hour':'рублей за час'}
        text = f"<h2>{self.project.name}</h2>"
        text += f"<p><b>Дата расписания:</b> {self.project.start_date:%d.%m.%Y}</p>"
        text += f"<p><b>Погода:</b> {self._weather_name()} &nbsp; <b>Тип:</b> {self.project.cleaning_type}</p>"
        text += (f"<p><b>Автооценка штата:</b> {cost.get('needed_employees', self.project.employees_count)} чел. &nbsp; "
                 f"<b>Минимально выполнимо:</b> {cost.get('staffing_minimum_feasible') or '—'} &nbsp; "
                 f"<b>Без переработки:</b> {cost.get('staffing_zero_overtime') or '—'}</p>")
        text += f"<p><b>Смена:</b> {self.project.shifts[0].start_time if self.project.shifts else '—'}–{self.project.shifts[0].end_time if self.project.shifts else '—'} &nbsp; <b>Обед:</b> {self.project.breaks[0][0] if self.project.breaks else '—'}–{self.project.breaks[0][1] if self.project.breaks else '—'}</p>"
        text += f"<p><b>Общее время уборки:</b> {duration(cost['total_minutes'])} &nbsp; <b>Стоимость:</b> {cost['cost_with_overtime']:.2f} ₽ &nbsp; <b>Оплата:</b> {salary_names.get(cost.get('salary_type'),'не указана')} — {cost.get('salary_value'):.2f} ₽ &nbsp; <b>Надбавка:</b> {overtime_names.get(cost.get('overtime_type'),'не указана')} — {cost.get('overtime_value'):.2f}</p>"
        if disabled or missed:
            text += "<h3 style='color:#b00020'>Помещения без уборки</h3><ul>"
            for r in disabled: text += f"<li>№{r.id+1} {r.name} — исключено пользователем</li>"
            for r in missed: text += f"<li>№{r.id+1} {r.name} — не попало в расписание</li>"
            text += "</ul>"
        else:
            text += "<p style='color:#187a3b'><b>Все активные помещения получили нормативную уборку.</b></p>"

        if getattr(self.project, 'priority_mode', '') == PRIORITY_TIME:
            analytics = {}
            for t in tasks:
                analytics.setdefault(t.employee, []).append(t)
            ends = [max((x.end_dt for x in ts), default=None) for ts in analytics.values()]
            ends = [x for x in ends if x]
            loads = []
            for ts in analytics.values():
                loads.append(sum((x.end_dt-x.start_dt).total_seconds()/60 for x in ts) + sum(int(getattr(x,'transit_before_minutes',0)) for x in ts))
            if ends:
                end_spread = int((max(ends)-min(ends)).total_seconds()/60)
                text += f"<p><b>Режим «Время»:</b> разброс окончания смен {end_spread} мин.; разброс рабочей нагрузки {int(max(loads)-min(loads)) if loads else 0} мин.</p>"

        for emp in range(self.project.employees_count):
            emp_tasks=sorted([t for t in tasks if t.employee==emp], key=lambda x:x.start_dt)
            unique_rooms={(t.floor_index,t.room_id) for t in emp_tasks}
            area=sum(next((r.area_m2 for f in self.project.floors for r in f.rooms if r.id==rid and f.index==fi),0) for fi,rid in unique_rooms)
            worked=sum((t.end_dt-t.start_dt).total_seconds()/60 for t in emp_tasks)
            overtime=sum((t.end_dt-t.start_dt).total_seconds()/60 for t in emp_tasks if getattr(t,'is_overtime',False))
            name=self.project.employee_names[emp] if emp < len(self.project.employee_names) else f"Сотрудник {emp+1}"
            text += f"<h3>{name}</h3>"
            overtime_text=duration(overtime) if overtime else 'нет'
            pay=cost.get('employee_pay',{}).get(emp,0); base=cost.get('employee_base_pay',{}).get(emp,pay); extra=cost.get('employee_overtime_pay',{}).get(emp,0)
            text += f"<p><b>Время уборки:</b> {duration(worked)} &nbsp; <b>Комнат:</b> {len(unique_rooms)} &nbsp; <b>Площадь:</b> {area:.1f} м² &nbsp; <b>Переработка:</b> {overtime_text} &nbsp; <b>Оплата:</b> {pay:.2f} ₽ ({base:.2f} ₽ — за смену, {extra:.2f} ₽ — за переработку)</p>"
            floor_header='<th>Этаж</th>' if len(self.project.floors)>1 else ''
            text += f"<table border='1' cellspacing='0' cellpadding='4' width='100%'><tr>{floor_header}<th>№</th><th>Комната</th><th>Тип</th><th>Площадь (м²)</th><th>Начало</th><th>Конец</th><th>Длительность (мин)</th></tr>"
            for t in emp_tasks:
                room=next((r for f in self.project.floors for r in f.rooms if r.id==t.room_id and f.index==t.floor_index),None)
                row_style=" style='background:#f4cccc'" if getattr(t,'is_overtime',False) else ''
                floor_cell=f'<td>{t.floor_index+1}</td>' if len(self.project.floors)>1 else ''
                fixed_mark = ' 🔒' if getattr(t, 'fixed', False) else ''
                number_cell = f'<td style="text-align:center; width:8%">{t.room_id + 1}</td>'
                name_cell = f"<td style=\"width:24%\">{room.name if room else '—'}{fixed_mark}</td>"
                text += (
                    f"<tr{row_style}>{floor_cell}{number_cell}{name_cell}"
                    f"<td style='width:15%'>{room.room_type if room else '—'}</td>"
                    f"<td style='text-align:right; width:12%'>{room.area_m2:.1f}</td>"
                    f"<td style='text-align:center; width:12%'>{t.start_dt:%H:%M}</td>"
                    f"<td style='text-align:center; width:12%'>{t.end_dt:%H:%M}</td>"
                    f"<td style='text-align:right; width:12%'>{int(round((t.end_dt-t.start_dt).total_seconds()/60))}</td></tr>"
                )
            text += "</table>"
        style = """
        <style>
          body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #222; }
          table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 6px 0 14px 0; }
          th { background: #eeeeee; font-weight: 600; padding: 5px 6px; border: 1px solid #999; text-align: center; }
          td { padding: 4px 6px; border: 1px solid #aaa; vertical-align: middle; }
          h3 { margin: 14px 0 4px 0; }
          p { margin: 4px 0; }
        </style>
        """
        self.report_preview.setHtml(style + text)

    def _weather_name(self):
        return {1.0:'Ясно',1.2:'Дождь',1.5:'Снег',1.8:'Сильный дождь'}.get(float(getattr(self.project,'weather_factor',1.0)), 'Не указано')

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

    def _ensure_schedule_exportable(self):
        if not self.project:
            return False
        validation = validate_schedule(self.project)
        if validation.get("valid"):
            return True
        QMessageBox.warning(
            self,
            "Экспорт заблокирован",
            "Расписание не прошло production-проверку.\n\n"
            + validation.get("violations_summary", "Обнаружены нарушения.")
            + "\n\nСначала исправьте штат, зоны или параметры смены.",
        )
        return False

    def generate_docx(self):
        if not self.project or not self._ensure_schedule_exportable():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", "", "Word (*.docx)")
        if path:
            try:
                generate_report(self.project, path)
                QMessageBox.information(self, "Готово", f"Отчёт сохранён: {path}")
            except ReportGenerationError as e:
                QMessageBox.warning(self, "Экспорт заблокирован", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

    def export_csv(self):
        if not self.project or not self._ensure_schedule_exportable():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт CSV", "", "CSV (*.csv)")
        if path:
            try:
                export_tasks_csv(self.project, path)
                QMessageBox.information(self, "Готово", f"График сохранён в {path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

    def export_xlsx(self):
        if not self.project or not self._ensure_schedule_exportable():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт Excel", "", "Excel (*.xlsx)")
        if path:
            try:
                export_tasks_excel(self.project, path)
                QMessageBox.information(self, "Готово", f"График сохранён в {path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

    # ---------- Экран нормативов ----------
    def load_norms_screen(self):
        self.norms_screen.load_norms_screen()

    # ---------- Совместимость со старой навигацией ----------
    def go_to_zone_screen(self):
        self.go_to_planning_screen()
