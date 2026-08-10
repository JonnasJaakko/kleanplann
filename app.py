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
    QDoubleSpinBox, QDialog, QDialogButtonBox, QSlider, QComboBox, QCheckBox,
    QToolTip, QHeaderView, QAbstractItemView, QGraphicsRectItem, QFrame,
    QMenu, QSplitter
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QLineF, QTimer, QEvent
from PySide6.QtGui import (
    QPixmap, QPen, QColor, QBrush, QPolygonF, QPainter, QCursor,
    QFont, QAction
)
import numpy as np
from shapely.geometry import LineString, box

from project import Project, Wall, Room, Floor, Zone, CleaningTask, Shift
from room_builder import (build_rooms_from_walls, detect_rooms,
                          nearest_point_on_segment, split_walls_at_intersections,
                          extract_wall_centerlines, cleanup_segments, snap_wall_ends)
from zone_manager import (manual_distribution, PRIORITY_BALANCED,
                          PRIORITY_PROXIMITY, PRIORITY_AREA, PRIORITY_COUNT)
from cost_calculator import calculate_cost, estimate_required_employees
from report_generator import generate_report
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY, DEFAULT_TRAFFIC_PER_TYPE
from scheduler import plan_cleaning_schedule, schedule_single_shift, compute_recommended_employees
from calendar_export import export_tasks_csv, export_tasks_excel
from dxf_analyzer import analyze_dxf, load_wall_segments
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
        self._closing = False
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.start_screen = QWidget(); self.setup_start_screen(); self.stack.addWidget(self.start_screen)
        self.plan_screen = QWidget(); self.setup_plan_screen(); self.stack.addWidget(self.plan_screen)
        self.zone_screen = QWidget(); self.setup_zone_screen(); self.stack.addWidget(self.zone_screen)
        self.report_screen = QWidget(); self.setup_report_screen(); self.stack.addWidget(self.report_screen)
        self.norms_screen = QWidget(); self.setup_norms_screen(); self.stack.addWidget(self.norms_screen)
        self.stack.setCurrentIndex(0)

        # Переменные для временного хранения загруженного DXF до разметки этажей
        self.temp_dxf_path = None
        self.temp_dxf_segments = []  # список LineString (метры)
        self.dxf_info = None         # слои стен, масштаб, толщина стены
        self.floor_rects = []        # список (x,y,w,h)

    def closeEvent(self, event):
        """При закрытии окна спрашиваем, сохранить ли проект."""
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
  <li><b>Кисть</b> — копирует тип/проходимость комнаты: Shift+клик — взять
      свойства, обычный клик — применить к другой комнате.</li>
</ul>

<h3>Хот-кеи</h3>
<ul>
  <li><b>Ctrl+Z</b> — отменить, <b>Ctrl+Y</b> — повторить.</li>
  <li><b>Ctrl</b> при рисовании — привязка угла (кратно 90°).</li>
  <li><b>ПКМ</b> (правая кнопка) — панорамирование, перетаскивание вершин.</li>
  <li><b>Колесо мыши</b> — масштаб.</li>
</ul>

