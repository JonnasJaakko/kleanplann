from datetime import datetime, date, timedelta, time
from typing import List, Tuple
from project import Project, Room, CleaningTask, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day, TRANSIT_TIME_MINUTES
import math

def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h*60 + m

def _distance(room1: Room, room2: Room):
    """Приближённое расстояние между центрами комнат."""
    c1 = (sum(p[0] for p in room1.points)/len(room1.points), sum(p[1] for p in room1.points)/len(room1.points))
    c2 = (sum(p[0] for p in room2.points)/len(room2.points), sum(p[1] for p in room2.points)/len(room2.points))
    return math.hypot(c1[0]-c2[0], c1[1]-c2[1])

def plan_cleaning_schedule(project: Project) -> List[CleaningTask]:
    tasks = []
    employees = project.employees_count
    weather = project.weather_factor
    # Для каждого этажа отдельно планируем
    for floor in project.floors:
        rooms = floor.rooms
        if not rooms:
            continue
        # Определяем, сколько раз нужно убрать каждую комнату за весь период
        total_days = (project.end_date - project.start_date).days + 1
        room_tasks = []
        for room in rooms:
            freq = get_frequency_per_day(room.room_type) * weather
            time_per_clean = get_cleaning_time_minutes(room.room_type, room.area_m2)
            for day in range(total_days):
                for _ in range(int(freq)):
                    room_tasks.append((room, time_per_clean, day))
        # Группируем задачи по сотрудникам (жадное распределение по близости)
        # Для простоты: раздаём задачи по кругу
        tasks_per_emp = [[] for _ in range(employees)]
        for i, (room, t, day) in enumerate(room_tasks):
            tasks_per_emp[i % employees].append((room, t, day))
        # Генерируем конкретные времена
        for emp_idx, emp_tasks in enumerate(tasks_per_emp):
            # Сортируем задачи по дню, затем по близости (TSP приближение)
            emp_tasks.sort(key=lambda x: (x[2], x[0].id))
            current_day = None
            # Используем смену по умолчанию (первую)
            shift = project.shifts[0]
            shift_start = _time_to_minutes(shift.start_time)
            shift_end = _time_to_minutes(shift.end_time)
            for room, t, day in emp_tasks:
                start_time = datetime.combine(project.start_date + timedelta(days=day), time())
                # Назначаем на начало смены (упрощённо)
                start_dt = start_time + timedelta(minutes=shift_start)
                end_dt = start_dt + timedelta(minutes=t)
                # Проверяем, что не вышли за смену, иначе переносим на следующий день
                if end_dt.hour*60 + end_dt.minute > shift_end:
                    start_dt += timedelta(days=1)
                    end_dt = start_dt + timedelta(minutes=t)
                tasks.append(CleaningTask(room.id, floor.index, start_dt, end_dt, emp_idx))
    return tasks