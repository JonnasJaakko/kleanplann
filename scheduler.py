"""
scheduler — построение расписания уборки.

Основной режим: SINGLE_SHIFT — одна смена одного дня.
Дополнительный режим: MULTI_DAY — планирование периода (отдельная функция).

Принципы:
  * использует существующие зоны (не перераспределяет);
  * уважает floor_index из зон;
  * каждая комната получает ровно столько уборок, сколько указано в частоте;
  * если не влезает — комната получает статус UNSCHEDULED с причиной;
  * не обрезает длительность больших комнат до длины смены;
  * не создаёт задачи через обед;
  * не создаёт параллельные задачи одному сотруднику;
  * не выбрасывает активные помещения молча.
"""
from datetime import datetime, timedelta, time, date
from typing import List, Tuple, Dict, Optional, Set, Any
from project import Project, Room, CleaningTask, Shift, Zone
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day, TRANSIT_TIME_MINUTES
import math
from collections import defaultdict

WALKING_SPEED_M_PER_MIN = 50.0
MIN_GAP_BETWEEN_SAME_ROOM = 30


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _room_center(room: Room):
    xs = [p[0] for p in room.points]
    ys = [p[1] for p in room.points]
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _transit_minutes(room1: Optional[Room], room2: Optional[Room],
                     dist_cache: Dict[int, float],
                     room_centers: Dict[int, Tuple[float, float]]) -> int:
    """Время перехода между комнатами (мин). Fallback: евклидово расстояние."""
    if room1 is None or room2 is None:
        return int(TRANSIT_TIME_MINUTES)
    key = (room1.id, room2.id)
    if key not in dist_cache:
        c1 = room_centers[room1.id]
        c2 = room_centers[room2.id]
        d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
        dist_cache[key] = d
    d = dist_cache[key]
    return max(int(TRANSIT_TIME_MINUTES), int(math.ceil(d / WALKING_SPEED_M_PER_MIN)))


def _get_shift_break_intervals(breaks: List[Tuple[str, str]]) -> List[Tuple[int, int]]:
    """Возвращает интервалы перерывов в минутах от начала суток."""
    result = []
    for b_start, b_end in breaks:
        try:
            s = _time_to_minutes(b_start)
            e = _time_to_minutes(b_end)
            if s < e:
                result.append((s, e))
        except (ValueError, AttributeError):
            pass
    return result


def _get_effective_freq(room_type: str, weather: float) -> int:
    """Частота уборки с учётом погоды. Единая формула для всех подсистем."""
    freq = get_frequency_per_day(room_type)
    return max(1, int(round(freq * weather)))


def _find_free_slot(
    ideal_start: int,
    duration: float,
    shift_start: int,
    shift_end: int,
    break_intervals: List[Tuple[int, int]],
    occupied: List[Tuple[int, int]],
    last_end: Optional[int],
    min_gap: int,
) -> Tuple[Optional[int], Optional[int]]:
    """Ищет свободный слот для задачи.

    Учитывает: ideal_start, duration, shift, перерывы, уже занятые интервалы,
    last_end той же комнаты, min_gap.

    Возвращает (start_min, end_min) или (None, None) если не влезает.
    """
    candidate = ideal_start

    # Учитываем last_end той же комнаты
    if last_end is not None:
        candidate = max(candidate, last_end + min_gap)

    # Проверяем, не попадает ли candidate на перерыв
    for bs, be in sorted(break_intervals):
        if bs <= candidate < be:
            candidate = be
        if candidate < bs and candidate + duration > bs:
            candidate = be

    # Проверяем, не пересекается ли с уже занятыми интервалами
    max_iter = 1000
    iter_count = 0
    while iter_count < max_iter:
        iter_count += 1
        overlapped = False
        for occ_start, occ_end in occupied:
            if candidate < occ_end and candidate + duration > occ_start:
                candidate = occ_end
                overlapped = True
                break
        if not overlapped:
            break
        for bs, be in sorted(break_intervals):
            if bs <= candidate < be:
                candidate = be
            if candidate < bs and candidate + duration > bs:
                candidate = be

    if candidate + duration > shift_end:
        # Пробуем от начала смены (для больших комнат)
        candidate = shift_start
        if last_end is not None:
            candidate = max(candidate, last_end + min_gap)
        for bs, be in sorted(break_intervals):
            if bs <= candidate < be:
                candidate = be
            if candidate < bs and candidate + duration > bs:
                candidate = be
        # Проверяем пересечения
        iter_count = 0
        while iter_count < max_iter:
            iter_count += 1
            overlapped = False
            for occ_start, occ_end in occupied:
                if candidate < occ_end and candidate + duration > occ_start:
                    candidate = occ_end
                    overlapped = True
                    break
            if not overlapped:
                break
            for bs, be in sorted(break_intervals):
                if bs <= candidate < be:
                    candidate = be
                if candidate < bs and candidate + duration > bs:
                    candidate = be
        if candidate + duration > shift_end:
            return None, None
        return candidate, int(candidate + duration)

    return candidate, int(candidate + duration)


