"""Редактор плана и настройки проекта."""
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QToolBar,QLineEdit,QSpinBox,QComboBox,QSlider,QTableWidget,QTableWidgetItem,QHeaderView,QFrame,QSplitter,QDialog,QFormLayout,QDoubleSpinBox,QDialogButtonBox,QTimeEdit,QMessageBox,QGraphicsScene)
from PySide6.QtCore import Qt,QTime
from sanitarnorm import DEFAULT_TRAFFIC_PER_TYPE
from tools import PlanView

ROOM_COLORS=[(255,0,0,30),(60,180,75,30),(255,225,25,30),(0,130,200,30),(245,130,48,30),(145,30,180,30),(70,240,240,30),(240,50,230,30),(210,245,60,30),(250,190,190,30),(0,128,128,30),(230,190,255,30)]

class ProjectSettingsDialog(QDialog):
    def __init__(self,main_window):
        super().__init__(main_window); self.main=main_window; p=main_window.project
        self.setWindowTitle('Настройки проекта'); self.setMinimumWidth(480)
        lay=QVBoxLayout(self); form=QFormLayout()
        self.total_area=QDoubleSpinBox(); self.total_area.setRange(0,1e7); self.total_area.setDecimals(1); self.total_area.setSuffix(' м²'); self.total_area.setValue(float(getattr(p,'total_area_m2',0)))
        self.salary_type=QComboBox(); self.salary_type.addItem('Фиксированная плата за смену','fixed_shift'); self.salary_type.addItem('Плата за квадратный метр','per_sqm'); self.salary_type.addItem('Плата за час работы','hour'); self.salary_type.setCurrentIndex(max(0,self.salary_type.findData(getattr(p,'salary_type','hour'))))
        self.salary_value=QDoubleSpinBox(); self.salary_value.setRange(0,1e7); self.salary_value.setDecimals(2); self.salary_value.setValue(float(getattr(p,'salary_value',getattr(p,'hourly_rate',200))))
        self.salary_hint=QLabel(); self.salary_hint.setWordWrap(True); self.salary_type.currentIndexChanged.connect(self._update_salary_hint)
        self.overtime_type=QComboBox(); self.overtime_type.addItem('Процент от оклада','percent'); self.overtime_type.addItem('Рубли за час дополнительной работы','per_hour'); self.overtime_type.setCurrentIndex(max(0,self.overtime_type.findData(getattr(p,'overtime_type','percent'))))
        self.overtime_value=QDoubleSpinBox(); self.overtime_value.setRange(0,1e7); self.overtime_value.setDecimals(2); self.overtime_value.setValue(float(getattr(p,'overtime_value',50)))
        self.cleaning_type=QComboBox(); self.cleaning_type.addItems(['поддерживающая','генеральная']); self.cleaning_type.setCurrentText(getattr(p,'cleaning_type','поддерживающая'))
        self.weather=QComboBox(); self.weather.addItems(['Ясно (x1.0)','Дождь (x1.2)','Снег (x1.5)','Сильный дождь (x1.8)']); self.weather.setCurrentIndex({1.0:0,1.2:1,1.5:2,1.8:3}.get(float(getattr(p,'weather_factor',1.0)),0))
        def te(value):
            w=QTimeEdit(QTime.fromString(value,'HH:mm')); w.setDisplayFormat('HH:mm'); return w
        self.shift_start=te(p.shifts[0].start_time if p.shifts else '08:00'); self.shift_end=te(p.shifts[0].end_time if p.shifts else '17:00'); self.lunch_start=te(p.breaks[0][0] if p.breaks else '12:00'); self.lunch_end=te(p.breaks[0][1] if p.breaks else '13:00')
        form.addRow('Суммарная площадь здания:',self.total_area)
        form.addRow('Вид оплаты сотрудникам:',self.salary_type); form.addRow('Значение оплаты:',self.salary_value); form.addRow('',self.salary_hint)
        form.addRow('Вид надбавки за переработку:',self.overtime_type); form.addRow('Значение надбавки:',self.overtime_value)
        form.addRow('Тип уборки:',self.cleaning_type); form.addRow('Погода:',self.weather)
        form.addRow('Смена с:',self.shift_start); form.addRow('Смена до:',self.shift_end); form.addRow('Обед с:',self.lunch_start); form.addRow('Обед до:',self.lunch_end)
        lay.addLayout(form); self._update_salary_hint()
        hint=QLabel('При генеральной уборке коэффициенты трудоёмкости автоматически умножаются на 2.'); hint.setWordWrap(True); lay.addWidget(hint)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); lay.addWidget(buttons)
        delete=QPushButton('Удалить план'); delete.setStyleSheet('color:#b00020;'); delete.clicked.connect(self._delete_plan); lay.addWidget(delete)
    def _update_salary_hint(self):
        data=self.salary_type.currentData(); self.salary_value.setSuffix(' руб/смену' if data=='fixed_shift' else (' руб/м²' if data=='per_sqm' else ' руб/час')); self.salary_hint.setText({'fixed_shift':'Каждый сотрудник получает указанную сумму за обычную смену.','per_sqm':'Оплата рассчитывается по суммарной площади комнат сотрудника.','hour':'Оплата рассчитывается по фактически отработанным часам.'}[data])
    def _delete_plan(self):
        if QMessageBox.question(self,'Удалить план','Удалить план, этажи, комнаты, зоны и расписание?')==QMessageBox.Yes: self.main.delete_plan_data(); self.accept()
    def values(self):
        wt=self.weather.currentText(); factor=1.0 if '1.0' in wt else 1.2 if '1.2' in wt else 1.5 if '1.5' in wt else 1.8
        return {'total_area':self.total_area.value(),'salary_type':self.salary_type.currentData(),'salary_value':self.salary_value.value(),'overtime_type':self.overtime_type.currentData(),'overtime_value':self.overtime_value.value(),'cleaning_type':self.cleaning_type.currentText(),'weather_factor':factor,'shift_start':self.shift_start.time().toString('HH:mm'),'shift_end':self.shift_end.time().toString('HH:mm'),'lunch_start':self.lunch_start.time().toString('HH:mm'),'lunch_end':self.lunch_end.time().toString('HH:mm')}

