"""Экспорт полного отчёта KleanPlann в Word."""
import io
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from PIL import Image
import numpy as np
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from cost_calculator import calculate_cost
from schedule_validator import validate_schedule


def _hex_color(rgb):
    return '#{:02X}{:02X}{:02X}'.format(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _set_cell_shading(cell, color_hex):
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), color_hex)
    shading.set(qn('w:val'), 'clear')
    cell._tc.get_or_add_tcPr().append(shading)


def _find_room(project, room_id, floor_index=None):
    if floor_index is not None and 0 <= floor_index < len(project.floors):
        for r in project.floors[floor_index].rooms:
            if r.id == room_id:
                return r
    for f in project.floors:
        for r in f.rooms:
            if r.id == room_id:
                return r
    return None


def _zone_for(project, floor_index, room_id):
    for z in project.zones:
        if getattr(z, 'floor_index', 0) == floor_index and room_id in z.room_ids:
            return z
    # Compatibility with old projects.
    for z in project.zones:
        if room_id in z.room_ids:
            return z
    return None


def _weather_name(project):
    return {1.0: 'Ясно', 1.2: 'Дождь', 1.5: 'Снег', 1.8: 'Сильный дождь'}.get(
        float(getattr(project, 'weather_factor', 1.0)), 'Не указано')


def _draw_floor_plan(project, floor, employee_index=None, dpi=180):
    """Изображение этажа с зонами. Если задан сотрудник — подсвечивается его зона."""
    fig, ax = plt.subplots(figsize=(7.2, 5.2), dpi=dpi)
    image_path = getattr(floor, 'image_path', None)
    if image_path:
        try:
            img = Image.open(image_path)
            arr = np.array(img)
            ax.imshow(arr, extent=[0, arr.shape[1], 0, arr.shape[0]], alpha=0.45)
        except Exception:
            pass

    for room in floor.rooms:
        zone = _zone_for(project, floor.index, room.id)
        if zone:
            rgb = tuple(zone.color[:3])
        else:
            rgb = tuple(room.color[:3])
        alpha = 0.78 if employee_index is None or (zone and zone.employee_index == employee_index) else 0.12
        poly = mpatches.Polygon(room.points, closed=True, facecolor=[x/255 for x in rgb],
                                alpha=alpha, edgecolor='black', linewidth=0.7)
        ax.add_patch(poly)
        cx = sum(x for x, _ in room.points) / len(room.points)
        cy = sum(y for _, y in room.points) / len(room.points)
        if zone:
            ax.text(cx, cy, str(zone.employee_index + 1), ha='center', va='center',
                    fontsize=9, fontweight='bold', color='white',
                    bbox=dict(facecolor=_hex_color(rgb), edgecolor='black', pad=1.2))
        ax.text(cx, cy - 10, f"№{room.id+1}", ha='center', va='center', fontsize=5,
                bbox=dict(facecolor='white', alpha=0.75, pad=0.7))

    ax.set_aspect('equal')
    ax.axis('off')
    ax.autoscale()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.04)
    plt.close(fig)
    buf.seek(0)
    return buf


def _employee_stats(project, emp):
    tasks = [t for t in project.cleaning_tasks if t.employee == emp]
    minutes = sum((t.end_dt - t.start_dt).total_seconds()/60 for t in tasks)
    overtime = sum((t.end_dt - t.start_dt).total_seconds()/60 for t in tasks if getattr(t, 'is_overtime', False))
    rooms = {(t.floor_index, t.room_id) for t in tasks}
    area = sum((_find_room(project, rid, fi).area_m2 if _find_room(project, rid, fi) else 0) for fi, rid in rooms)
    return tasks, minutes, overtime, rooms, area


def _style_header(row):
    for cell in row.cells:
        for run in cell.paragraphs[0].runs:
            run.bold = True


