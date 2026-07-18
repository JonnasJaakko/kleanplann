import math
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QLineF
from PySide6.QtGui import QPen, QColor, QBrush, QPainter, QCursor, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsEllipseItem, QGraphicsLineItem,
    QGraphicsItem, QApplication, QGraphicsPolygonItem, QGraphicsTextItem,
    QToolTip, QGraphicsRectItem
)
from room_builder import nearest_point_on_segment

# ---------- Undo/Redo ----------
class UndoCommand:
    def undo(self): pass
    def redo(self): pass

class AddWallCommand(UndoCommand):
    def __init__(self, scene, x1, y1, x2, y2):
        self.scene = scene
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.item = None
    def undo(self):
        if self.item: self.scene.removeItem(self.item)
    def redo(self):
        seg = WallSegmentItem(QPointF(self.x1, self.y1), QPointF(self.x2, self.y2))
        self.scene.addItem(seg)
        self.item = seg

class RemoveWallCommand(UndoCommand):
    def __init__(self, scene, x1, y1, x2, y2):
        self.scene = scene
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.item = None
    def undo(self):
        if self.item: self.scene.addItem(self.item)
    def redo(self):
        if self.item: self.scene.removeItem(self.item)

class UndoStack:
    def __init__(self, max_undo=32):
        self.undo_stack = []
        self.redo_stack = []
        self.max_undo = max_undo
    def push(self, cmd):
        self.redo_stack.clear()
        self.undo_stack.append(cmd)
        if len(self.undo_stack) > self.max_undo:
            self.undo_stack.pop(0)
    def undo(self):
        if self.undo_stack:
            cmd = self.undo_stack.pop()
            cmd.undo()
            self.redo_stack.append(cmd)
    def redo(self):
        if self.redo_stack:
            cmd = self.redo_stack.pop()
            cmd.redo()
            self.undo_stack.append(cmd)

# ---------- Графические элементы стен ----------
class WallVertexItem(QGraphicsEllipseItem):
    def __init__(self, pos, radius=4, parent=None):
        super().__init__(-radius, -radius, 2*radius, 2*radius, parent)
        self.setPos(pos)
        self.setBrush(Qt.red)
        self.setPen(QPen(Qt.black, 1))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(2)
        self.wall_items = []

class WallSegmentItem(QGraphicsLineItem):
    def __init__(self, start, end, parent=None):
        super().__init__(QLineF(start, end), parent)
        self.setPen(QPen(Qt.blue, 3))
        self.setZValue(1)
        self.start_vertex = WallVertexItem(start, parent=self)
        self.end_vertex = WallVertexItem(end, parent=self)
        self.start_vertex.wall_items.append(self)
        self.end_vertex.wall_items.append(self)