def _plan_for_employee_on_day(
    emp_idx: int,
    rooms: List[Room],
    shift: Shift,
    breaks: List[Tuple[str, str]],
    weather: float,
    current_date: date,
    floor_idx: int,
    room_map: Dict[int, Room],
    dist_cache: Dict[int, float],
    room_centers: Dict[int, Tuple[float, float]],
) -> Tuple[List[CleaningTask], List[Dict[str, Any]]]:
    """Планирует уборку для одного сотрудника на ОДИН день.

    Возвращает (список задач, список unscheduled-записей).
    Каждая unscheduled-запись:
      {
        "room": [floor_idx, room_id],
        "room_name": str,
        "required_minutes": float,
        "scheduled_minutes": 0.0,
        "status": "UNSCHEDULED",
        "reason": "NO_AVAILABLE_TIME"
      }
    """
    if not rooms:
        return [], []

    unscheduled: List[Dict[str, Any]] = []
    tasks: List[CleaningTask] = []

    shift_start = _time_to_minutes(shift.start_time)
    shift_end = _time_to_minutes(shift.end_time)
    shift_len = shift_end - shift_start
    break_intervals = _get_shift_break_intervals(breaks)

    # Сортируем комнаты: приоритетные — первыми, затем по близости (TSP)
    priority_rooms = [r for r in rooms if r.priority]
    normal_rooms = [r for r in rooms if not r.priority]
    ordered = (_order_rooms_by_proximity(priority_rooms, room_centers, dist_cache)
               + _order_rooms_by_proximity(normal_rooms, room_centers, dist_cache))

    # Собираем все требуемые уборки
    all_cleanings: List[Tuple[int, float, int]] = []
    for room in ordered:
        freq = _get_effective_freq(room.room_type, weather)
        duration = get_cleaning_time_minutes(room.room_type, room.area_m2)
        if duration <= 0:
            continue
        for i in range(freq):
            all_cleanings.append((room.id, duration, i))

    total_cleanings = len(all_cleanings)
    if total_cleanings == 0:
        return [], []

    # Равномерно распределяем ideal_start по смене
    placements: List[Tuple[int, float, int, int]] = []
    max_freq = max((_get_effective_freq(r.room_type, weather) for r in ordered), default=1)
    idx = 0
    for i in range(max_freq):
        for room in ordered:
            freq = _get_effective_freq(room.room_type, weather)
            if i >= freq:
                continue
            duration = get_cleaning_time_minutes(room.room_type, room.area_m2)
            if duration <= 0:
                continue
            ideal_start = shift_start + int((idx + 0.5) * shift_len / total_cleanings)
            placements.append((room.id, duration, ideal_start, i))
            idx += 1

    placements.sort(key=lambda p: p[2])

    occupied: List[Tuple[int, int]] = []  # занятые интервалы (start, end)
    last_end_by_room: Dict[int, int] = {}
    scheduled_freq_by_room: Dict[int, int] = defaultdict(int)

    for room_id, duration, ideal_start, freq_idx in placements:
        room = room_map.get(room_id)
        room_name = room.name if room else f"Комната {room_id + 1}"

        last_end = last_end_by_room.get(room_id)
        min_gap = MIN_GAP_BETWEEN_SAME_ROOM if last_end is not None else 0

        start_min, end_min = _find_free_slot(
            ideal_start, duration, shift_start, shift_end,
            break_intervals, occupied, last_end, min_gap
        )

        if start_min is None:
            # Комната не влезает — регистрируем UNSCHEDULED
            unscheduled.append({
                "room": [floor_idx, room_id],
                "room_name": room_name,
                "required_minutes": duration,
                "scheduled_minutes": 0.0,
                "status": "UNSCHEDULED",
                "reason": "NO_AVAILABLE_TIME",
                "cleaning_index": freq_idx + 1,
            })
            continue

        start_dt = datetime.combine(current_date, time()) + timedelta(minutes=start_min)
        end_dt = start_dt + timedelta(minutes=duration)
        tasks.append(CleaningTask(room_id, floor_idx, start_dt, end_dt, emp_idx))
        occupied.append((start_min, end_min))
        occupied.sort(key=lambda x: x[0])
        last_end_by_room[room_id] = end_min
        scheduled_freq_by_room[room_id] += 1

    return tasks, unscheduled