class PlanScreen(QWidget):
    def __init__(self,main_window): super().__init__(); self.main=main_window; self.setup_ui()
    def setup_ui(self):
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        toolbar=QToolBar('Инструменты')
        toolbar.addAction('← Назад',self.main.return_to_start_screen); toolbar.addAction('💾 Сохранить',self.main.save_project); toolbar.addAction('⚙ Настройки проекта',self.main.open_project_settings); toolbar.addAction('❓ Справка',self.main.show_help); toolbar.addSeparator()
        for title,tool in [('Выбор',0),('Ластик',1),('Линия',2),('Комната',4),('Калибровка',3),('Кисть',5)]: toolbar.addAction(title,lambda checked=False,t=tool:self.plan_view.set_tool(t))
        toolbar.addSeparator(); toolbar.addAction('📐 Загрузить план',self.main.load_plan_universal)
        self.btn_finish_floors=QPushButton('✅ Завершить разметку'); self.btn_finish_floors.setVisible(False); self.btn_finish_floors.clicked.connect(self.main.finish_floor_selection); toolbar.addWidget(self.btn_finish_floors)
        layout.addWidget(toolbar)
        splitter=QSplitter(Qt.Horizontal); self.plan_scene=QGraphicsScene(); self.plan_view=PlanView(self.plan_scene,self.main); splitter.addWidget(self.plan_view)
        right=QWidget(); rp=QVBoxLayout(right)
        floor_row=QHBoxLayout(); floor_row.addWidget(QLabel('Этаж:')); self.floor_combo=QComboBox(); self.floor_combo.currentIndexChanged.connect(self.main.switch_floor); floor_row.addWidget(self.floor_combo,1); add=QPushButton('+ Создать этаж'); add.clicked.connect(self.main.add_floor); floor_row.addWidget(add); delete=QPushButton('Удалить этаж'); delete.setToolTip('Удалить выбранный этаж'); delete.clicked.connect(self.main.delete_floor); floor_row.addWidget(delete); rp.addLayout(floor_row)
        floor_area=QHBoxLayout(); floor_area.addWidget(QLabel('Суммарная площадь этажа:')); self.floor_area=QDoubleSpinBox(); self.floor_area.setRange(0,1e7); self.floor_area.setDecimals(1); self.floor_area.setSuffix(' м²'); self.floor_area.valueChanged.connect(self.main.update_floor_area); floor_area.addWidget(self.floor_area); rp.addLayout(floor_area)
        line=QFrame(); line.setFrameShape(QFrame.HLine); rp.addWidget(line)
        emp=QHBoxLayout(); emp.addWidget(QLabel('Сотрудников:')); self.param_employees=QSpinBox(); self.param_employees.setRange(1,100); emp.addWidget(self.param_employees); rp.addLayout(emp); rp.addWidget(QPushButton('Авторасчёт персонала',clicked=self.main.auto_calculate_staff))
        rp.addWidget(QPushButton('Автоопределение типов комнат',clicked=self.main.auto_classify_rooms))
        fill=QHBoxLayout(); fill.addWidget(QLabel('Заливка:')); self.fill_mode_combo=QComboBox(); self.fill_mode_combo.addItem('Стандартный','standard'); self.fill_mode_combo.addItem('По типу','type'); self.fill_mode_combo.currentIndexChanged.connect(self.main.update_room_opacity); fill.addWidget(self.fill_mode_combo); rp.addLayout(fill)
        rp.addWidget(QLabel('Прозрачность заливки')); self.opacity_slider=QSlider(Qt.Horizontal); self.opacity_slider.setRange(0,255); self.opacity_slider.setValue(30); self.opacity_slider.valueChanged.connect(self.main.update_room_opacity); rp.addWidget(self.opacity_slider)
        table_head=QHBoxLayout(); self.sort_combo=QComboBox(); self.sort_combo.addItems(['⇅','№','Пл','А-Я','Тип']); self.sort_combo.setFixedWidth(52); self.sort_combo.currentIndexChanged.connect(self.main.sort_rooms); table_head.addWidget(self.sort_combo); table_head.addWidget(QLabel('<b>Комнаты</b>')); table_head.addStretch(); self.btn_collapse_table=QPushButton('▼'); self.btn_collapse_table.setFixedWidth(28); self.btn_collapse_table.setFlat(True); self.btn_collapse_table.clicked.connect(self.main.toggle_room_table); table_head.addWidget(self.btn_collapse_table); rp.addLayout(table_head)
        self.room_table=QTableWidget(0,5); self.room_table.setHorizontalHeaderLabels(['№','Название','Тип','Площадь (м²)','Проходимость (чел/ч)']); self.room_table.horizontalHeader().setStretchLastSection(True); self.room_table.setSelectionBehavior(QTableWidget.SelectRows); self.room_table.setEditTriggers(QTableWidget.NoEditTriggers); self.room_table.verticalHeader().setVisible(False); self.room_table.setMouseTracking(True); self.room_table.cellEntered.connect(self.main.on_room_table_hover); self.room_table.cellClicked.connect(self.main.on_room_table_clicked); self.room_table.itemSelectionChanged.connect(self.main.on_room_table_select); rp.addWidget(self.room_table,1)
        create=QPushButton('Создать расписание →'); create.setMinimumHeight(42); create.clicked.connect(self.main.go_to_planning_screen); rp.addWidget(create)
        splitter.addWidget(right); splitter.setStretchFactor(0,3); splitter.setStretchFactor(1,1); layout.addWidget(splitter)
        self.plan_view.scene_changed.connect(self.main.on_scene_changed); self.plan_view.floor_rect_added.connect(self.main.on_floor_rect_added)