<h3>Экран зон</h3>
<p>Клик по табличке комнаты позволяет передать её другому сотруднику.
   Наведите курсор на табличку — увидите время уборки этой комнаты.</p>
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

    # ---------- Стартовый экран ----------
    def setup_start_screen(self):
        layout = QVBoxLayout(self.start_screen)
        layout.addWidget(QLabel("Добро пожаловать в KleanPlann"))
        layout.addWidget(QLabel("Недавние проекты:"))
        self.project_list = QListWidget(); layout.addWidget(self.project_list)
        self.project_list.itemClicked.connect(self.on_project_clicked)
        self.project_list.itemDoubleClicked.connect(self.open_project)
        self.project_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self._project_context_menu)
        btn_new = QPushButton("Новый проект"); btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Открыть проект"); btn_open.clicked.connect(self.open_project)
        btn_norms = QPushButton("Нормативы СанПиН"); btn_norms.clicked.connect(self.go_to_norms_screen)
        layout.addWidget(btn_new); layout.addWidget(btn_open)
        layout.addWidget(btn_norms)
        self.refresh_project_list()

    def refresh_project_list(self):
        self.project_list.clear()
        files = glob.glob(os.path.join(PROJECTS_DIR, "*.json"))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:10]:
            name = os.path.basename(f).replace('.json','')
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                last_modified = data.get('last_modified', '')
                if last_modified:
                    try:
                        dt = datetime.fromisoformat(last_modified)
                        date_str = dt.strftime("%d.%m.%Y %H:%M")
                    except:
                        date_str = ""
                else:
                    date_str = ""
                display = f"{name}\nизменён {date_str}" if date_str else name
            except:
                display = name
                date_str = ""
            item = QListWidgetItem()
            item.setData(Qt.UserRole, f)
            item.setData(Qt.UserRole + 1, name)

            # Виджет строки: название + кнопка-карандаш
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_widget.setMinimumHeight(40)
            row_layout.setContentsMargins(8, 0, 8, 8)
            row_layout.setSpacing(6)
            label = QLabel(display)
            label.setStyleSheet("font-size: 14px; font-weight: bold; color: black;")
            row_layout.addWidget(label)
            row_layout.addStretch()
            btn_edit = QPushButton("✏")
            btn_edit.setFixedSize(28, 28)
            btn_edit.setFlat(True)
            btn_edit.setToolTip("Переименовать")
            btn_edit.clicked.connect(lambda checked=False, p=f, n=name: self.rename_project_path(p, n))
            row_layout.addWidget(btn_edit)
            item.setSizeHint(row_widget.sizeHint())
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, row_widget)

    def on_project_clicked(self, item):
        pass  # просто выделение

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Название помещения:")
        if ok and name:
            self.project = Project(name); self.current_project_path = None
            self.load_plan_screen(); self.stack.setCurrentIndex(1)

    def rename_project(self):
        item = self.project_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")
            return
        path = item.data(Qt.UserRole)
        old_name = item.data(Qt.UserRole + 1)
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Новое название:", text=old_name)
        if ok and new_name and new_name != old_name:
            new_path = os.path.join(PROJECTS_DIR, f"{new_name}.json")
            try:
                os.rename(path, new_path)
                # Обновляем название внутри файла
                with open(new_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['name'] = new_name
                data['last_modified'] = datetime.now().isoformat()
                with open(new_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.refresh_project_list()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось переименовать: {e}")

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
                self.refresh_project_list()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось переименовать: {e}")

    def open_project(self):
        item = self.project_list.currentItem()
        if item:
            path = item.data(Qt.UserRole); self.project = Project.load_from_file(path)
            self.current_project_path = path
            self.load_plan_screen(); self.stack.setCurrentIndex(1)
        else: QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")

    def go_to_norms_screen(self):
        self.load_norms_screen(); self.stack.setCurrentIndex(4)

    def _project_context_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if not item:
            return
        menu = QMenu()
        open_action = menu.addAction("Открыть")
        open_action.triggered.connect(lambda: self._open_project_item(item))
        rename_action = menu.addAction("Переименовать")
        rename_action.triggered.connect(lambda: self._rename_project_item(item))
        delete_action = menu.addAction("Удалить")
        delete_action.setData("delete")
        menu.triggered.connect(lambda action: self._delete_project_item(item) if action.text() == "Удалить" else None)
        # Удалить — красным
        for a in menu.actions():
            if a.text() == "Удалить":
                a.setForeground(QColor(255, 0, 0))
        menu.exec(self.project_list.viewport().mapToGlobal(pos))

    def _open_project_item(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self.project = Project.load_from_file(path)
            self.current_project_path = path
            self.load_plan_screen()
            self.stack.setCurrentIndex(1)

    def _rename_project_item(self, item):
        path = item.data(Qt.UserRole)
        old_name = item.data(Qt.UserRole + 1)
        self.rename_project_path(path, old_name)

    def _delete_project_item(self, item):
        path = item.data(Qt.UserRole)
        name = item.data(Qt.UserRole + 1)
        ret = QMessageBox.question(self, "Удалить проект",
            f"Вы уверены, что хотите удалить проект «{name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            try:
                os.remove(path)
                self.refresh_project_list()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось удалить: {e}")

    def return_to_start_screen(self):
        """Возврат на стартовый экран с запросом сохранения."""
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

    # ---------- Экран плана ----------
    def setup_plan_screen(self):
        layout = QVBoxLayout(self.plan_screen)
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
        btn_help_editor.clicked.connect(self.show_help)
        header_row.addWidget(btn_help_editor)
        layout.addLayout(header_row)
        toolbar = QToolBar("Инструменты")
        self.plan_scene = QGraphicsScene()
        self.plan_view = PlanView(self.plan_scene, self)
        self.plan_view.setMinimumSize(300, 300)

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

        # План и сайдбар — QSplitter с возможностью менять ширину
        splitter_main = QSplitter(Qt.Horizontal)
        splitter_main.addWidget(self.plan_view)

        right_panel_widget = QWidget()
        right_panel = QVBoxLayout(right_panel_widget)
        form = QFormLayout()
        self.param_total_area = QLineEdit()
        self.param_total_area.setPlaceholderText("Например: 1500")
        self.param_employees = QSpinBox(); self.param_employees.setRange(1,100)
        self.param_rate = QDoubleSpinBox(); self.param_rate.setRange(0.01,10000)
        self.weather_combo = QComboBox()
        self.weather_combo.addItems(["Ясно (x1.0)", "Дождь (x1.2)", "Снег (x1.5)", "Сильный дождь (x1.8)"])
        form.addRow("Общая площадь (м²):", self.param_total_area)
        form.addRow("Кол-во сотрудников:", self.param_employees)
        form.addRow("Зарплата/час (руб):", self.param_rate)
        form.addRow("Погода:", self.weather_combo)
        right_panel.addLayout(form)
        right_panel.addWidget(QPushButton("Авторасчёт персонала", clicked=self.auto_calculate_staff))
        line1 = QFrame(); line1.setFrameShape(QFrame.HLine); line1.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line1)

        # Режим заливки
        fill_layout = QHBoxLayout()
        fill_layout.addWidget(QLabel("Заливка:"))
        self.fill_mode_combo = QComboBox()
        self.fill_mode_combo.addItem("Стандартный", "standard")
        self.fill_mode_combo.addItem("По типу", "type")
        self.fill_mode_combo.currentIndexChanged.connect(self.update_room_opacity)
        fill_layout.addWidget(self.fill_mode_combo)
        right_panel.addLayout(fill_layout)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(30)
        self.opacity_slider.valueChanged.connect(self.update_room_opacity)
        right_panel.addWidget(QLabel("Прозрачность заливки"))
        right_panel.addWidget(self.opacity_slider)
        line2 = QFrame(); line2.setFrameShape(QFrame.HLine); line2.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line2)

        # Заголовок таблицы
        table_header = QHBoxLayout()
        self.room_table_collapsed = False
        btn_collapse = QPushButton("▼")
        btn_collapse.setFlat(True)
        btn_collapse.setFixedWidth(28)
        btn_collapse.setToolTip("Свернуть/развернуть таблицу")
        btn_collapse.clicked.connect(self.toggle_room_table)
        self.btn_collapse_table = btn_collapse
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["⇅", "№", "Пл", "А-Я", "Тип"])
        self.sort_combo.setFixedWidth(52)
        self.sort_combo.setToolTip("Сортировка")
        self.sort_combo.currentIndexChanged.connect(self.sort_rooms)
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
        self.room_table.cellEntered.connect(self.on_room_table_hover)
        self.room_table.cellDoubleClicked.connect(self.on_room_table_double_clicked)
        self.room_table.itemSelectionChanged.connect(self.on_room_table_select)
        right_panel.addWidget(self.room_table)
        line3 = QFrame(); line3.setFrameShape(QFrame.HLine); line3.setFrameShadow(QFrame.Sunken)
        right_panel.addWidget(line3)

        right_panel.addWidget(QPushButton("Загрузить план", clicked=self.load_image))
        right_panel.addWidget(QPushButton("Распознать стены (CV)", clicked=self.detect_walls_cv))
        right_panel.addWidget(QPushButton("Удалить план", clicked=self.remove_image))
        right_panel.addStretch()
        splitter_main.addWidget(right_panel_widget)
        splitter_main.setStretchFactor(0, 1)
        splitter_main.setStretchFactor(1, 2)
        splitter_main.setSizes([400, 650])
        layout.addWidget(splitter_main)

        nav = QHBoxLayout()
        btn_back = QPushButton("← Назад"); btn_back.clicked.connect(self.return_to_start_screen)
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

    def remove_image(self):
        if not self.project or not self.project.image_paths: return
        self.project.image_paths = []
        self.refresh_plan_view()

    def load_image(self):
        if not self.project: return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить план", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.project.image_paths = [path]; self.refresh_plan_view()

    def load_dxf(self):
        if not self.project: return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить DXF", "", "DXF Files (*.dxf)")
        if not path: return
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
        self.plan_view.set_tool(6)
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

    def _get_dxf_scale(self, doc):
        insunits = doc.header.get("$INSUNITS", 0)
        if insunits == 4: return 0.001
        elif insunits == 5: return 0.01
        elif insunits == 6: return 1.0
        elif insunits == 1: return 0.0254
        return 0.001

    def _extract_lines(self, entity, scale):
        segs = []
        if entity.dxftype() == 'LINE':
            s, e = entity.dxf.start, entity.dxf.end
            segs.append(LineString([(s.x*scale, s.y*scale), (e.x*scale, e.y*scale)]))
        elif entity.dxftype() == 'LWPOLYLINE':
            pts = entity.get_points('xy')
            if len(pts) >= 2:
                scaled = [(x*scale, y*scale) for x, y in pts]
                for i in range(len(scaled)-1):
                    segs.append(LineString([scaled[i], scaled[i+1]]))
        elif entity.dxftype() == 'ARC':
            start = entity.dxf.start_angle
            end = entity.dxf.end_angle
            radius = entity.dxf.radius * scale
            center = (entity.dxf.center.x * scale, entity.dxf.center.y * scale)
            if end < start: end += 360
            step = (end - start) / 10.0
            pts = []
            for i in range(11):
                angle = math.radians(start + i * step)
                pts.append((center[0] + radius * math.cos(angle),
                            center[1] + radius * math.sin(angle)))
            for i in range(len(pts)-1):
                segs.append(LineString([pts[i], pts[i+1]]))
        return segs

    def show_temp_dxf_lines(self):
        scene = self.plan_view.scene()
        for item in scene.items():
            if isinstance(item, QGraphicsLineItem) and item.pen().color() == QColor(128,128,128):
                scene.removeItem(item)
        for seg in self.temp_dxf_segments:
            coords = list(seg.coords)
            if len(coords) >= 2:
                for i in range(len(coords)-1):
                    line = QGraphicsLineItem(QLineF(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]))
                    line.setPen(QPen(QColor(128,128,128), 1))
                    scene.addItem(line)
        rect = scene.itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
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
        for idx, (fx, fy, fw, fh) in enumerate(self.floor_rects):
            floor = Floor(index=len(self.project.floors), name=f"Этаж {len(self.project.floors)+1}")
            floor_box = box(fx, fy, fx+fw, fy+fh)
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
                    for i in range(len(coords)-1):
                        walls.append((coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]))
            if walls:
                all_x = [c for w in walls for c in (w[0], w[2])]
                all_y = [c for w in walls for c in (w[1], w[3])]
                shift_x = -(min(all_x) + max(all_x)) / 2
                shift_y = -(min(all_y) + max(all_y)) / 2
                walls = [(w[0]+shift_x, w[1]+shift_y, w[2]+shift_x, w[3]+shift_y) for w in walls]
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
        elif not (self.project and (self.project.rooms or self.project.walls)):
            self.plan_view.setSceneRect(0,0,800,600)
            text = scene.addText("Загрузите изображение плана"); text.setPos(400,300)
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
                QTimer.singleShot(0, lambda r=QRectF(rect): self.plan_view.fitInView(r, Qt.KeepAspectRatio))

    def draw_rooms(self):
        scene = self.plan_view.scene()
        for item in scene.items():
            if (isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None) or \
               (isinstance(item, QGraphicsTextItem) and item.data(1) == "room_label"):
                scene.removeItem(item)
        alpha = self.opacity_slider.value()
        fill_mode = self.fill_mode_combo.currentData() if hasattr(self, 'fill_mode_combo') else "standard"
        for room in self.project.rooms:
            if fill_mode == "type" and room.room_type:
                base = TYPE_COLORS.get(room.room_type, QColor(128, 128, 128))
                col = QColor(base.red(), base.green(), base.blue(), alpha)
                brush = QBrush(col)
            elif fill_mode == "type" and not room.room_type:
                brush = QBrush(Qt.black, Qt.DiagCrossPattern)
            else:
                col = QColor(*room.color[:3], alpha)
                brush = QBrush(col)
            pen = QPen(Qt.black, 1); pen.setCosmetic(True)
            poly = QPolygonF([QPointF(x,y) for x,y in room.points])
            item = scene.addPolygon(poly, pen, brush)
            item.setData(Qt.UserRole, room.id)
            item.setAcceptHoverEvents(False)
            item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            cx = sum(p[0] for p in room.points)/len(room.points)
            cy = sum(p[1] for p in room.points)/len(room.points)
            bg_color = TYPE_COLORS.get(room.room_type, QColor(Qt.red)) if room.room_type else QColor(Qt.red)
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
        if not self.project or not self.project.rooms: return
        for room in self.project.rooms:
            row = self.room_table.rowCount()
            self.room_table.insertRow(row)
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(str(int(round(room.area_m2)))))
            self.room_table.item(row, 0).setData(Qt.UserRole, room.id)

    def on_room_table_hover(self, row, col):
        item = self.room_table.item(row, 0)
        if item: self.plan_view.highlight_room(item.data(Qt.UserRole))

    def on_room_table_double_clicked(self, row, col):
        item = self.room_table.item(row, 0)
        if item: self.edit_room_properties(item.data(Qt.UserRole))

    def toggle_room_table(self):
        self.room_table_collapsed = not self.room_table_collapsed
        self.room_table.setVisible(not self.room_table_collapsed)
        self.btn_collapse_table.setText("▲" if self.room_table_collapsed else "▼")

    def on_room_table_select(self):
        rows = self.room_table.selectionModel().selectedRows()
        if not rows: return
        item = self.room_table.item(rows[0].row(), 0)
        if item: self.plan_view.set_selected_room(item.data(Qt.UserRole))

    def sort_rooms(self):
        idx = self.sort_combo.currentIndex()
        if not self.project or not self.project.rooms: return
        rooms = self.project.rooms[:]
        if idx == 1: rooms.sort(key=lambda r: r.id)
        elif idx == 2: rooms.sort(key=lambda r: r.area_m2, reverse=True)
        elif idx == 3: rooms.sort(key=lambda r: r.name.lower())
        elif idx == 4: rooms.sort(key=lambda r: r.room_type if r.room_type else "яя")
        else: return
        self.room_table.setRowCount(0)
        for room in rooms:
            row = self.room_table.rowCount()
            self.room_table.insertRow(row)
            self.room_table.setItem(row, 0, QTableWidgetItem(str(room.id+1)))
            self.room_table.setItem(row, 1, QTableWidgetItem(room.name))
            type_str = room.room_type if room.room_type else "—"
            self.room_table.setItem(row, 2, QTableWidgetItem(type_str))
            self.room_table.setItem(row, 3, QTableWidgetItem(str(int(round(room.area_m2)))))

    def update_room_opacity(self): self.draw_rooms()

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
            QMessageBox.warning(self, "Ошибка", "Загрузите изображение."); return
        try:
            from image_processor import load_image, detect_walls as detect_walls_cv_func
            img = load_image(self.project.image_paths[0])
            contours = detect_walls_cv_func(img)
            self.project.rooms = []; self.project.walls = []
            for i, pts in enumerate(contours):
                color = ROOM_COLORS[i % len(ROOM_COLORS)]
                self.project.rooms.append(Room(i, pts, color=color))
                for j in range(len(pts)):
                    x1,y1 = pts[j]; x2,y2 = pts[(j+1)%len(pts)]
                    self.project.walls.append(Wall(x1,y1,x2,y2))
            self._scale_rooms(); self.refresh_plan_view()
            QMessageBox.information(self, "Готово", f"Распознано {len(contours)} комнат(ы).")
        except Exception as e: QMessageBox.critical(self, "Ошибка CV", str(e))

    def build_rooms_from_project_walls(self):
        if not self.project.walls: self.project.rooms = []; return
        walls_list = [(w.x1, w.y1, w.x2, w.y2) for w in self.project.walls]
        polygons = build_rooms_from_walls(walls_list, mode="thin")
        if not polygons: self.project.rooms = []; return
        new_rooms = []
        for i, pts in enumerate(polygons):
            color = ROOM_COLORS[i % len(ROOM_COLORS)]
            new_rooms.append(Room(i, pts, color=color))
        old_rooms = {r.id: r for r in self.project.rooms}
        for new_room in new_rooms:
            center_new = (sum(x for x,y in new_room.points)/len(new_room.points),
                          sum(y for x,y in new_room.points)/len(new_room.points))
            for old_room in old_rooms.values():
                center_old = (sum(x for x,y in old_room.points)/len(old_room.points),
                              sum(y for x,y in old_room.points)/len(old_room.points))
                if math.hypot(center_new[0]-center_old[0], center_new[1]-center_old[1]) < 10:
                    new_room.area_m2 = old_room.area_m2; new_room.traffic = old_room.traffic
                    new_room.room_type = old_room.room_type; new_room.name = old_room.name; break
        self.project.rooms = new_rooms; self._scale_rooms()

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
                for room in self.project.rooms: room.area_m2 *= factor
        else:
            if self.project.rooms:
                total_px = sum(self._polygon_area(r.points) for r in self.project.rooms)
                if total_px > 0:
                    factor = total_area / total_px
                    for room in self.project.rooms: room.area_m2 = self._polygon_area(room.points) * factor
                else:
                    area_per = total_area / len(self.project.rooms)
                    for room in self.project.rooms: room.area_m2 = area_per
        current_floor = self.project.current_floor
        current_floor.total_area_m2 = sum(r.area_m2 for r in current_floor.rooms)
        self.param_total_area.setText(str(current_floor.total_area_m2))

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
        if changed: self.refresh_plan_view(); QMessageBox.information(self, "Калибровка", "Стены выровнены.")

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
        type_combo.currentTextChanged.connect(
            lambda t: traffic_spin.setValue(DEFAULT_TRAFFIC_PER_TYPE.get(t, 0)) if t else None)
        layout.addRow("Название:", name_edit); layout.addRow("Номер:", num_spin)
        layout.addRow("Площадь (м²):", area_spin); layout.addRow("Проходимость (чел/ч):", traffic_spin)
        layout.addRow("Тип:", type_combo)
        # Чекбоксы для приоритетной уборки и исключения
        prio_check = QCheckBox("★ Приоритетная уборка")
        prio_check.setChecked(room.priority)
        layout.addRow(prio_check)
        disabled_check = QCheckBox("🚫 Не назначать уборку")
        disabled_check.setChecked(room.disabled)
        layout.addRow(disabled_check)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); layout.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            room.priority = prio_check.isChecked()
            room.disabled = disabled_check.isChecked()
            new_num = num_spin.value()
            if any(r.id == new_num-1 and r != room for r in self.project.rooms):
                QMessageBox.warning(self, "Ошибка", "Номер уже используется."); return
            room.id = new_num - 1; room.name = name_edit.text() or f"Комната {room.id+1}"
            room.area_m2 = area_spin.value(); room.traffic = traffic_spin.value()
            room.room_type = type_combo.currentText(); self.draw_rooms(); self.update_room_table()

    def auto_calculate_staff(self):
        if not self.project or not self.project.all_rooms():
            QMessageBox.warning(self, "Авторасчёт персонала", "Сначала загрузите план с распознанными помещениями.")
            return
        weather_text = self.weather_combo.currentText()
        if "1.2" in weather_text: self.project.weather_factor = 1.2
        elif "1.5" in weather_text: self.project.weather_factor = 1.5
        elif "1.8" in weather_text: self.project.weather_factor = 1.8
        else: self.project.weather_factor = 1.0
        result = estimate_required_employees(self.project)
        self.param_employees.setValue(result["employees"]); self.project.employees_count = result["employees"]
        QMessageBox.information(self, "Авторасчёт персонала",
            f"Рекомендуемое количество сотрудников: {result['employees']}\n"
            f"Расчётная ежедневная нагрузка: {result['daily_minutes']:.0f} мин.\n"
            f"Полезная смена одного сотрудника: {result['capacity_minutes']:.0f} мин.")

    def go_to_zone_screen(self):
        if not self.project: return
        total_text = (self.param_total_area.text() or "").strip()
        self.project.total_area_m2 = float(total_text) if total_text else 0.0
        self.project.employees_count = self.param_employees.value()
        self.project.hourly_rate = self.param_rate.value()
        weather_text = self.weather_combo.currentText()
        if "1.2" in weather_text: self.project.weather_factor = 1.2
        elif "1.5" in weather_text: self.project.weather_factor = 1.5
        elif "1.8" in weather_text: self.project.weather_factor = 1.8
        else: self.project.weather_factor = 1.0
        self._scale_rooms()
        all_rooms = self.project.all_rooms()
        if not all_rooms: QMessageBox.warning(self, "Ошибка", "Нет комнат ни на одном этаже."); return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Не задана площадь комнат."); return
        while len(self.project.employee_names) < self.project.employees_count:
            self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names)+1}")
        self.project.employee_names = self.project.employee_names[:self.project.employees_count]
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(all_rooms, percents)
        self.load_zone_screen(); self.stack.setCurrentIndex(2)

    # ---------- Экран зон ----------
    def setup_zone_screen(self):
        layout = QVBoxLayout(self.zone_screen)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        header = QLabel("Распределение зон ответственности")
        header.setFixedHeight(30); header.setAlignment(Qt.AlignCenter); layout.addWidget(header)
        splitter_zone = QSplitter(Qt.Horizontal)
        self.zone_scene = QGraphicsScene()
        self.zone_view = QGraphicsView(self.zone_scene)
        self.zone_view.setMinimumSize(300, 300)
        self.zone_view.wheelEvent = lambda ev: self.zone_view.scale(1.15 if ev.angleDelta().y()>0 else 1/1.15, 1.15 if ev.angleDelta().y()>0 else 1/1.15)
        splitter_zone.addWidget(self.zone_view)
        ctrl_widget = QWidget()
        ctrl = QVBoxLayout(ctrl_widget)
        ctrl.addWidget(QLabel("Приоритет распределения:"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItem("Сбалансированно", PRIORITY_BALANCED)
        self.priority_combo.addItem("Близость комнат", PRIORITY_PROXIMITY)
        self.priority_combo.addItem("Площадь", PRIORITY_AREA)
        self.priority_combo.addItem("Количество комнат", PRIORITY_COUNT)
        self.priority_combo.currentIndexChanged.connect(self.recalculate_zones)
        ctrl.addWidget(self.priority_combo)
        ctrl.addWidget(QLabel("Сотрудники:"))
        self.employee_list_widget = QListWidget()
        self.employee_list_widget.setDragDropMode(QListWidget.InternalMove)
        self.employee_list_widget.setDefaultDropAction(Qt.MoveAction)
        ctrl.addWidget(self.employee_list_widget)
        ctrl.addWidget(QPushButton("Добавить сотрудника", clicked=self.add_employee))
        ctrl.addWidget(QPushButton("Пересчитать зоны", clicked=self.recalculate_zones))
        ctrl.addWidget(QLabel("Смена:"))
        shift_layout = QHBoxLayout()
        self.shift_start_edit = QLineEdit("08:00"); self.shift_start_edit.setFixedWidth(50)
        self.shift_end_edit = QLineEdit("22:00"); self.shift_end_edit.setFixedWidth(50)
        shift_layout.addWidget(QLabel("с")); shift_layout.addWidget(self.shift_start_edit)
        shift_layout.addWidget(QLabel("до")); shift_layout.addWidget(self.shift_end_edit)
        ctrl.addLayout(shift_layout)
        ctrl.addWidget(QLabel("Обед (HH:MM-HH:MM):"))
        self.lunch_start_edit = QLineEdit("12:00"); self.lunch_start_edit.setFixedWidth(50)
        self.lunch_end_edit = QLineEdit("13:00"); self.lunch_end_edit.setFixedWidth(50)
        lunch_layout = QHBoxLayout()
        lunch_layout.addWidget(QLabel("с")); lunch_layout.addWidget(self.lunch_start_edit)
        lunch_layout.addWidget(QLabel("до")); lunch_layout.addWidget(self.lunch_end_edit)
        ctrl.addLayout(lunch_layout); ctrl.addStretch()
        splitter_zone.addWidget(ctrl_widget)
        splitter_zone.setStretchFactor(0, 2); splitter_zone.setStretchFactor(1, 1); splitter_zone.setSizes([650, 400])
        layout.addWidget(splitter_zone)
        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.stack.setCurrentIndex(1)))
        nav.addStretch(); nav.addWidget(QPushButton("Далее →", clicked=self.go_to_planning_screen))
        layout.addLayout(nav)

    def load_zone_screen(self):
        scene = self.zone_view.scene(); scene.clear()
        if self.project.image_paths:
            pix = QPixmap(self.project.image_paths[0]); scene.addPixmap(pix); self.zone_view.setSceneRect(QRectF(pix.rect()))
        if self.project.shifts:
            self.shift_start_edit.setText(self.project.shifts[0].start_time)
            self.shift_end_edit.setText(self.project.shifts[-1].end_time)
        if self.project.breaks:
            self.lunch_start_edit.setText(self.project.breaks[0][0]); self.lunch_end_edit.setText(self.project.breaks[0][1])
        self.employee_list_widget.clear()
        for i in range(self.project.employees_count):
            name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i+1}"
            item = QListWidgetItem(); widget = QWidget()
            vbox = QVBoxLayout(widget); vbox.setContentsMargins(4,2,4,2)
            name_btn = QPushButton(name); name_btn.setFlat(True)
            name_btn.clicked.connect(lambda checked=False, idx=i: self.rename_employee(idx))
            h_name = QHBoxLayout(); h_name.addWidget(name_btn)
            btn_del = QPushButton("✕"); btn_del.setFixedSize(24,24)
            btn_del.clicked.connect(lambda checked=False, it=item: self.remove_employee(it))
            h_name.addWidget(btn_del); vbox.addLayout(h_name)
            info_label = QLabel(""); info_label.setWordWrap(True); vbox.addWidget(info_label)
            item.setSizeHint(widget.sizeHint()); item.setData(Qt.UserRole, i)
            item.info_label = info_label; item.name_btn = name_btn; item.widget_ref = widget
            self.employee_list_widget.addItem(item); self.employee_list_widget.setItemWidget(item, widget)
        self.recalculate_zones()

    def rename_employee(self, index):
        current_name = self.project.employee_names[index] if index < len(self.project.employee_names) else ""
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Имя сотрудника:", text=current_name)
        if ok and new_name:
            self.project.employee_names[index] = new_name
            item = self.employee_list_widget.item(index)
            if item and hasattr(item, 'name_btn'): item.name_btn.setText(new_name)

    def add_employee(self):
        self.project.employees_count += 1
        self.project.employee_names.append(f"Сотрудник {self.project.employees_count}")
        self.load_zone_screen()

    def remove_employee(self, item):
        row = self.employee_list_widget.row(item)
        if row >= 0:
            self.employee_list_widget.takeItem(row); del self.project.employee_names[row]
            self.project.employees_count -= 1; self.recalculate_zones()

    def _apply_shift_and_lunch(self):
        shift_start = self.shift_start_edit.text().strip() or "08:00"
        shift_end = self.shift_end_edit.text().strip() or "22:00"
        lunch_start = self.lunch_start_edit.text().strip() or "12:00"
        lunch_end = self.lunch_end_edit.text().strip() or "13:00"
        self.project.shifts = [Shift("Основная", shift_start, shift_end)]
        self.project.breaks = [(lunch_start, lunch_end)]

    def recalculate_zones(self):
        self._apply_shift_and_lunch()
        all_rooms = self.project.all_rooms()
        if not all_rooms: return
        if any(r.area_m2 <= 0 for r in all_rooms):
            QMessageBox.warning(self, "Ошибка", "Сначала задайте площадь."); return
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        priority = self.priority_combo.currentData() if hasattr(self, 'priority_combo') else PRIORITY_BALANCED
        self.project.zones = manual_distribution(all_rooms, percents, priority=priority)
        # Расписание генерируется ТОЛЬКО при переходе в отчёт (см. go_to_planning_screen),
        # иначе plan_cleaning_schedule автоматически увеличит число сотрудников.
        self.project.cleaning_tasks = []
        self.refresh_zone_display(); self.update_employee_labels()

    def refresh_zone_display(self):
        scene = self.zone_view.scene()
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None: scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label": scene.removeItem(item)
        for zone in self.project.zones:
            col = QColor(*zone.color); brush = QBrush(col); pen = QPen(Qt.black, 1)
            for rid in zone.room_ids:
                room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                if not room: continue
                poly = QPolygonF([QPointF(x,y) for x,y in room.points])
                item = scene.addPolygon(poly, pen, brush); item.setData(Qt.UserRole, room.id)
                cx = sum(p[0] for p in room.points)/len(room.points)
                cy = sum(p[1] for p in room.points)/len(room.points)
                label_html = (
                    f"<div style='text-align:center; background-color:{col.name()}; color:white; "
                    f"padding:3px; border:1px solid black; font-size:18px; font-weight:bold;'>{zone.employee_index+1}</div>"
                    f"<div style='text-align:center; background-color:{col.name()}; color:white; "
                    f"padding:1px 3px; border:1px solid black; font-size:10px;'>{room.name}<br>№{room.id+1} ({room.area_m2:.1f} м²)</div>"
                )
                text = QGraphicsTextItem(); text.setHtml(label_html); text.setPos(cx-40, cy-28)
                text.setData(1, "zone_label"); text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                text.setAcceptHoverEvents(True)
                text.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(ev.screenPos(), self._get_schedule_tip(rid, emp))
                text.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                text.mousePressEvent = lambda ev, rid=rid: self.change_room_employee(rid)
                scene.addItem(text)
                hit = QGraphicsRectItem(cx-45, cy-32, 90, 52)
                hit.setPen(QPen(Qt.NoPen)); hit.setBrush(QBrush(QColor(0,0,0,1)))
                hit.setData(1, "zone_hitbox"); hit.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                hit.setAcceptHoverEvents(True); hit.setZValue(3)
                hit.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(ev.screenPos(), self._get_schedule_tip(rid, emp))
                hit.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                hit.mousePressEvent = lambda ev, rid=rid: self.change_room_employee(rid)
                scene.addItem(hit)

    def _get_schedule_tip(self, room_id, emp_idx):
        if not hasattr(self.project, 'cleaning_tasks') or not self.project.cleaning_tasks: return "Расписание не сгенерировано"
        tasks = [t for t in self.project.cleaning_tasks if t.room_id == room_id and t.employee == emp_idx]
        if not tasks: return "Нет назначенных уборок"
        return "\n".join(f"{t.start_dt.strftime('%H:%M')} - {t.end_dt.strftime('%H:%M')}" for t in sorted(tasks, key=lambda x: x.start_dt)[:10])

    def change_room_employee(self, room_id):
        emp_list = [self.project.employee_names[i] for i in range(self.project.employees_count)]
        current = next((z.employee_index for z in self.project.zones if room_id in z.room_ids), 0)
        item, ok = QInputDialog.getItem(self, "Сменить сотрудника", "Выберите:", emp_list, current, False)
        if not ok: return
        new_emp = emp_list.index(item)
        for z in self.project.zones:
            if room_id in z.room_ids: z.room_ids.remove(room_id)
        for z in self.project.zones:
            if z.employee_index == new_emp: z.room_ids.append(room_id); break
        self.refresh_zone_display(); self.update_employee_labels()

    def update_employee_labels(self):
        for i in range(self.employee_list_widget.count()):
            item = self.employee_list_widget.item(i)
            if not hasattr(item, 'info_label'): continue
            zones = [z for z in self.project.zones if z.employee_index == i]
            total_area = 0.0; room_details = []
            for z in zones:
                for rid in z.room_ids:
                    room = next((r for r in self.project.all_rooms() if r.id == rid), None)
                    if room:
                        total_area += room.area_m2
                        type_str = f" ({room.room_type})" if room.room_type else ""
                        room_details.append(f"- {room.name}{type_str} {room.area_m2:.1f} м²")
            name = self.project.employee_names[i] if i < len(self.project.employee_names) else f"Сотрудник {i+1}"
            text = f"{name} ({total_area:.1f} м²)\n" + "\n".join(room_details)
            if total_area > 100: text = f"<font color='red'>{text}</font>"
            item.info_label.setText(text)
            if hasattr(item, 'widget_ref'):
                item.widget_ref.adjustSize(); item.setSizeHint(item.widget_ref.sizeHint())

    def go_to_planning_screen(self, skip_check=False):
        """Переход к генерации расписания.
        Если skip_check=True — пропускаем popup о нехватке (вызов из _finish_exclude_and_continue)."""
        all_rooms = self.project.all_rooms()
        if not all_rooms or not self.project.zones:
            QMessageBox.warning(self, "Ошибка", "Сначала распределите зоны."); return
        self._apply_shift_and_lunch()
        # Проверка: достаточно ли сотрудников?
        if not skip_check:
            cold_load = compute_recommended_employees(self.project)
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
                    self.project.employees_count = recommended
                    from zone_manager import manual_distribution
                    all_rooms = self.project.all_rooms()
                    percents = [100.0 / recommended] * recommended
                    while len(self.project.employee_names) < recommended:
                        self.project.employee_names.append(f"Сотрудник {len(self.project.employee_names)+1}")
                    self.project.employee_names = self.project.employee_names[:recommended]
                    self.project.zones = manual_distribution(all_rooms, percents)
                    self.load_zone_screen()
                    self.stack.setCurrentIndex(2)
                    return
                elif clicked == btn_select:
                    self._manual_exclude_mode()
                    return  # ждём завершения через _finish_exclude_and_continue
                elif clicked == dlg.button(QMessageBox.Cancel):
                    return
                # keep — продолжаем как есть
        # SINGLE_SHIFT режим: одна смена одного дня
        result = schedule_single_shift(self.project, employees=self.project.employees_count)
        self.project.cleaning_tasks = result["tasks"]
        self.load_report_screen(); self.stack.setCurrentIndex(3)

    def _finish_exclude_and_continue(self):
        """Завершение ручного исключения → пересчёт зон → генерация расписания."""
        self._finish_manual_exclude()
        active = [r for r in self.project.all_rooms() if not r.disabled]
        if not active:
            self.project.cleaning_tasks = []
            self.load_report_screen()
            self.stack.setCurrentIndex(3)
            return
        from zone_manager import manual_distribution
        percents = [100.0 / self.project.employees_count] * self.project.employees_count
        self.project.zones = manual_distribution(self.project.all_rooms(), percents)
        self.refresh_zone_display()
        self.update_employee_labels()
        self.go_to_planning_screen(skip_check=True)

    def _manual_exclude_mode(self):
        """Режим: клик по полигонам комнат на плане, чтобы исключить/включить их."""
        if self.stack.currentIndex() != 2:
            self.stack.setCurrentIndex(2)
        self._exclude_mode_active = True
        scene = self.zone_view.scene()

        # Удаляем старые элементы режима (label, кнопку, hitbox'ы зон)
        for item in scene.items():
            data2 = item.data(2)
            if data2 in ("exclude_label", "exclude_finish_btn"):
                scene.removeItem(item)
            if isinstance(item, QGraphicsRectItem) and item.data(1) == "zone_hitbox":
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)

        # Перерисовываем полигоны зон
        self.refresh_zone_display()

        # Удаляем hitbox'ы и label'и, которые создала refresh_zone_display
        for item in scene.items():
            if isinstance(item, QGraphicsRectItem) and item.data(1) == "zone_hitbox":
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)

        # Делаем полигоны кликабельными
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                room_id = item.data(Qt.UserRole)
                room = next((r for r in self.project.all_rooms() if r.id == room_id), None)
                disabled = room.disabled if room else False
                if disabled:
                    item.setBrush(QBrush(QColor(255, 0, 0, 120)))
                else:
                    item.setBrush(QBrush(QColor(0, 255, 0, 70)))
                item.setAcceptHoverEvents(True)
                item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                # Сохраняем room_id в item для обработчика
                item._exclude_room_id = room_id
                # Устанавливаем обработчик через setAcceptHoverEvents + mousePressEvent
                def make_handler(rid):
                    def handler(ev):
                        r = next((rr for rr in self.project.all_rooms() if rr.id == rid), None)
                        if r:
                            r.disabled = not r.disabled
                        self._manual_exclude_mode()
                        ev.accept()
                    return handler
                item.mousePressEvent = make_handler(room_id)

        # Инструкция (текст в сцене)
        label = QGraphicsTextItem()
        label.setHtml("<div style='background:white; padding:6px; border:2px solid black; font-size:13px;'>"
                      "👆 Кликните по комнатам: зелёные = убираем, красные = пропускаем.<br>"
                      "Нажмите <b>✅ Готово</b> для продолжения.</div>")
        label.setPos(10, 10)
        label.setZValue(100)
        label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        label.setData(2, "exclude_label")
        scene.addItem(label)

        # Кнопка "Готово" (proxy widget в сцене)
        btn_finish = QPushButton("✅ Готово")
        btn_finish.setFixedSize(130, 40)
        btn_finish.clicked.connect(self._finish_exclude_and_continue)
        from PySide6.QtWidgets import QGraphicsProxyWidget
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(btn_finish)
        proxy.setPos(scene.width() - 160, 10)
        proxy.setZValue(101)
        proxy.setData(2, "exclude_finish_btn")
        proxy.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        scene.addItem(proxy)

    def _finish_manual_exclude(self):
        """Завершение режима ручного исключения."""
        self._exclude_mode_active = False
        scene = self.zone_view.scene()
        # Убираем label и кнопку из сцены
        for item in scene.items():
            data2 = item.data(2)
            if data2 in ("exclude_label", "exclude_finish_btn"):
                scene.removeItem(item)
        # Восстанавливаем нормальное отображение зон
        self.refresh_zone_display()

    # ---------- Экран отчёта ----------
    def setup_report_screen(self):
        layout = QVBoxLayout(self.report_screen)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        header = QLabel("Отчёт и анализ затрат")
        header.setFixedHeight(30); header.setAlignment(Qt.AlignCenter); layout.addWidget(header)
        self.report_preview = QTextEdit(readOnly=True); layout.addWidget(self.report_preview)
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
        for task in self.project.cleaning_tasks: tasks_by_emp.setdefault(task.employee, []).append(task)
        for emp_idx, tasks in tasks_by_emp.items():
            name = self.project.employee_names[emp_idx] if emp_idx < len(self.project.employee_names) else f"Сотрудник {emp_idx+1}"
            text += f"<h4>{name}</h4>"
            text += "<table border='1' cellspacing='0' cellpadding='4'><tr><th>№</th><th>Комната</th><th>Площадь (м²)</th><th>Начало</th><th>Конец</th><th>Длит.</th></tr>"
            for t in tasks[:50]:
                room = self._find_room_by_id(t.room_id)
                room_name = room.name if room else str(t.room_id)
                if room and room.room_type: room_name += f" ({room.room_type})"
                area = f"{room.area_m2:.0f}" if room else "—"
                dur = (t.end_dt - t.start_dt).seconds // 60
                text += f"<tr><td>{t.room_id+1}</td><td>{room_name}</td><td>{area}</td><td>{t.start_dt.strftime('%H:%M')}</td><td>{t.end_dt.strftime('%H:%M')}</td><td>{dur} мин</td></tr>"
            text += "</table>"
        self.report_preview.setHtml(text)

    def _find_room_by_id(self, room_id):
        for floor in self.project.floors:
            for room in floor.rooms:
                if room.id == room_id: return room
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
            try: generate_report(self.project, path); QMessageBox.information(self, "Готово", f"Отчёт сохранён: {path}")
            except Exception as e: QMessageBox.critical(self, "Ошибка", str(e))

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт CSV", "", "CSV (*.csv)")
        if path: export_tasks_csv(self.project, path); QMessageBox.information(self, "Готово", f"График сохранён в {path}")

    def export_xlsx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт Excel", "", "Excel (*.xlsx)")
        if path: export_tasks_excel(self.project, path); QMessageBox.information(self, "Готово", f"График сохранён в {path}")

    # ---------- Экран нормативов ----------
    def setup_norms_screen(self):
        layout = QVBoxLayout(self.norms_screen)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        header = QLabel("Нормативы СанПиН по типам помещений")
        header.setFixedHeight(30); header.setAlignment(Qt.AlignCenter); layout.addWidget(header)
        self.norms_table = QTableWidget(0, 3)
        self.norms_table.setHorizontalHeaderLabels(["Тип", "Коэффициент сложности", "Частота (раз/день)"])
        layout.addWidget(self.norms_table)
        btn_layout_norms = QHBoxLayout()
        btn_add_type = QPushButton("+ Добавить тип"); btn_add_type.clicked.connect(self.add_norm_type)
        btn_remove_type = QPushButton("✕ Удалить тип"); btn_remove_type.clicked.connect(self.remove_norm_type)
        btn_layout_norms.addWidget(btn_add_type); btn_layout_norms.addWidget(btn_remove_type)
        layout.addLayout(btn_layout_norms)
        btn_save = QPushButton("Сохранить"); btn_save.clicked.connect(self.save_norms); layout.addWidget(btn_save)
        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.stack.setCurrentIndex(0)))
        layout.addLayout(nav)

    def load_norms_screen(self):
        self.norms_table.setRowCount(0)
        for room_type, coeff in COMPLEXITY_FACTOR.items():
            row = self.norms_table.rowCount(); self.norms_table.insertRow(row)
            self.norms_table.setItem(row, 0, QTableWidgetItem(room_type))
            self.norms_table.setItem(row, 1, QTableWidgetItem(str(coeff)))
            self.norms_table.setItem(row, 2, QTableWidgetItem(str(DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1))))

    def add_norm_type(self):
        name, ok = QInputDialog.getText(self, "Добавить тип", "Название типа помещения:")
        if ok and name and name not in COMPLEXITY_FACTOR:
            COMPLEXITY_FACTOR[name] = 1.0; DEFAULT_FREQUENCY_PER_DAY[name] = 1; self.load_norms_screen()

    def remove_norm_type(self):
        row = self.norms_table.currentRow()
        if row < 0: QMessageBox.warning(self, "Ошибка", "Выберите тип для удаления."); return
        room_type = self.norms_table.item(row, 0).text()
        if room_type in COMPLEXITY_FACTOR: del COMPLEXITY_FACTOR[room_type]
        if room_type in DEFAULT_FREQUENCY_PER_DAY: del DEFAULT_FREQUENCY_PER_DAY[room_type]
        self.load_norms_screen()

    def save_norms(self):
        for row in range(self.norms_table.rowCount()):
            room_type = self.norms_table.item(row, 0).text()
            try:
                coeff = float(self.norms_table.item(row, 1).text())
                freq = int(self.norms_table.item(row, 2).text())
                COMPLEXITY_FACTOR[room_type] = coeff; DEFAULT_FREQUENCY_PER_DAY[room_type] = freq
            except: pass
        QMessageBox.information(self, "Успех", "Нормативы обновлены.")

