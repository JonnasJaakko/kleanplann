from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QFileDialog, QMessageBox
)
from cost_calculator import calculate_cost
from report_generator import generate_report
from calendar_export import export_tasks_csv, export_tasks_excel

PROJECTS_DIR = "projects"

class ReportScreen(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Отчёт и анализ затрат"))
        self.report_preview = QTextEdit()
        self.report_preview.setReadOnly(True)
        layout.addWidget(self.report_preview)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("Сохранить проект", clicked=self.main_window.save_project))
        btn_layout.addWidget(QPushButton("Создать DOCX", clicked=self.generate_docx))
        btn_layout.addWidget(QPushButton("Экспорт CSV", clicked=self.export_csv))
        btn_layout.addWidget(QPushButton("Экспорт Excel", clicked=self.export_xlsx))
        layout.addLayout(btn_layout)

        nav = QHBoxLayout()
        nav.addWidget(QPushButton("← Назад", clicked=lambda: self.main_window.stack.setCurrentIndex(2)))
        layout.addLayout(nav)

    def load_report(self):
        project = self.main_window.project
        if not project:
            return
        cost = calculate_cost(project)
        text = f"<h2>{project.name}</h2>"
        text += f"<p>Общее время уборки: {cost['total_time_hours']} ч</p>"
        text += f"<p>Затраты (штат с переработкой): {cost['cost_with_overtime']} руб</p>"
        text += f"<p>Затраты (наём): {cost['cost_hire']} руб</p>"
        text += f"<p><b>Рекомендация: {cost['recommendation']}</b></p>"
        text += "<h3>Расписание уборки</h3>"
        tasks_by_emp = {}
        for task in project.cleaning_tasks:
            tasks_by_emp.setdefault(task.employee, []).append(task)
        for emp_idx, tasks in tasks_by_emp.items():
            name = (project.employee_names[emp_idx]
                    if emp_idx < len(project.employee_names)
                    else f"Сотрудник {emp_idx+1}")
            text += f"<h4>{name}</h4>"
            text += ("<table border='1' cellspacing='0' cellpadding='4'>"
                     "<tr><th>Комната</th><th>Начало</th><th>Конец</th><th>Длит.</th></tr>")
            for t in tasks[:50]:
                room = self._find_room_by_id(t.room_id)
                room_name = room.name if room else str(t.room_id)
                dur = (t.end_dt - t.start_dt).seconds // 60
                text += (f"<tr><td>{room_name}</td>"
                         f"<td>{t.start_dt.strftime('%H:%M')}</td>"
                         f"<td>{t.end_dt.strftime('%H:%M')}</td>"
                         f"<td>{dur} мин</td></tr>")
            text += "</table>"
        self.report_preview.setHtml(text)

    def _find_room_by_id(self, room_id):
        for floor in self.main_window.project.floors:
            for room in floor.rooms:
                if room.id == room_id:
                    return room
        return None

    def generate_docx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", "", "Word (*.docx)")
        if path:
            try:
                generate_report(self.main_window.project, path)
                QMessageBox.information(self, "Готово", f"Отчёт сохранён: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт CSV", "", "CSV (*.csv)")
        if path:
            export_tasks_csv(self.main_window.project, path)
            QMessageBox.information(self, "Готово", f"График сохранён в {path}")

    def export_xlsx(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт Excel", "", "Excel (*.xlsx)")
        if path:
            export_tasks_excel(self.main_window.project, path)
            QMessageBox.information(self, "Готово", f"График сохранён в {path}")