def _order_rooms_by_proximity(rooms: List[Room],
                              room_centers: Dict[int, Tuple[float, float]],
                              dist_cache: Dict[int, float]) -> List[Room]:
    """Жадный TSP (ближайший сосед). Fallback для transit."""
    if not rooms:
        return []
    start = min(rooms, key=lambda r: (room_centers[r.id][0] + room_centers[r.id][1]))
    ordered = [start]
    remaining = [r for r in rooms if r is not start]
    while remaining:
        cur = ordered[-1]
        nxt = min(remaining, key=lambda r: _transit_minutes(cur, r, dist_cache, room_centers))
        ordered.append(nxt)
        remaining.remove(nxt)
    return ordered


def _build_room_map(project: Project) -> Dict[int, Room]:
    result = {}
    for floor in project.floors:
        for room in floor.rooms:
            result[room.id] = room
    return result


def _build_room_centers(project: Project) -> Dict[int, Tuple[float, float]]:
    result = {}
    for floor in project.floors:
        for room in floor.rooms:
            result[room.id] = _room_center(room)
    return result


def _get_shift_minutes(shift: Shift, breaks: List[Tuple[str, str]]) -> int:
    """Доступное время смены с вычетом обеда (минуты)."""
    try:
        start = _time_to_minutes(shift.start_time)
        end = _time_to_minutes(shift.end_time)
    except (ValueError, AttributeError):
        return 480
    shift_len = max(0, end - start)
    for b_start, b_end in breaks:
        try:
            bs = _time_to_minutes(b_start)
            be = _time_to_minutes(b_end)
            # Пересечение перерыва со сменой
            overlap = max(0, min(end, be) - max(start, bs))
            shift_len = max(0, shift_len - overlap)
        except (ValueError, AttributeError):
            pass
    return max(1, shift_len)


