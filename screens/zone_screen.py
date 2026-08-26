"""Объединённый экран зон ответственности и отчётности."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGraphicsScene, QGraphicsView, QTextBrowser, QSplitter, QDialog, QDialogButtonBox,
    QGraphicsPolygonItem, QGraphicsProxyWidget
)
from PySide6.QtCore import Qt, QPoint
from zone_manager import PRIORITY_BALANCED, PRIORITY_PROXIMITY, PRIORITY_AREA, PRIORITY_COUNT, PRIORITY_TIME


class ZoneView(QGraphicsView):
    def __init__(self, scene, main):
        super().__init__(scene)
        self.main = main
        self._pan = False
        self._pan_pos = QPoint()
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._pan = True
            self._pan_pos = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton and getattr(self.main, '_exclude_mode_active', False):
            if isinstance(self.itemAt(e.position().toPoint()), QGraphicsProxyWidget):
                super().mousePressEvent(e)
                return
            pos = self.mapToScene(e.position().toPoint())
            if self.main.toggle_excluded_room_at_scene(pos):
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan:
            p = e.position().toPoint()
            d = p - self._pan_pos
            self._pan_pos = p
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            e.accept()
            return
        if e.modifiers() & Qt.ShiftModifier:
            item = self.itemAt(e.position().toPoint())
            rid = None
            fi = None
            if item is not None:
                rid = item.data(Qt.UserRole)
                fi = item.data(2)
            if rid is not None:
                self.main.highlight_zone_employee(fi, rid)
            else:
                self.main.clear_zone_employee_highlight()
        else:
            self.main.clear_zone_employee_highlight()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            self._pan = False
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)


class ZoneScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        self.zone_scene = QGraphicsScene()
        self.zone_view = ZoneView(self.zone_scene, self.main)
        splitter.addWidget(self.zone_view)

        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.addWidget(QLabel('<b>Режим распределения / приоритет расписания</b>'))
        self.priority_combo = QComboBox()
        self.priority_combo.addItem('Сбалансированно', PRIORITY_BALANCED)
        self.priority_combo.addItem('Близость комнат', PRIORITY_PROXIMITY)
        self.priority_combo.addItem('Площадь', PRIORITY_AREA)
        self.priority_combo.addItem('Количество комнат', PRIORITY_COUNT)
        self.priority_combo.addItem('Время — равная нагрузка и общий финиш', PRIORITY_TIME)
        self.priority_combo.currentIndexChanged.connect(self.main.recalculate_zones)
        pv.addWidget(self.priority_combo)

        self.priority_help = QLabel()
        self.priority_help.setWordWrap(True)
        self.priority_help.setStyleSheet('color:#555; padding:4px;')
        pv.addWidget(self.priority_help)
        self._update_priority_help()
        self.priority_combo.currentIndexChanged.connect(lambda _: self._update_priority_help())

        floor_row = QHBoxLayout()
        floor_row.addWidget(QLabel('Этаж:'))
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.main.switch_floor)
        floor_row.addWidget(self.floor_combo)
        pv.addLayout(floor_row)

        self.exclude_finish_button = QPushButton('✓ Готово: обновить расписание')
        self.exclude_finish_button.setVisible(False)
        self.exclude_finish_button.clicked.connect(self.main._finish_exclude_and_continue)
        pv.addWidget(self.exclude_finish_button)

        self.report_preview = QTextBrowser()
        self.report_preview.setOpenLinks(False)
        self.report_preview.anchorClicked.connect(self.main.handle_report_room_link)
        self.report_preview.setMinimumWidth(520)
        pv.addWidget(self.report_preview, 1)

        buttons = QHBoxLayout()
        back = QPushButton('← Назад в редактор')
        back.clicked.connect(self.main.back_to_editor)
        export = QPushButton('Экспорт')
        export.clicked.connect(self.show_export_menu)
        save = QPushButton('Сохранить')
        save.clicked.connect(self.main.save_project)
        buttons.addWidget(back)
        buttons.addWidget(export)
        buttons.addWidget(save)
        pv.addLayout(buttons)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def _update_priority_help(self):
        mode = self.priority_combo.currentData()
        text = {
            PRIORITY_BALANCED: 'Балансирует реальную трудоёмкость зон.',
            PRIORITY_PROXIMITY: 'Сохраняет компактность маршрутов, но не даёт одной зоне стать существенно тяжелее другой.',
            PRIORITY_AREA: 'Балансирует зоны по площади с учётом нормативной трудоёмкости.',
            PRIORITY_COUNT: 'Распределяет количество помещений, при этом scheduler проверяет фактическую выполнимость.',
            PRIORITY_TIME: '<b>Время:</b> цель — максимально близкие рабочие часы сотрудников и одинаковое время окончания. Зафиксированные пользователем задачи не двигаются.',
        }.get(mode, '')
        self.priority_help.setText(text)

    def show_export_menu(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Экспорт отчёта')
        dialog.setMinimumWidth(300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Выберите формат файла:'))
        for title, action in [('Word (.docx)', self.main.generate_docx), ('CSV (.csv)', self.main.export_csv), ('Excel (.xlsx)', self.main.export_xlsx)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, fn=action: (dialog.accept(), fn()))
            layout.addWidget(button)
        cancel = QDialogButtonBox(QDialogButtonBox.Cancel)
        cancel.rejected.connect(dialog.reject)
        layout.addWidget(cancel)
        dialog.exec()
