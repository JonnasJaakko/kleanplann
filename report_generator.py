# report_generator.py
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection
from PIL import Image
import numpy as np
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from project import Project, Zone
from zone_manager import ZONE_COLORS
from collections import OrderedDict
from datetime import timedelta


def _hex_color(rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return '#{:02X}{:02X}{:02X}'.format(r, g, b)


def _set_cell_shading(cell, color_hex):
    """Заливка ячейки таблицы DOCX цветом."""
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), color_hex)
    shading.set(qn('w:val'), 'clear')
    cell._tc.get_or_add_tcPr().append(shading)


def _find_room(project, room_id):
    for floor in project.floors:
        for r in floor.rooms:
            if r.id == room_id:
                return r
    return None


def _draw_floor_plan(project: Project, floor, image_path=None, dpi=200) -> io.BytesIO:
    """Рисует план этажа: комнаты окрашены в цвет сотрудника + таблички."""
    if not floor.rooms:
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(0.5, 0.5, 'Нет комнат', transform=ax.transAxes, ha='center')
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi)
        plt.close(fig)
        buf.seek(0)
        return buf

    # карта комната -> сотрудник
    room_to_emp = {}
    for zone in project.zones:
        for rid in zone.room_ids:
            room_to_emp[rid] = zone.employee_index

    # карта комната -> цвет зоны
    room_to_color = {}
    for zone in project.zones:
        col = tuple(zone.color[:3])
        for rid in zone.room_ids:
            room_to_color[rid] = col

    fig, ax = plt.subplots(figsize=(10, 8), dpi=dpi)
    if image_path:
        try:
            pil_img = Image.open(image_path)
            np_img = np.array(pil_img)
            ax.imshow(np_img, extent=[0, np_img.shape[1] / 100, 0, np_img.shape[0] / 100])
        except Exception:
            pass
    ax.set_aspect('equal')
    ax.axis('off')

    for room in floor.rooms:
        # цвет комнаты — цвет сотрудника (если назначен), иначе собственный
        color = room_to_color.get(room.id, tuple(room.color[:3]))
        face = [c / 255 for c in color] + [0.55]
        poly = mpatches.Polygon(room.points, closed=True, fill=True,
                                facecolor=face, edgecolor='black', linewidth=1)
        ax.add_patch(poly)
        cx = sum(p[0] for p in room.points) / len(room.points)
        cy = sum(p[1] for p in room.points) / len(room.points)
        emp_num = room_to_emp.get(room.id, 0) + 1
        # табличка: номер сотрудника крупно + название/№/площадь
        ax.text(cx, cy + 0.5, str(emp_num), ha='center', va='center',
                fontsize=14, fontweight='bold', color='white',
                bbox=dict(facecolor=_hex_color(color), edgecolor='black',
                          pad=1.5, alpha=0.9))
        # помечаем приоритетные комнаты звёздочкой
        prio = "★ " if room.priority else ""
        label = f"{prio}{room.name}\n№{room.id + 1} ({room.area_m2:.0f} м²)"
        ax.text(cx, cy - 2.2, label, ha='center', va='center',
                fontsize=6, color='black',
                bbox=dict(facecolor='white', edgecolor='black',
                          pad=1.0, alpha=0.85))

    ax.autoscale()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0.05)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_report(project: Project, filepath: str):
    doc = Document()
    # базовые стили
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(10)

    # ---------- ТИТУЛЬНЫЙ ЛИСТ ----------
    doc.add_heading(project.name, level=0)
    doc.add_paragraph('')
    doc.add_paragraph(f'Период планирования: {project.start_date.strftime("%d.%m.%Y")} — '
                      f'{project.end_date.strftime("%d.%m.%Y")}')
    doc.add_paragraph(f'Количество сотрудников: {project.employees_count}')
    total_area = sum(r.area_m2 for r in project.all_rooms())
    doc.add_paragraph(f'Общая убираемая площадь: {total_area:.0f} м²')
    if project.shifts:
        s = project.shifts[0]
        doc.add_paragraph(f'Смена: с {s.start_time} до {s.end_time}')
    if project.breaks:
        b = project.breaks[0]
        doc.add_paragraph(f'Обеденный перерыв: с {b[0]} до {b[1]}')
    doc.add_page_break()

    # ---------- ЛИСТ 1: Схема распределения зон ----------
    doc.add_heading('Схема распределения зон', level=1)
    doc.add_paragraph('Цветом показана зона ответственности каждого сотрудника. '
                      'Звёздочкой (★) отмечены приоритетные комнаты.')

    for floor in project.floors:
        if not floor.rooms:
            continue
        doc.add_heading(floor.name, level=2)
        plan_img = _draw_floor_plan(project, floor,
                                    project.image_paths[0] if project.image_paths else None)
        doc.add_picture(plan_img, width=Inches(6.5))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Легенда: сотрудник, комнаты с площадью
        legend_table = doc.add_table(rows=1, cols=5)
        legend_table.style = 'Table Grid'
        legend_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = legend_table.rows[0].cells
        headers = ['Сотрудник', 'Имя', 'Комнат', 'Комнаты (площадь, м²)', 'Суммарная площадь, м²']
        for i, h in enumerate(headers):
            hdr[i].text = h
            hdr[i].paragraphs[0].runs[0].bold = True

        for zone in project.zones:
            room_ids = zone.room_ids
            rooms_on_floor = [r for r in floor.rooms if r.id in room_ids]
            if not rooms_on_floor:
                continue
            row_cells = legend_table.add_row().cells
            color_hex = _hex_color(zone.color[:3])
            _set_cell_shading(row_cells[0], color_hex)
            row_cells[0].text = str(zone.employee_index + 1)
            row_cells[0].paragraphs[0].runs[0].bold = True
            row_cells[0].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            name = project.employee_names[zone.employee_index] \
                if zone.employee_index < len(project.employee_names) else f"Сотрудник {zone.employee_index + 1}"
            row_cells[1].text = name
            row_cells[2].text = str(len(rooms_on_floor))
            detail = []
            for r in sorted(rooms_on_floor, key=lambda x: x.id):
                prio = "★ " if r.priority else ""
                t = f" ({r.room_type})" if r.room_type else ""
                detail.append(f"{prio}№{r.id + 1} {r.name}{t} — {r.area_m2:.0f} м²")
            row_cells[3].text = '\n'.join(detail)
            row_cells[4].text = f"{sum(r.area_m2 for r in rooms_on_floor):.0f}"
        doc.add_paragraph('')

    # ---------- ЛИСТ 2: Расписание уборки ----------
    doc.add_page_break()
    doc.add_heading('Расписание уборки', level=1)
    doc.add_paragraph(
        f'Период: {project.start_date.strftime("%d.%m.%Y")} — '
        f'{project.end_date.strftime("%d.%m.%Y")}.'
        f' Для каждого сотрудника указано, какие комнаты и в какое время'
        f' необходимо убирать каждый день.'
    )

    tasks_by_emp_day = OrderedDict()
    for task in project.cleaning_tasks:
        day_key = task.start_dt.date()
        tasks_by_emp_day.setdefault(task.employee, OrderedDict())
        tasks_by_emp_day[task.employee].setdefault(day_key, [])
        tasks_by_emp_day[task.employee][day_key].append(task)

    for emp_idx, days in tasks_by_emp_day.items():
        name = project.employee_names[emp_idx] \
            if emp_idx < len(project.employee_names) else f"Сотрудник {emp_idx + 1}"
        doc.add_heading(f'{name}', level=2)

        for day, tasks in days.items():
            doc.add_heading(f'{day.strftime("%d.%m.%Y")} ({_weekday_ru(day.weekday())})', level=3)
            emp_tasks = sorted(tasks, key=lambda t: t.start_dt)

            table = doc.add_table(rows=1, cols=6)
            table.style = 'Table Grid'
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            hdr = table.rows[0].cells
            headers = ['№', 'Комната', 'Площадь, м²', 'Начало', 'Окончание', 'Продолж., мин']
            for i, h in enumerate(headers):
                hdr[i].text = h
                hdr[i].paragraphs[0].runs[0].bold = True

            for t in emp_tasks:
                room = _find_room(project, t.room_id)
                row = table.add_row().cells
                row[0].text = str(t.room_id + 1)
                name_txt = room.name if room else f"Комн. {t.room_id + 1}"
                if room and room.room_type:
                    name_txt += f" ({room.room_type})"
                if room and room.priority:
                    name_txt = "★ " + name_txt
                row[1].text = name_txt
                row[2].text = f"{room.area_m2:.0f}" if room else "—"
                row[3].text = t.start_dt.strftime('%H:%M')
                row[4].text = t.end_dt.strftime('%H:%M')
                row[5].text = str(int((t.end_dt - t.start_dt).total_seconds() // 60))
            doc.add_paragraph('')

    # ---------- Анализ затрат ----------
    from cost_calculator import calculate_cost
    cost = calculate_cost(project)

    # Validator результат (используем те же данные scheduler)
    from schedule_validator import validate_schedule
    v = validate_schedule(project)

    doc.add_heading('Анализ затрат', level=1)
    doc.add_paragraph(f"Общее время уборки (scheduler): {cost['total_time_hours']} ч")
    doc.add_paragraph(f"Штат: {cost['staff_count']} чел., фонд времени: {cost['staff_hours']} ч")
    doc.add_paragraph(f"Переработка: {cost['overtime_hours']} ч")
    doc.add_paragraph(f"Затраты (штат с переработкой): {cost['cost_with_overtime']} руб.")
    doc.add_paragraph(f"Затраты (наём): {cost['cost_hire']} руб.")
    doc.add_paragraph(f"Рекомендация: {cost['recommendation']}")

    doc.add_heading('Валидация расписания', level=2)
    doc.add_paragraph(f"Статус: {'✓ ВЫПОЛНИМО' if v['valid'] else '✗ НЕВЫПОЛНИМО'}")
    doc.add_paragraph(f"Комнат всего: {v['rooms_total']}, активных: {v['active_rooms']}, "
                      f"запланировано: {v['scheduled_rooms']}, не запланировано: {v['unscheduled_rooms']}")
    doc.add_paragraph(f"Задач всего: {v['tasks_total']}")
    doc.add_paragraph(f"Конфликтов времени: {v['time_conflicts']}")
    doc.add_paragraph(f"Нарушений обеда: {v['break_violations']}")
    doc.add_paragraph(f"Задач вне смены: {v['out_of_shift_tasks']}")
    doc.add_paragraph(f"Нарушений частоты: {v['frequency_violations']} "
                      f"(требуется {v['frequency_required']}, запланировано {v['frequency_scheduled']})")
    doc.add_paragraph(f"Трудоёмкость: {v['cleaning_minutes']} мин уборки, "
                      f"{v['transit_minutes']} мин переходов, всего {v['total_hours']} ч")
    doc.add_paragraph(f"Стоимость (по validator): {v['cost']} руб., переработка: {v['overtime_minutes']} мин")

    if v['missed_rooms']:
        doc.add_heading('Не запланированные помещения', level=2)
        for m in v['missed_rooms'][:20]:
            doc.add_paragraph(f"• {m}", style='List Bullet')
        if len(v['missed_rooms']) > 20:
            doc.add_paragraph(f"... и ещё {len(v['missed_rooms']) - 20}")

    doc.save(filepath)


def _weekday_ru(idx: int) -> str:
    days = ['понедельник', 'вторник', 'среда', 'четверг',
            'пятница', 'суббота', 'воскресенье']
    return days[idx]