def _add_schedule_table(doc, tasks, project, compact=False):
    table = doc.add_table(rows=1, cols=7)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0]
    labels = ['Этаж', '№', 'Комната', 'Площадь, м²', 'Начало', 'Окончание', 'Статус']
    for i, label in enumerate(labels):
        hdr.cells[i].text = label
    _style_header(hdr)
    for t in sorted(tasks, key=lambda x: x.start_dt):
        room = _find_room(project, t.room_id, t.floor_index)
        row = table.add_row().cells
        overtime = bool(getattr(t, 'is_overtime', False))
        if overtime:
            for cell in row:
                _set_cell_shading(cell, 'F4CCCC')
        row[0].text = str(t.floor_index + 1)
        row[1].text = str(t.room_id + 1)
        row[2].text = room.name if room else f'Комната {t.room_id + 1}'
        row[3].text = f'{room.area_m2:.1f}' if room else '—'
        row[4].text = t.start_dt.strftime('%H:%M')
        row[5].text = t.end_dt.strftime('%H:%M')
        row[6].text = 'СВЕРХ СМЕНЫ' if overtime else 'В смене'
    return table


def generate_report(project, filepath):
    doc = Document()
    normal = doc.styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(9)

    cost = calculate_cost(project)
    validation = validate_schedule(project)
    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not getattr(r, 'disabled', False)]
    disabled_rooms = [r for r in all_rooms if getattr(r, 'disabled', False)]
    scheduled_keys = {(t.floor_index, t.room_id) for t in project.cleaning_tasks}
    missed = []
    for fi, floor in enumerate(project.floors):
        for r in floor.rooms:
            if not r.disabled and (fi, r.id) not in scheduled_keys:
                missed.append((fi, r))

    # ---- Общая отчётность ----
    doc.add_heading(project.name, level=0)
    doc.add_paragraph(f"Период: {project.start_date:%d.%m.%Y} — {project.end_date:%d.%m.%Y}")
    doc.add_paragraph(f"Сотрудников: {project.employees_count}; общая площадь: {sum(r.area_m2 for r in all_rooms):.1f} м²")
    shift = project.shifts[0] if project.shifts else None
    lunch = project.breaks[0] if project.breaks else None
    doc.add_paragraph(f"Смена: {shift.start_time}–{shift.end_time}" if shift else "Смена: не задана")
    doc.add_paragraph(f"Обед: {lunch[0]}–{lunch[1]}" if lunch else "Обед: не задан")
    doc.add_paragraph(f"Погода: {_weather_name(project)}; тип уборки: {getattr(project, 'cleaning_type', 'поддерживающая')}")
    doc.add_paragraph(f"Зарплата: {project.hourly_rate:.2f} руб/ч; надбавка за переработку: {getattr(project, 'overtime_premium_percent', 50):.1f}%")

    doc.add_heading('Итоги', level=1)
    doc.add_paragraph(
        f"Валидация: {'ВЫПОЛНИМО' if validation.get('valid') else 'ЕСТЬ НАРУШЕНИЯ'}; "
        f"конфликтов: {validation.get('time_conflicts', 0)}; "
        f"нарушений обеда: {validation.get('break_violations', 0)}; "
        f"нарушений частоты: {validation.get('frequency_violations', 0)}; "
        f"вне смены: {validation.get('out_of_shift_tasks', 0)}."
    )
    for line in [
        f"Общее трудовое время: {cost['total_time_hours']:.2f} ч",
        f"Переработка: {cost['overtime_hours']:.2f} ч",
        f"Стоимость с учётом переработки: {cost['cost_with_overtime']:.2f} руб.",
        f"Рекомендуемый штат: {cost['needed_employees']} чел.; {cost['recommendation']}",
        f"Активных помещений: {len(active_rooms)}; исключено: {len(disabled_rooms)}; не запланировано: {len(missed)}",
        f"Задач уборки: {len(project.cleaning_tasks)}",
    ]:
        doc.add_paragraph(line, style='List Bullet')

    doc.add_heading('Сотрудники', level=1)
    for emp in range(project.employees_count):
        tasks, minutes, overtime, rooms, area = _employee_stats(project, emp)
        name = project.employee_names[emp] if emp < len(project.employee_names) else f'Сотрудник {emp+1}'
        doc.add_heading(name, level=2)
        doc.add_paragraph(f"Работа: {minutes/60:.2f} ч; комнат: {len(rooms)}; площадь: {area:.1f} м²; переработка: {overtime/60:.2f} ч")
        _add_schedule_table(doc, tasks, project)

    if disabled_rooms or missed:
        doc.add_heading('Помещения без уборки', level=1)
        for r in disabled_rooms:
            fi = next((i for i,f in enumerate(project.floors) if r in f.rooms), 0)
            doc.add_paragraph(f"Этаж {fi+1}, №{r.id+1} {r.name} — исключено пользователем", style='List Bullet')
        for fi, r in missed:
            doc.add_paragraph(f"Этаж {fi+1}, №{r.id+1} {r.name} — не попало в расписание", style='List Bullet')

    doc.add_page_break()
    doc.add_heading('Зоны ответственности', level=1)
    for fi, floor in enumerate(project.floors):
        if not floor.rooms:
            continue
        doc.add_heading(floor.name, level=2)
        doc.add_picture(_draw_floor_plan(project, floor), width=Inches(6.7))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        legend = doc.add_table(rows=1, cols=5); legend.style = 'Table Grid'
        for i,h in enumerate(['Сотрудник','Комнат','Площадь','Комнаты','Цвет']): legend.rows[0].cells[i].text=h
        _style_header(legend.rows[0])
        for emp in range(project.employees_count):
            rooms = [r for r in floor.rooms if (z:=_zone_for(project, fi, r.id)) and z.employee_index == emp]
            if not rooms: continue
            z = _zone_for(project, fi, rooms[0].id)
            row=legend.add_row().cells
            _set_cell_shading(row[0], _hex_color(z.color[:3]))
            row[0].text=project.employee_names[emp] if emp < len(project.employee_names) else f'Сотрудник {emp+1}'
            row[1].text=str(len(rooms)); row[2].text=f'{sum(r.area_m2 for r in rooms):.1f}'
            row[3].text=', '.join(f'№{r.id+1}' for r in sorted(rooms,key=lambda r:r.id))
            row[4].text=_hex_color(z.color[:3])

    # ---- Индивидуальные горизонтальные листы ----
    for emp in range(project.employees_count):
        name = project.employee_names[emp] if emp < len(project.employee_names) else f'Сотрудник {emp+1}'
        for fi, floor in enumerate(project.floors):
            zone_rooms = [r for r in floor.rooms if (z:=_zone_for(project, fi, r.id)) and z.employee_index == emp]
            if not zone_rooms:
                continue
            section = doc.add_section()
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = section.page_height, section.page_width
            section.top_margin = Inches(0.35); section.bottom_margin = Inches(0.35)
            section.left_margin = Inches(0.35); section.right_margin = Inches(0.35)
            doc.add_heading(f'{name} — зона ответственности, {floor.name}', level=1)
            p = doc.add_paragraph()
            p.add_run(f'Погода: {_weather_name(project)} | Тип: {getattr(project, "cleaning_type", "поддерживающая")} | ').bold = True
            tasks = [t for t in project.cleaning_tasks if t.employee == emp and t.floor_index == fi]
            _, minutes, overtime, _, area = _employee_stats(project, emp)
            p.add_run(f'Площадь этажа в зоне: {sum(r.area_m2 for r in zone_rooms):.1f} м² | Переработка: {overtime/60:.2f} ч')

            outer = doc.add_table(rows=1, cols=2)
            outer.autofit = False
            outer.columns[0].width = Inches(5.0); outer.columns[1].width = Inches(5.0)
            left, right = outer.rows[0].cells
            left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            left.paragraphs[0].text = 'ПЛАН ЗОНЫ'
            left.paragraphs[0].runs[0].bold = True
            left.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = left.add_paragraph().add_run(); run.add_picture(_draw_floor_plan(project, floor, employee_index=emp), width=Inches(4.8))
            right.paragraphs[0].text = 'ТАБЛИЦА ОБЯЗАННОСТЕЙ'
            right.paragraphs[0].runs[0].bold = True
            ttable = right.add_table(rows=1, cols=5)
            ttable.style='Table Grid'
            for i,h in enumerate(['№','Комната','Площадь','Время','Статус']): ttable.rows[0].cells[i].text=h
            _style_header(ttable.rows[0])
            for t in sorted(tasks,key=lambda x:x.start_dt):
                room=_find_room(project,t.room_id,fi); row=ttable.add_row().cells
                if getattr(t,'is_overtime',False):
                    for c in row: _set_cell_shading(c,'F4CCCC')
                row[0].text=str(t.room_id+1); row[1].text=room.name if room else '—'; row[2].text=f'{room.area_m2:.1f}' if room else '—'
                row[3].text=f'{t.start_dt:%H:%M}–{t.end_dt:%H:%M}'
                row[4].text='СВЕРХ СМЕНЫ' if getattr(t,'is_overtime',False) else 'В смене'

    doc.save(filepath)
