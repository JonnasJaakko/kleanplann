"""
Планирование расписания уборки с оптимизацией маршрута (ближайший сосед)
и учётом времени на перемещение между комнатами.
"""
from datetime import datetime, date, timedelta, time
from typing import List, Tuple
import math
from project import Project, Room, CleaningTask, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day

# Средняя скорость перемещения уборщика (м/мин)
WALKING_SPEED_M_PER_MIN = 50.0

def _distance_m(room1: Room, room2: Room) -> float:
    """Евклидово расстояние между центрами комнат."""
    cx1 = sum(p[0] for p in room1.points) / len(room1.points)
    cy1 = sum(p[1] for p in room1.points) / len(room1.points)
    cx2 = sum(p[0] for p in room2.points) / len(room2.points)
    cy2 = sum(p[1] for p in room2.points) / len(room2.points)
    return math.hypot(cx2 - cx1, cy2 - cy1)

def _travel_time_min(room1: Room, room2: Room) -> float:
    """Время перемещения между комнатами в минутах."""
    return _distance_m(room1, room2) / WALKING_SPEED_M_PER_MIN

def _nearest_neighbor_route(rooms: List[Room], start_point: Tuple[float, float] = (0,0)) -> List[Room]:
    """
    Возвращает список комнат, упорядоченный по эвристике ближайшего соседа,
    начиная с комнаты, ближайшей к start_point.
    """
    if not rooms:
        return []
    remaining = rooms.copy()
    # Начинаем с ближайшей к start_point
    current = min(remaining, key=lambda r: _distance_m(r, Room(-1, [start_point])))
    route = [current]
    remaining.remove(current)
    while remaining:
        last = route[-1]
        next_room = min(remaining, key=lambda r: _distance_m(last, r))
        route.append(next_room)
        remaining.remove(next_room)
    return route

def plan_cleaning_schedule(project: Project) -> List[CleaningTask]:
    """
    Генерирует оптимизированное расписание уборки.
    Для каждого сотрудника строится маршрут по зонам ответственности.
    """
    tasks = []
    # Собираем все комнаты всех этажей
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

    # Для каждого сотрудника
    for emp_idx in range(project.employees_count):
        # Находим зоны, назначенные этому сотруднику
        emp_zones = [z for z in project.zones if z.employee_index == emp_idx]
        if not emp_zones:
            continue

        # Собираем комнаты сотрудника
        emp_rooms = []
        for zone in emp_zones:
            for rid in zone.room_ids:
                room = next((r for r in all_rooms if r.id == rid), None)
                if room:
                    emp_rooms.append(room)

        if not emp_rooms:
            continue

        # Оптимизируем маршрут (ближайший сосед)
        # Стартовая точка – центр здания (0,0) или первый этаж
        route = _nearest_neighbor_route(emp_rooms)

        # Планируем на каждый день
        current_day = project.start_date
        for day_offset in range(total_days):
            day = project.start_date + timedelta(days=day_offset)
            # Используем первую смену (можно будет расширить)
            shift_start, shift_end = shift_intervals[0]
            current_time = shift_start  # в минутах от начала дня

            for room in route:
                freq = get_frequency_per_day(room.room_type) * weather
                # Округляем частоту – сколько раз в день нужно убирать
                times_per_day = max(1, round(freq))
                for _ in range(times_per_day):
                    # Время уборки
                    clean_time = get_cleaning_time_minutes(room.room_type, room.area_m2)
                    # Добавляем время на перемещение от предыдущей комнаты
                    if tasks:
                        last_room = all_rooms[tasks[-1].room_id] if tasks[-1].room_id < len(all_rooms) else None
                        if last_room:
                            travel = _travel_time_min(last_room, room)
                            current_time += travel

                    # Проверяем, не выходим ли за пределы смены
                    if current_time + clean_time > shift_end:
                        # Переносим на следующий день (упрощённо)
                        current_time = shift_start
                        day += timedelta(days=1)

                    start_dt = datetime.combine(day, time(hour=0, minute=0)) + timedelta(minutes=current_time)
                    end_dt = start_dt + timedelta(minutes=clean_time)
                    tasks.append(CleaningTask(room.id, 0, start_dt, end_dt, emp_idx))
                    current_time += clean_time

    # Сортируем задачи по времени для красивого отображения
    tasks.sort(key=lambda t: (t.employee, t.start_dt))
    return tasks