def schedule_single_shift(
    project: Project,
    target_date: Optional[date] = None,
    employees: Optional[int] = None,
    allow_partial_schedule: bool = False,
) -> Dict[str, Any]:
    """Планирует ОДНУ смену ОДНОГО дня.

    Основной режим работы scheduler.

    Args:
        project: проект
        target_date: дата смены (по умолчанию project.start_date)
        employees: количество сотрудников (по умолчанию project.employees_count)
        allow_partial_schedule: разрешить частичное расписание

    Returns:
        Словарь с полным результатом:
        {
            "date": "2026-08-05",
            "shift_start": "09:00",
            "shift_end": "18:00",
            "break_start": "12:00",
            "break_end": "13:00",
            "feasible": bool,
            "employees": int,
            "active_rooms": int,
            "scheduled_rooms": int,
            "unscheduled_rooms": int,
            "required_cleanings": int,
            "scheduled_cleanings": int,
            "missed_cleanings": int,
            "cleaning_minutes": float,
            "transit_minutes": float,
            "total_workload_minutes": float,
            "available_minutes": float,
            "capacity_deficit": float,
            "tasks": List[CleaningTask],
            "unscheduled_rooms_list": [...],
            "violations": {...},
        }
    """
    if target_date is None:
        target_date = project.start_date

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]
    room_map = _build_room_map(project)
    room_centers = _build_room_centers(project)
    dist_cache: Dict[int, float] = {}

    if not project.shifts:
        return {
            "date": target_date.isoformat(),
            "feasible": False,
            "error": "Нет настроек смены",
        }
    shift = project.shifts[0]
    breaks = project.breaks
    weather = project.weather_factor

    emp_count = max(1, employees if employees is not None else project.employees_count)

    # Собираем комнаты по зонам: (emp_idx, floor_idx) -> [Room]
    emp_floor_rooms: Dict[Tuple[int, int], List[Room]] = defaultdict(list)

    # Если employees задан явно и отличается от project.employees_count —
    # пересоздаём зоны для нового количества сотрудников (режим staffing).
    # Если employees не задан — используем существующие зоны.
    if employees is not None and employees != project.employees_count:
        from zone_manager import manual_distribution
        percents = [100.0 / emp_count] * emp_count
        project.zones = manual_distribution(active_rooms, percents)
        for zone in project.zones:
            for rid in zone.room_ids:
                for fi, floor in enumerate(project.floors):
                    for room in floor.rooms:
                        if room.id == rid:
                            zone.floor_index = fi
                            break
                else:
                    zone.floor_index = 0
    elif not project.zones:
        from zone_manager import manual_distribution
        percents = [100.0 / emp_count] * emp_count
        project.zones = manual_distribution(active_rooms, percents)
        for zone in project.zones:
            for rid in zone.room_ids:
                for fi, floor in enumerate(project.floors):
                    for room in floor.rooms:
                        if room.id == rid:
                            zone.floor_index = fi
                            break
                else:
                    zone.floor_index = 0

    for zone in project.zones:
        emp = zone.employee_index
        fi = zone.floor_index
        if fi < len(project.floors):
            for room in project.floors[fi].rooms:
                if room.id in zone.room_ids and not room.disabled:
                    emp_floor_rooms[(emp, fi)].append(room)

    # Комнаты без зон — распределяем поровну
    zoned_rooms: Set[int] = set()
    for zone in project.zones:
        zoned_rooms.update(zone.room_ids)
    unzoned = [r for r in active_rooms if r.id not in zoned_rooms]
    if unzoned:
        for i, room in enumerate(unzoned):
            emp = i % emp_count
            for fi, floor in enumerate(project.floors):
                if room in floor.rooms:
                    emp_floor_rooms[(emp, fi)].append(room)
                    break

    all_tasks: List[CleaningTask] = []
    all_unscheduled: List[Dict[str, Any]] = []

    for (emp, fi), rooms in emp_floor_rooms.items():
        if not rooms:
            continue
        emp_tasks, emp_unscheduled = _plan_for_employee_on_day(
            emp, rooms, shift, breaks, weather,
            target_date, fi, room_map, dist_cache, room_centers
        )
        all_tasks.extend(emp_tasks)
        all_unscheduled.extend(emp_unscheduled)

    # Считаем статистику
    scheduled_room_keys = {(t.floor_index, t.room_id) for t in all_tasks}
    scheduled_rooms = len(scheduled_room_keys)
    active_room_keys = set()
    for fi, floor in enumerate(project.floors):
        for room in floor.rooms:
            if not room.disabled:
                active_room_keys.add((fi, room.id))

    unscheduled_room_keys = active_room_keys - scheduled_room_keys

    # Требуемые уборки
    required_cleanings = 0
    for room in active_rooms:
        required_cleanings += _get_effective_freq(room.room_type, weather)
    scheduled_cleanings = len(all_tasks)
    missed_cleanings = max(0, required_cleanings - scheduled_cleanings)

    # Трудоёмкость
    cleaning_minutes = sum(
        (t.end_dt - t.start_dt).total_seconds() / 60.0 for t in all_tasks
    )

    # Transit: оцениваем как разницу между workload и cleaning
    # (в задачах transit не хранится отдельно, поэтому оцениваем)
    transit_minutes = 0.0
    for emp in set(t.employee for t in all_tasks):
        emp_tasks = sorted([t for t in all_tasks if t.employee == emp], key=lambda t: t.start_dt)
        for i in range(len(emp_tasks) - 1):
            gap = (emp_tasks[i + 1].start_dt - emp_tasks[i].end_dt).total_seconds() / 60.0
            if gap > 0:
                transit_minutes += min(gap, 10.0)  # не более 10 мин на переход

    total_workload_minutes = cleaning_minutes + transit_minutes

    # Доступное время
    shift_minutes = _get_shift_minutes(shift, breaks)
    available_minutes = emp_count * shift_minutes
    capacity_deficit = max(0.0, total_workload_minutes - available_minutes)

    # Валидация
    from schedule_validator import validate_schedule
    project.cleaning_tasks = all_tasks
    validation = validate_schedule(project)

    feasible = (
        len(unscheduled_room_keys) == 0
        and missed_cleanings == 0
        and validation["time_conflicts"] == 0
        and validation["break_violations"] == 0
        and validation["out_of_shift_tasks"] == 0
    )

    if not feasible and not allow_partial_schedule:
        feasible = False

    return {
        "date": target_date.isoformat(),
        "shift_start": shift.start_time,
        "shift_end": shift.end_time,
        "break_start": breaks[0][0] if breaks else None,
        "break_end": breaks[0][1] if breaks else None,
        "feasible": feasible,
        "employees": emp_count,
        "active_rooms": len(active_room_keys),
        "scheduled_rooms": scheduled_rooms,
        "unscheduled_rooms": len(unscheduled_room_keys),
        "required_cleanings": required_cleanings,
        "scheduled_cleanings": scheduled_cleanings,
        "missed_cleanings": missed_cleanings,
        "cleaning_minutes": round(cleaning_minutes, 1),
        "cleaning_hours": round(cleaning_minutes / 60.0, 2),
        "transit_minutes": round(transit_minutes, 1),
        "total_workload_minutes": round(total_workload_minutes, 1),
        "total_workload_hours": round(total_workload_minutes / 60.0, 2),
        "available_minutes": available_minutes,
        "capacity_deficit": round(capacity_deficit, 1),
        "tasks": all_tasks,
        "unscheduled_rooms_list": all_unscheduled,
        "violations": {
            "time_conflicts": validation["time_conflicts"],
            "break_violations": validation["break_violations"],
            "out_of_shift_tasks": validation["out_of_shift_tasks"],
            "frequency_violations": validation["frequency_violations"],
        },
        "validation": validation,
    }


