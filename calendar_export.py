"""
Экспорт графика уборки в CSV и Excel.
"""
import csv
from datetime import datetime
from typing import List
from project import CleaningTask, Project, Room

def export_tasks_csv(project: Project, filename: str):
    tasks = project.cleaning_tasks
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Сотрудник", "Комната", "Тип", "Начало", "Окончание", "Продолжительность (мин)"])
        for task in tasks:
            room = next((r for r in project.rooms if r.id == task.room_id), None)
            room_name = f"Комната {task.room_id+1}" if room else str(task.room_id)
            room_type = room.room_type if room else ""
            duration = (task.end_dt - task.start_dt).total_seconds() / 60
            writer.writerow([
                f"Сотрудник {task.employee+1}",
                room_name,
                room_type,
                task.start_dt.strftime("%Y-%m-%d %H:%M"),
                task.end_dt.strftime("%Y-%m-%d %H:%M"),
                f"{duration:.0f}"
            ])

def export_tasks_excel(project: Project, filename: str):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "График уборки"
    ws.append(["Сотрудник", "Комната", "Тип", "Начало", "Окончание", "Продолжительность (мин)"])
    for task in project.cleaning_tasks:
        room = next((r for r in project.rooms if r.id == task.room_id), None)
        room_name = f"Комната {task.room_id+1}" if room else str(task.room_id)
        room_type = room.room_type if room else ""
        duration = (task.end_dt - task.start_dt).total_seconds() / 60
        ws.append([
            f"Сотрудник {task.employee+1}",
            room_name,
            room_type,
            task.start_dt.strftime("%Y-%m-%d %H:%M"),
            task.end_dt.strftime("%Y-%m-%d %H:%M"),
            duration
        ])
    wb.save(filename)