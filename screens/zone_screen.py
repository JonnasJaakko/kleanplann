"""Объединённый экран зон ответственности и отчётности."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QGraphicsScene,QGraphicsView,QTextEdit,QSplitter,QMenu,QGraphicsPolygonItem
from PySide6.QtCore import Qt,QPoint,QPointF
from zone_manager import PRIORITY_BALANCED,PRIORITY_PROXIMITY,PRIORITY_AREA,PRIORITY_COUNT

class ZoneView(QGraphicsView):
    def __init__(self,scene,main):
        super().__init__(scene); self.main=main; self._pan=False; self._pan_pos=QPoint(); self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse); self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
    def wheelEvent(self,e):
        factor=1.15 if e.angleDelta().y()>0 else 1/1.15
        self.scale(factor,factor); e.accept()
    def mousePressEvent(self,e):
        if e.button()==Qt.RightButton:
            self._pan=True; self._pan_pos=e.position().toPoint(); self.setCursor(Qt.ClosedHandCursor); e.accept(); return
        if e.button()==Qt.LeftButton and getattr(self.main,'_exclude_mode_active',False):
            pos=self.mapToScene(e.position().toPoint())
            if self.main.toggle_excluded_room_at_scene(pos): e.accept(); return
        super().mousePressEvent(e)
    def mouseMoveEvent(self,e):
        if self._pan:
            p=e.position().toPoint(); d=p-self._pan_pos; self._pan_pos=p
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-d.x()); self.verticalScrollBar().setValue(self.verticalScrollBar().value()-d.y()); e.accept(); return
        if e.modifiers() & Qt.ShiftModifier:
            item=self.itemAt(e.position().toPoint()); rid=None; fi=None
            if item is not None:
                rid=item.data(Qt.UserRole); fi=item.data(2)
            if rid is not None: self.main.highlight_zone_employee(fi,rid)
            else: self.main.clear_zone_employee_highlight()
        else: self.main.clear_zone_employee_highlight()
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.RightButton:
            self._pan=False; self.setCursor(Qt.ArrowCursor); e.accept(); return
        super().mouseReleaseEvent(e)

class ZoneScreen(QWidget):
    def __init__(self,main_window): super().__init__(); self.main=main_window; self.setup_ui()
    def setup_ui(self):
        layout=QVBoxLayout(self); # layout.setContentsMargins(0,0,0,0); layout.setSpacing(0); layout.addWidget(QLabel('Зоны ответственности и отчётность'))
        splitter=QSplitter(Qt.Horizontal); self.zone_scene=QGraphicsScene(); self.zone_view=ZoneView(self.zone_scene,self.main); splitter.addWidget(self.zone_view)
        panel=QWidget(); pv=QVBoxLayout(panel); pv.addWidget(QLabel('<b>Приоритет распределения зон</b>'))
        self.priority_combo=QComboBox(); self.priority_combo.addItem('Сбалансированно',PRIORITY_BALANCED); self.priority_combo.addItem('Близость комнат',PRIORITY_PROXIMITY); self.priority_combo.addItem('Площадь',PRIORITY_AREA); self.priority_combo.addItem('Количество комнат',PRIORITY_COUNT); self.priority_combo.currentIndexChanged.connect(self.main.recalculate_zones); pv.addWidget(self.priority_combo)
        self.report_preview=QTextEdit(readOnly=True); self.report_preview.setMinimumWidth(430); pv.addWidget(self.report_preview,1)
        buttons=QHBoxLayout(); back=QPushButton('← Назад в редактор'); back.clicked.connect(self.main.back_to_editor); export=QPushButton('Экспорт'); export.clicked.connect(self.show_export_menu); save=QPushButton('Сохранить'); save.clicked.connect(self.main.save_project); buttons.addWidget(back); buttons.addWidget(export); buttons.addWidget(save); pv.addLayout(buttons)
        splitter.addWidget(panel); splitter.setStretchFactor(0,3); splitter.setStretchFactor(1,2); layout.addWidget(splitter)
    def show_export_menu(self):
        menu=QMenu(self); menu.addAction('В Word (.docx)',self.main.generate_docx); menu.addAction('В CSV (.csv)',self.main.export_csv); menu.addAction('В Excel (.xlsx)',self.main.export_xlsx); menu.exec(self.mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.rect().center())