# ---------- PlanView ----------
class PlanView(QGraphicsView):
    calibration_finished = Signal(QLineF)
    scene_changed = Signal()
    floor_rect_added = Signal(float, float, float, float)  # x, y, w, h

    def __init__(self, scene, main_window, parent=None):
        super().__init__(scene, parent)
        self.main_window = main_window
        self.current_tool = 0
        self.drawing = False
        self.start_point = None
        self.temp_line = None
        self.calibration_start = None
        self.temp_calib_line = None
        self.undo_stack = UndoStack(max_undo=32)
        self.dragged_vertex = None
        self._snap_distance = 15
        self._highlighted_room = None
        self._drag_start_pos = None
        self._panning = False
        self._pan_start = QPointF()
        self._brush_source = None

        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.eraser_cursor = self._make_eraser_cursor()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

    def _make_eraser_cursor(self):
        radius = 10
        pix = QPixmap(radius*2+2, radius*2+2)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setPen(QPen(Qt.black, 2))
        p.drawEllipse(1, 1, radius*2, radius*2)
        p.end()
        return QCursor(pix, radius+1, radius+1)

    def set_tool(self, tool: int):
        self.current_tool = tool
        if tool == 0:
            self.setCursor(Qt.ArrowCursor)
        elif tool == 1:
            self.setCursor(self.eraser_cursor)
        elif tool == 5:
            self.setCursor(Qt.PointingHandCursor)
        elif tool == 6:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        self._reset_drawing()
        self._brush_source = None

    def _reset_drawing(self):
        if self.temp_line:
            self.scene().removeItem(self.temp_line)
            self.temp_line = None
        self.drawing = False
        self.start_point = None
        self.dragged_vertex = None
        self._drag_start_pos = None
        self._panning = False

    def wheelEvent(self, event):
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1/factor, 1/factor)

    def zoom_to_fit(self, margin=50):
        """Подгоняет масштаб так, чтобы все элементы сцены были видны с отступом."""
        rect = self.scene().itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            rect.adjust(-margin, -margin, margin, margin)
            self.fitInView(rect, Qt.KeepAspectRatio)

    def _snap_angle(self, angle):
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            return round(angle/90)*90
        return angle

    def _find_nearest_vertex(self, pos, exclude=None):
        best = None
        best_dist = self._snap_distance
        for item in self.scene().items():
            if isinstance(item, WallVertexItem) and item is not exclude:
                dist = (item.pos() - pos).manhattanLength()
                if dist < best_dist:
                    best_dist = dist
                    best = item
        return best

    def _find_nearest_segment(self, pos):
        best_seg = None
        best_pt = pos
        best_dist = self._snap_distance
        for item in self.scene().items():
            if isinstance(item, WallSegmentItem):
                line = item.line()
                pt = nearest_point_on_segment((pos.x(), pos.y()),
                                              (line.x1(), line.y1()),
                                              (line.x2(), line.y2()))
                dist = np.hypot(pt[0]-pos.x(), pt[1]-pos.y())
                if dist < best_dist:
                    best_dist = dist
                    best_seg = item
                    best_pt = QPointF(*pt)
        return best_seg, best_pt

    def _room_at_pos(self, pos):
        items = self.scene().items(pos)
        for item in items:
            if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) is not None:
                return item.data(Qt.UserRole)
        return None

    def highlight_room(self, room_id):
        self._highlight_room(room_id)

    def _highlight_room(self, room_id):
        if self._highlighted_room is not None:
            old_room = next((r for r in self.main_window.project.rooms if r.id == self._highlighted_room), None)
            if old_room:
                col = QColor(*old_room.color)
                brush = QBrush(col)
                for item in self.scene().items():
                    if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) == self._highlighted_room:
                        item.setBrush(brush)
            self._highlighted_room = None
        if room_id is not None:
            room = next((r for r in self.main_window.project.rooms if r.id == room_id), None)
            if room:
                col = QColor(*room.color)
                col.setAlpha(min(col.alpha() + 80, 255))
                brush = QBrush(col)
                for item in self.scene().items():
                    if isinstance(item, QGraphicsPolygonItem) and item.data(Qt.UserRole) == room_id:
                        item.setBrush(brush)
                self._highlighted_room = room_id

    # ---------- Обработка мыши ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.current_tool in (2, 4):
                scene_pos = self.mapToScene(event.pos())
                vertex = self._find_nearest_vertex(scene_pos)
                if vertex:
                    self.dragged_vertex = vertex
                    self._drag_start_pos = vertex.pos()
                    self.setCursor(Qt.ClosedHandCursor)
                    event.accept()
                    return
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())

            if self.current_tool == 6:  # Выделение этажа (прямоугольник)
                self.start_point = scene_pos
                self.drawing = True
                event.accept()
                return

            if self.current_tool == 5:  # Кисть
                room_id = self._room_at_pos(scene_pos)
                if room_id is not None:
                    room = next((r for r in self.main_window.project.rooms if r.id == room_id), None)
                    if room:
                        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
                            self._brush_source = (room.room_type, room.traffic)
                            QToolTip.showText(event.globalPos(), "Свойства скопированы")
                        elif self._brush_source is not None:
                            room.room_type = self._brush_source[0]
                            room.traffic = self._brush_source[1]
                            self.main_window.draw_rooms()
                            self.main_window.update_room_table()
                event.accept()
                return

            if self.current_tool == 0:
                room_id = self._room_at_pos(scene_pos)
                if room_id is not None:
                    self.main_window.edit_room_properties(room_id)
                    return
                super().mousePressEvent(event)
            elif self.current_tool == 1:
                region = QRectF(scene_pos.x()-8, scene_pos.y()-8, 16, 16)
                items = self.scene().items(region)
                for item in items:
                    if isinstance(item, WallSegmentItem):
                        line = item.line()
                        cmd = RemoveWallCommand(self.scene(), line.x1(), line.y1(), line.x2(), line.y2())
                        cmd.item = item
                        cmd.redo()
                        self.undo_stack.push(cmd)
                        self.scene_changed.emit()
                        event.accept()
                        return
                event.accept()
            elif self.current_tool == 2:
                vertex = self._find_nearest_vertex(scene_pos)
                if vertex:
                    self.start_point = vertex.pos()
                else:
                    seg, pt = self._find_nearest_segment(scene_pos)
                    self.start_point = pt if seg else scene_pos
                self.drawing = True
                event.accept()
            elif self.current_tool == 3:
                self.main_window.straighten_walls()
                event.accept()
            elif self.current_tool == 4:
                self.start_point = scene_pos
                self.drawing = True
                event.accept()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing and self.start_point:
            scene_pos = self.mapToScene(event.pos())
            end = scene_pos

            if self.current_tool == 6:  # SelectFloor
                rect = QRectF(self.start_point, scene_pos).normalized()
                if self.temp_line:
                    self.scene().removeItem(self.temp_line)
                self.temp_line = self.scene().addRect(rect, QPen(Qt.green, 2, Qt.DashLine))
                event.accept()
                return

            if self.current_tool == 2:
                snap_vertex = self._find_nearest_vertex(end)
                if snap_vertex:
                    end = snap_vertex.pos()
                else:
                    seg, pt = self._find_nearest_segment(end)
                    if seg:
                        end = pt
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    dx, dy = end.x()-self.start_point.x(), end.y()-self.start_point.y()
                    angle = math.atan2(dy, dx)*180/math.pi
                    snap = self._snap_angle(angle)
                    length = math.hypot(dx, dy)
                    end = QPointF(self.start_point.x()+length*math.cos(math.radians(snap)),
                                  self.start_point.y()+length*math.sin(math.radians(snap)))
                if self.temp_line:
                    self.temp_line.setLine(QLineF(self.start_point, end))
                else:
                    self.temp_line = self.scene().addLine(QLineF(self.start_point, end),
                                                           QPen(Qt.gray, 1, Qt.DashLine))
            elif self.current_tool == 4:
                rect = QRectF(self.start_point, scene_pos).normalized()
                if self.temp_line:
                    self.scene().removeItem(self.temp_line)
                self.temp_line = self.scene().addRect(rect, QPen(Qt.gray, 1, Qt.DashLine))
            event.accept()
            return

        if self.current_tool == 0:
            scene_pos = self.mapToScene(event.pos())
            room_id = self._room_at_pos(scene_pos)
            self._highlight_room(room_id)
        else:
            self._highlight_room(None)

        if self.dragged_vertex is not None:
            new_pos = self.mapToScene(event.pos())
            if QApplication.keyboardModifiers() & Qt.ControlModifier and self._drag_start_pos:
                dx = new_pos.x() - self._drag_start_pos.x()
                dy = new_pos.y() - self._drag_start_pos.y()
                angle = math.atan2(dy, dx) * 180 / math.pi
                snap = self._snap_angle(angle)
                length = math.hypot(dx, dy)
                new_pos = QPointF(self._drag_start_pos.x() + length * math.cos(math.radians(snap)),
                                  self._drag_start_pos.y() + length * math.sin(math.radians(snap)))
            snap_vertex = self._find_nearest_vertex(new_pos, exclude=self.dragged_vertex)
            if snap_vertex:
                new_pos = snap_vertex.pos()
            else:
                seg, pt = self._find_nearest_segment(new_pos)
                if seg:
                    new_pos = pt
            self.dragged_vertex.setPos(new_pos)
            for wall in self.dragged_vertex.wall_items:
                if wall.start_vertex == self.dragged_vertex:
                    wall.setLine(QLineF(new_pos, wall.line().p2()))
                else:
                    wall.setLine(QLineF(wall.line().p1(), new_pos))
                wall.start_vertex.setPos(wall.line().p1())
                wall.end_vertex.setPos(wall.line().p2())
            self.scene_changed.emit()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drawing and self.start_point:
            end = self.mapToScene(event.pos())

            if self.current_tool == 6:  # SelectFloor – завершили прямоугольник
                rect = QRectF(self.start_point, end).normalized()
                if rect.width() > 10 and rect.height() > 10:
                    self.floor_rect_added.emit(rect.x(), rect.y(), rect.width(), rect.height())
                if self.temp_line:
                    self.scene().removeItem(self.temp_line)
                    self.temp_line = None
                self.drawing = False
                self.start_point = None
                event.accept()
                return

            if self.current_tool == 2:
                snap_vertex = self._find_nearest_vertex(end)
                if snap_vertex:
                    end = snap_vertex.pos()
                else:
                    seg, pt = self._find_nearest_segment(end)
                    if seg:
                        end = pt
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    dx, dy = end.x()-self.start_point.x(), end.y()-self.start_point.y()
                    angle = math.atan2(dy, dx)*180/math.pi
                    snap = self._snap_angle(angle)
                    length = math.hypot(dx, dy)
                    end = QPointF(self.start_point.x()+length*math.cos(math.radians(snap)),
                                  self.start_point.y()+length*math.sin(math.radians(snap)))
                if (end - self.start_point).manhattanLength() > 2:
                    cmd = AddWallCommand(self.scene(), self.start_point.x(), self.start_point.y(),
                                         end.x(), end.y())
                    cmd.redo()
                    self.undo_stack.push(cmd)
                    self.scene_changed.emit()
            elif self.current_tool == 4:
                rect = QRectF(self.start_point, end).normalized()
                if rect.width() > 2 and rect.height() > 2:
                    x1, y1 = rect.topLeft().x(), rect.topLeft().y()
                    x2, y2 = rect.topRight().x(), rect.topRight().y()
                    x3, y3 = rect.bottomRight().x(), rect.bottomRight().y()
                    x4, y4 = rect.bottomLeft().x(), rect.bottomLeft().y()
                    for x1w, y1w, x2w, y2w in [(x1, y1, x2, y2), (x2, y2, x3, y3),
                                               (x3, y3, x4, y4), (x4, y4, x1, y1)]:
                        cmd = AddWallCommand(self.scene(), x1w, y1w, x2w, y2w)
                        cmd.redo()
                        self.undo_stack.push(cmd)
                    self.scene_changed.emit()
            self._reset_drawing()
            event.accept()
        elif event.button() == Qt.RightButton:
            if self.dragged_vertex:
                self.dragged_vertex = None
                self._drag_start_pos = None
                self.setCursor(Qt.CrossCursor)
            self.setDragMode(QGraphicsView.NoDrag)
            super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undo_stack.undo()
            self.scene_changed.emit()
            event.accept()
        elif event.key() == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            self.undo_stack.redo()
            self.scene_changed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def collect_walls(self):
        from project import Wall
        walls = []
        for item in self.scene().items():
            if isinstance(item, WallSegmentItem):
                line = item.line()
                walls.append(Wall(line.x1(), line.y1(), line.x2(), line.y2()))
        return walls