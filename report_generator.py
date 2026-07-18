# report_generator.py
import io, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection
from PIL import Image
import numpy as np
from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from project import Project
from cost_calculator import calculate_cost, _cleaning_frequency

def _draw_floor_plan(floor, image_path=None, dpi=200) -> io.BytesIO:
    """Рисует план одного этажа с раскрашенными комнатами."""
    if not floor.rooms:
        fig, ax = plt.subplots(figsize=(10,8))
        ax.text(0.5,0.5,'Нет комнат',transform=ax.transAxes,ha='center')
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi)
        plt.close(fig)
        buf.seek(0)
        return buf

    fig, ax = plt.subplots(figsize=(10,8), dpi=dpi)
    # Если есть изображение, добавляем как фон
    if image_path:
        try:
            pil_img = Image.open(image_path)
            np_img = np.array(pil_img)
            ax.imshow(np_img, extent=[0, np_img.shape[1]/100, 0, np_img.shape[0]/100])
        except:
            pass
    ax.set_aspect('equal')
    ax.axis('off')

    for room in floor.rooms:
        color = [c/255 for c in room.color[:3]] + [room.color[3]/255]
        poly = mpatches.Polygon(room.points, closed=True, fill=True,
                               facecolor=color, edgecolor='black', linewidth=1)
        ax.add_patch(poly)
        # номер комнаты
        cx = sum(p[0] for p in room.points)/len(room.points)
        cy = sum(p[1] for p in room.points)/len(room.points)
        ax.text(cx, cy, str(room.id+1), ha='center', va='center', fontsize=8, color='black')

    ax.autoscale()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf

def generate_report(project: Project, filepath: str):
    doc = Document()
    doc.add_heading(project.name, level=0)

    # Для каждого этажа
    for floor in project.floors:
        doc.add_heading(floor.name, level=1)
        # План этажа
        plan_img = _draw_floor_plan(floor, project.image_paths[0] if project.image_paths else None)
        doc.add_picture(plan_img, width=Inches(6))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Таблица комнат
        table = doc.add_table(rows=1, cols=5)
        hdr = table.rows[0].cells
        hdr[0].text = '№'; hdr[1].text = 'Название'; hdr[2].text = 'Тип'; hdr[3].text = 'Площадь, м²'; hdr[4].text = 'Интервал уборки'
        for room in floor.rooms:
            row_cells = table.add_row().cells
            row_cells[0].text = str(room.id+1)
            row_cells[1].text = room.name
            row_cells[2].text = room.room_type if room.room_type else "—"
            row_cells[3].text = f"{room.area_m2:.1f}"
            freq = _cleaning_frequency(room.traffic, project.weather_factor)
            row_cells[4].text = f"Каждые {freq:.1f} ч"

    # Сводка по сотрудникам и расписание
    doc.add_heading('Расписание уборки', level=1)
    # Группируем задачи по сотрудникам
    tasks_by_emp = {}
    for task in project.cleaning_tasks:
        tasks_by_emp.setdefault(task.employee, []).append(task)
    for emp_idx, emp_tasks in tasks_by_emp.items():
        name = project.employee_names[emp_idx] if emp_idx < len(project.employee_names) else f"Сотрудник {emp_idx+1}"
        doc.add_heading(name, level=2)
        # Таблица задач сотрудника
        emp_table = doc.add_table(rows=1, cols=5)
        e_hdr = emp_table.rows[0].cells
        e_hdr[0].text = 'Этаж'; e_hdr[1].text = 'Комната'; e_hdr[2].text = 'Начало'; e_hdr[3].text = 'Окончание'; e_hdr[4].text = 'Продолж. (мин)'
        for task in emp_tasks:
            row = emp_table.add_row().cells
            row[0].text = str(task.floor_index+1)
            room = next((r for f in project.floors for r in f.rooms if r.id == task.room_id), None)
            row[1].text = room.name if room else f"Комн. {task.room_id+1}"
            row[2].text = task.start_dt.strftime("%H:%M")
            row[3].text = task.end_dt.strftime("%H:%M")
            row[4].text = f"{(task.end_dt-task.start_dt).seconds//60}"
        doc.add_paragraph('')

    # Анализ затрат
    cost = calculate_cost(project)
    doc.add_heading('Анализ затрат', level=1)
    doc.add_paragraph(f"Общее время уборки: {cost['total_time_hours']} ч")
    doc.add_paragraph(f"Штат: {cost['staff_count']} чел., фонд времени: {cost['staff_hours']} ч")
    doc.add_paragraph(f"Переработка: {cost['overtime_hours']} ч")
    doc.add_paragraph(f"Затраты (штат с переработкой): {cost['cost_with_overtime']} руб.")
    doc.add_paragraph(f"Затраты (наём): {cost['cost_hire']} руб.")
    doc.add_paragraph(f"Рекомендация: {cost['recommendation']}")

    doc.save(filepath)