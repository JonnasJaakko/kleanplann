from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QComboBox, QGraphicsScene, QGraphicsView, QToolTip,
    QGraphicsPolygonItem, QGraphicsTextItem, QGraphicsItem
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPolygonF

class ZoneScreen(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._setup_ui()

    @property
    def project(self):
        return self.main_window.project

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Распределение зон ответственности"))

        top = QHBoxLayout()
        self.zone_scene = QGraphicsScene()
        self.zone_view = QGraphicsView(self.zone_scene)
        self.zone_view.setMinimumSize(600, 400)
        self.zone_view.wheelEvent = lambda ev: self.zone_view.scale(
            1.15 if ev.angleDelta().y()>0 else 1/1.15,
            1.15 if ev.angleDelta().y()>0 else 1/1.15
        )
        top.addWidget(self.zone_view, 2)

        ctrl = QVBoxLayout()
        ctrl.addWidget(QLabel("Сотрудники:"))
        self.employee_list_widget = QListWidget()
        self.employee_list_widget.setDragDropMode(QListWidget.InternalMove)
        self.employee_list_widget.setDefaultDropAction(Qt.MoveAction)
        ctrl.addWidget(self.employee_list_widget)
        ctrl.addWidget(QPushButton("Добавить сотрудника",
                                   clicked=self.main_window.add_employee))
        ctrl.addWidget(QPushButton("Пересчитать зоны",
                                   clicked=self.main_window.recalculate_zones))

        self.zone_floor_combo = QComboBox()
        self.zone_floor_combo.addItem("Все этажи")
        self.zone_floor_combo.currentIndexChanged.connect(self.refresh_zone_display)
        ctrl.addWidget(QLabel("Этаж:"))
        ctrl.addWidget(self.zone_floor_combo)
        top.addLayout(ctrl)
        layout.addLayout(top)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад",
                                  clicked=lambda: self.main_window.stack.setCurrentIndex(1)))
        nav.addStretch()
        nav.addWidget(QPushButton("Далее →",
                                  clicked=self.main_window.go_to_planning_screen))
        layout.addLayout(nav)

    def load_zone(self):
        scene = self.zone_scene; scene.clear()
        project = self.project
        if project.image_paths:
            pix = QPixmap(project.image_paths[0])
            scene.addPixmap(pix)
            self.zone_view.setSceneRect(pix.rect())

        self.employee_list_widget.clear()
        for i in range(project.employees_count):
            name = (project.employee_names[i]
                    if i < len(project.employee_names)
                    else f"Сотрудник {i+1}")
            item = QListWidgetItem()
            widget = QWidget()
            vbox = QVBoxLayout(widget)
            vbox.setContentsMargins(4,2,4,2)
            name_btn = QPushButton(name)
            name_btn.setFlat(True)
            name_btn.clicked.connect(
                lambda checked=False, idx=i: self.main_window.rename_employee(idx))
            h_name = QHBoxLayout()
            h_name.addWidget(name_btn)
            btn_del = QPushButton("✕")
            btn_del.setFixedSize(24,24)
            btn_del.clicked.connect(
                lambda checked=False, it=item: self.main_window.remove_employee(it))
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

        self.main_window.recalculate_zones()

        self.zone_floor_combo.blockSignals(True)
        self.zone_floor_combo.clear()
        self.zone_floor_combo.addItem("Все этажи")
        for floor in project.floors:
            self.zone_floor_combo.addItem(floor.name)
        self.zone_floor_combo.setCurrentIndex(0)
        self.zone_floor_combo.blockSignals(False)

    def refresh_zone_display(self):
        scene = self.zone_scene
        for item in scene.items():
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                scene.removeItem(item)
            if isinstance(item, QGraphicsTextItem) and item.data(1) == "zone_label":
                scene.removeItem(item)
        project = self.project
        selected_floor = self.zone_floor_combo.currentText()
        for zone in project.zones:
            col = QColor(*zone.color); brush = QBrush(col); pen = QPen(Qt.black, 1)
            for rid in zone.room_ids:
                room = next((r for r in project.all_rooms() if r.id == rid), None)
                if room:
                    room_floor = None
                    for i, floor in enumerate(project.floors):
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
                    text.setHtml(
                        f"<div style='text-align:center; background-color:{col.name()}; "
                        f"color:white; padding:2px; border:1px solid black;'>"
                        f"{zone.employee_index+1}</div>")
                    text.setPos(cx-12, cy-10)
                    text.setData(1, "zone_label")
                    text.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                    text.setAcceptHoverEvents(True)
                    text.hoverEnterEvent = lambda ev, rid=rid, emp=zone.employee_index: QToolTip.showText(
                        ev.screenPos(), self.main_window._get_schedule_tip(rid, emp))
                    text.hoverLeaveEvent = lambda ev: QToolTip.hideText()
                    text.mousePressEvent = lambda ev, rid=rid: self.main_window.change_room_employee(rid)
                    scene.addItem(text)