def plan_cleaning_schedule(
    project: Project,
    fixed_employees: Optional[int] = None,
    max_days: Optional[int] = None,
) -> List[CleaningTask]:
    """MULTI_DAY режим: генерирует расписание на период (start_date — end_date).

    Отдельный режим, вызывается явно. По умолчанию используется schedule_single_shift.
    """
    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]
    room_map = _build_room_map(project)
    room_centers = _build_room_centers(project)
    dist_cache: Dict[int, float] = {}

    if not project.shifts:
        print("[scheduler] Нет настроек смены")
        return []
    shift = project.shifts[0]
    breaks = project.breaks
    weather = project.weather_factor

    start_date = project.start_date
    end_date = project.end_date
    if max_days is not None:
        end_date = min(end_date, start_date + timedelta(days=max_days - 1))
    num_days = (end_date - start_date).days + 1
    if num_days < 1:
        num_days = 1

    employees = max(1, fixed_employees if fixed_employees is not None else project.employees_count)

    emp_floor_rooms: Dict[Tuple[int, int], List[Room]] = defaultdict(list)

    if not project.zones:
        from zone_manager import manual_distribution
        percents = [100.0 / employees] * employees
        project.zones = manual_distribution(active_rooms, percents)
        for zone in project.zones:
            for rid in zone.room_ids:
                for fi, floor in enumerate(project.floors):
                    for room in floor.rooms:
                        if room.id == rid:
                            zone.floor_index = fi
                            break
                else:
                    zone.floor_index = 0

    for zone in project.zones:
        emp = zone.employee_index
        fi = zone.floor_index
        if fi < len(project.floors):
            for room in project.floors[fi].rooms:
                if room.id in zone.room_ids and not room.disabled:
                    emp_floor_rooms[(emp, fi)].append(room)

    zoned_rooms: Set[int] = set()
    for zone in project.zones:
        zoned_rooms.update(zone.room_ids)
    unzoned = [r for r in active_rooms if r.id not in zoned_rooms]
    if unzoned:
        for i, room in enumerate(unzoned):
            emp = i % employees
            for fi, floor in enumerate(project.floors):
                if room in floor.rooms:
                    emp_floor_rooms[(emp, fi)].append(room)
                    break

    all_tasks: List[CleaningTask] = []
    all_warnings: List[str] = []

    for day_offset in range(num_days):
        current_date = start_date + timedelta(days=day_offset)

        for (emp, fi), rooms in emp_floor_rooms.items():
            if not rooms:
                continue
            emp_tasks, emp_unscheduled = _plan_for_employee_on_day(
                emp, rooms, shift, breaks, weather,
                current_date, fi, room_map, dist_cache, room_centers
            )
            all_tasks.extend(emp_tasks)
            for u in emp_unscheduled:
                all_warnings.append(
                    f"Комната {u['room_name']} (№{u['room'][1] + 1}), "
                    f"уборка #{u['cleaning_index']}: {u['reason']}"
                )

    total_load = sum(
        get_cleaning_time_minutes(r.room_type, r.area_m2) * _get_effective_freq(r.room_type, weather)
        for r in active_rooms
    )
    print(f"[scheduler] начало: {len(active_rooms)} комнат, {employees} сотрудников, "
          f"смена {shift.start_time}-{shift.end_time}, "
          f"суммарная нагрузка {total_load:.0f} мин ≈ {total_load / 60:.0f} ч, "
          f"период: {num_days} дн.")

    if all_warnings:
        print(f"[scheduler] предупреждения ({len(all_warnings)}):")
        for w in all_warnings[:10]:
            print(f"  ⚠ {w}")
        if len(all_warnings) > 10:
            print(f"  ... и ещё {len(all_warnings) - 10}")

    project.cleaning_tasks = all_tasks

    covered = len({t.room_id for t in all_tasks})
    print(f"[scheduler] готово: {len(all_tasks)} задач, {covered}/{len(active_rooms)} комнат, "
          f"{num_days} дн.")
    return all_tasks


def compute_recommended_employees(project: Project) -> int:
    """Вычисляет минимальное количество сотрудников (грубая оценка)."""
    all_rooms = project.all_rooms()
    active = [r for r in all_rooms if not r.disabled]
    if not active or not project.shifts:
        return 1
    from cost_calculator import estimate_required_employees
    rough = estimate_required_employees(project)
    return rough["employees"]