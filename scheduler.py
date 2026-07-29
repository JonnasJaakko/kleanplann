"""
Планирование расписания уборки с оптимизацией маршрута и исключением дублирования.
"""
from datetime import datetime, date, timedelta, time
from typing import List, Tuple
import math
from project import Project, Room, CleaningTask
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day

# Средняя скорость перемещения уборщика (м/мин)
WALKING_SPEED_M_PER_MIN = 50.0
# Минимальный интервал между уборками одной комнаты (часы)
MIN_INTERVAL_HOURS = 2

def _distance_m(room1: Room, room2: Room) -> float:
    """Евклидово расстояние между центрами комнат."""
    if len(room1.points) == 0 or len(room2.points) == 0:
        return 0.0
    cx1 = sum(p[0] for p in room1.points) / len(room1.points)
    cy1 = sum(p[1] for p in room1.points) / len(room1.points)
    cx2 = sum(p[0] for p in room2.points) / len(room2.points)
    cy2 = sum(p[1] for p in room2.points) / len(room2.points)
    return math.hypot(cx2 - cx1, cy2 - cy1)

def _travel_time_min(room1: Room, room2: Room) -> float:
    """Время перемещения между комнатами в минутах."""
    return _distance_m(room1, room2) / WALKING_SPEED_M_PER_MIN

def _nearest_neighbor_route(rooms: List[Room]) -> List[Room]:
    """Сортирует комнаты по эвристике ближайшего соседа."""
    if not rooms:
        return []
    remaining = rooms.copy()
    # Начинаем с первой комнаты (можно улучшить, выбрав ближайшую к входу)
    route = [remaining.pop(0)]
    while remaining:
        last = route[-1]
        next_room = min(remaining, key=lambda r: _distance_m(last, r))
        route.append(next_room)
        remaining.remove(next_room)
    return route

def plan_cleaning_schedule(project: Project) -> List[CleaningTask]:
    tasks = []
    all_rooms = project.all_rooms()
    if not all_rooms:
        return tasks

    # Определяем рабочие интервалы смен
    shift_intervals = []
    for shift in project.shifts:
        start_min = int(shift.start_time.split(':')[0]) * 60 + int(shift.start_time.split(':')[1])
        end_min = int(shift.end_time.split(':')[0]) * 60 + int(shift.end_time.split(':')[1])
        shift_intervals.append((start_min, end_min))

    total_days = (project.end_date - project.start_date).days + 1
    weather = project.weather_factor

    for emp_idx in range(project.employees_count):
        emp_zones = [z for z in project.zones if z.employee_index == emp_idx]
        if not emp_zones:
            continue

        # Собираем комнаты сотрудника
        emp_rooms = []
        for zone in emp_zones:
            for rid in zone.room_ids:
                room = next((r for r in all_rooms if r.id == rid), None)
                if room and room not in emp_rooms:
                    emp_rooms.append(room)

        if not emp_rooms:
            continue

        # Оптимизируем маршрут
        route = _nearest_neighbor_route(emp_rooms)

        # Планируем уборку на каждый день
        for day_offset in range(total_days):
            day = project.start_date + timedelta(days=day_offset)
            shift_start, shift_end = shift_intervals[0]
            current_time = shift_start

            # Словарь для отслеживания времени последней уборки каждой комнаты
            last_cleaned = {room.id: datetime.min for room in emp_rooms}

            for room in route:
                freq_per_day = get_frequency_per_day(room.room_type) * weather
                # Округляем до ближайшего целого
                times_today = max(1, round(freq_per_day))

                for _ in range(times_today):
                    clean_time = get_cleaning_time_minutes(room.room_type, room.area_m2)
                    # Добавляем время на перемещение от предыдущей комнаты (если была)
                    if tasks and tasks[-1].employee == emp_idx:
                        last_room_id = tasks[-1].room_id
                        last_room = next((r for r in all_rooms if r.id == last_room_id), None)
                        if last_room:
                            travel = _travel_time_min(last_room, room)
                            current_time += travel

                    # Проверяем, не выходим за пределы смены
                    if current_time + clean_time > shift_end:
                        break  # переносим на следующий день (упрощённо)

                    # Проверяем минимальный интервал с последней уборкой этой комнаты
                    last_time = last_cleaned[room.id]
                    if last_time != datetime.min:
                        hours_since = (datetime.combine(day, time(0,0)) - last_time).total_seconds() / 3600
                        if hours_since < MIN_INTERVAL_HOURS:
                            continue  # пропускаем эту задачу, интервал ещё не прошёл

                    start_dt = datetime.combine(day, time(hour=shift_start//60, minute=shift_start%60)) + timedelta(minutes=current_time - shift_start)
                    end_dt = start_dt + timedelta(minutes=clean_time)
                    tasks.append(CleaningTask(room.id, 0, start_dt, end_dt, emp_idx))
                    last_cleaned[room.id] = start_dt
                    current_time += clean_time

    # Сортируем задачи по времени для красивого отображения
    tasks.sort(key=lambda t: (t.employee, t.start_dt))
    return tasks