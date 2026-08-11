"""
scheduler — построение расписания уборки.

Основной режим: SINGLE_SHIFT — одна смена одного дня.

Алгоритм:
  1. Для каждого сотрудника собираются все задачи (комната × частота).
  2. Каждая задача получает "идеальное время" — равномерно распределённое
     по смене (без застоев и скоплений).
  3. Задачи сортируются по идеальному времени и планируются последовательно,
     заполняя смену без больших промежутков.
  4. Приоритетные комнаты (санузел, коридор, кухня, помеченные вручную)
     планируются в первую очередь.
"""
from datetime import datetime, timedelta, time, date
from typing import List, Tuple, Dict, Optional, Set, Any, NamedTuple
from project import Project, Room, CleaningTask, Shift, Zone
from sanitarnorm import (get_cleaning_time_minutes, get_frequency_per_day,
                         TRANSIT_TIME_MINUTES, WALKING_SPEED_M_PER_MIN,
                         MIN_CLEANING_TIME_MINUTES)
import math
from collections import defaultdict

MIN_GAP_BETWEEN_SAME_ROOM = 30
FLOOR_CHANGE_EXTRA_MINUTES = 5.0

CRITICAL_ROOM_TYPES = {"санузел", "коридор", "кухня"}


class CleaningJob(NamedTuple):
    room_id: int
    floor_idx: int
    duration_min: float
    freq_idx: int
    total_freq: int
    priority: bool
    employee: int
    critical: bool


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _room_center(room: Room):
    xs = [p[0] for p in room.points]
    ys = [p[1] for p in room.points]
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _transit_minutes(key1: Optional[Tuple[int, int]], key2: Optional[Tuple[int, int]],
                     dist_cache: Dict[Tuple[Tuple[int, int], Tuple[int, int]], float],
                     room_centers: Dict[Tuple[int, int], Tuple[float, float]]) -> int:
    if key1 is None or key2 is None:
        return int(TRANSIT_TIME_MINUTES)
    cache_key = (key1, key2)
    if cache_key not in dist_cache:
        c1 = room_centers.get(key1)
        c2 = room_centers.get(key2)
        if c1 is None or c2 is None:
            dist_cache[cache_key] = None
        else:
            d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
            dist_cache[cache_key] = d
    d = dist_cache[cache_key]
    if d is None:
        base = int(TRANSIT_TIME_MINUTES)
    else:
        base = int(math.ceil(d / WALKING_SPEED_M_PER_MIN))
        base = max(1, base)
    base = min(base, 5)  # ограничение
    floor_penalty = FLOOR_CHANGE_EXTRA_MINUTES if key1[0] != key2[0] else 0.0
    return base + int(math.ceil(floor_penalty))


def _get_shift_break_intervals(breaks: List[Tuple[str, str]]) -> List[Tuple[int, int]]:
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
    freq = get_frequency_per_day(room_type)
    return max(1, int(round(freq * weather)))


def _shift_to_next_free(candidate: int, duration: float, shift_start: int, shift_end: int,
                        break_intervals: List[Tuple[int, int]],
                        occupied: List[Tuple[int, int]]) -> Optional[int]:
    """
    Находит подходящее время для задачи.
    Сначала пробует разместить в candidate, затем сдвигает вперёд.
    Если candidate попадает в перерыв — пробует разместить ДО перерыва
    (в оставшееся окно), иначе после перерыва.
    """
    # Сначала проверяем, попадает ли candidate в перерыв
    for bs, be in sorted(break_intervals):
        if bs <= candidate < be:
            # Пробуем уместить ДО перерыва
            # Ищем последнее свободное окно перед bs
            before = bs - int(duration)
            if before >= shift_start:
                # Проверяем, что окно [before, bs) свободно
                slot_free = True
                for occ_start, occ_end in occupied:
                    if before < occ_end and bs > occ_start:
                        slot_free = False
                        break
                if slot_free:
                    return before
            # Не помещается до — после перерыва
            candidate = be
            break
        if candidate < bs and candidate + duration > bs:
            # Задача заходит на перерыв
            before = bs - int(duration)
            if before >= candidate:
                # Пробуем уместить прямо перед перерывом
                slot_free = True
                for occ_start, occ_end in occupied:
                    if before < occ_end and bs > occ_start:
                        slot_free = False
                        break
                if slot_free:
                    return before
            candidate = be
            break

    max_iter = 5000
    for _ in range(max_iter):
        if candidate + duration > shift_end:
            return None
        # Проверяем перерывы
        overlapped_break = False
        for bs, be in sorted(break_intervals):
            if bs <= candidate < be:
                candidate = be
                overlapped_break = True
                break
            if candidate < bs and candidate + duration > bs:
                candidate = be
                overlapped_break = True
                break
        if overlapped_break:
            continue
        # Проверяем занятые интервалы
        overlapped_occ = False
        for occ_start, occ_end in occupied:
            if candidate < occ_end and candidate + duration > occ_start:
                candidate = occ_end
                overlapped_occ = True
                break
        if overlapped_occ:
            continue
        # Свободно
        return candidate
    return None


def _build_jobs_for_employee(
    emp_idx: int,
    rooms_with_floor: List[Tuple[int, Room]],
    weather: float,
) -> List[CleaningJob]:
    jobs = []
    for floor_idx, room in rooms_with_floor:
        freq = _get_effective_freq(room.room_type, weather)
        duration = get_cleaning_time_minutes(room.room_type, room.area_m2)
        if duration <= 0:
            continue
        critical = room.priority or (room.room_type in CRITICAL_ROOM_TYPES)
        for i in range(freq):
            jobs.append(CleaningJob(
                room_id=room.id,
                floor_idx=floor_idx,
                duration_min=duration,
                freq_idx=i,
                total_freq=freq,
                priority=room.priority,
                employee=emp_idx,
                critical=critical,
            ))
    return jobs


def _compute_ideal_start(job: CleaningJob, shift_start: int, shift_end: int) -> int:
    """
    Вычисляет идеальное время начала задачи, равномерно распределённое по смене.

    Для комнаты с частотой N:
      - 1-я уборка: начало смены
      - 2-я уборка: середина смены
      - 3-я уборка: 1/3 и 2/3 смены
      - и т.д. — равномерно распределяем по смене
    """
    span = max(1, shift_end - shift_start)
    if job.total_freq == 1:
        # Единственная уборка — в начале смены
        return shift_start
    if job.total_freq == 2:
        # Две уборки: начало и середина
        if job.freq_idx == 0:
            return shift_start
        return shift_start + span // 2
    # Для 3+ уборок: равномерно распределяем по смене
    # freq_idx = 0, 1, 2, ... total_freq-1
    # Позиции: 0, 1/N, 2/N, ..., (N-1)/N
    fraction = job.freq_idx / max(1, job.total_freq)
    return shift_start + int(span * fraction)


def _plan_jobs_for_employee(
    jobs: List[CleaningJob],
    shift: Shift,
    breaks: List[Tuple[str, str]],
    current_date: date,
    room_map: Dict[Tuple[int, int], Room],
    room_centers: Dict[Tuple[int, int], Tuple[float, float]],
    dist_cache: Dict[Tuple[Tuple[int, int], Tuple[int, int]], float],
) -> Tuple[List[CleaningTask], List[Dict[str, Any]]]:
    if not jobs:
        return [], []

    tasks = []
    unscheduled = []

    shift_start = _time_to_minutes(shift.start_time)
    shift_end = _time_to_minutes(shift.end_time)
    break_intervals = _get_shift_break_intervals(breaks)

    occupied = []
    last_end_by_room = {}
    prev_key = None

    # Первые уборки (freq_idx == 0) распределяем равномерно по всей смене,
    # чтобы не было скопления в начале. Повторные уборки размещаем посередине.
    anchor_jobs = [j for j in jobs if j.freq_idx == 0]
    repeat_jobs = [j for j in jobs if j.freq_idx > 0]

    # Сортируем первые уборки и присваиваем равномерные идеальные времена
    n_anchors = len(anchor_jobs)
    ordered_with_ideal = []
    for i, job in enumerate(sorted(anchor_jobs, key=lambda j: (0 if j.critical else 1, j.room_id))):
        # Равномерно распределяем первую уборку по смене
        if n_anchors > 1:
            ideal = shift_start + (shift_end - shift_start) * i // n_anchors
        else:
            ideal = shift_start
        ordered_with_ideal.append((ideal, job))

    # Повторные уборки: размещаем после середины смены
    span = max(1, shift_end - shift_start)
    for job in repeat_jobs:
        ideal = _compute_ideal_start(job, shift_start, shift_end)
        ordered_with_ideal.append((ideal, job))

    # Сортируем по идеальному времени начала
    ordered = sorted(ordered_with_ideal, key=lambda x: x[0])

    for ideal_start, job in ordered:
        job_key = (job.floor_idx, job.room_id)
        room = room_map.get(job_key)
        room_name = room.name if room else f"Комната {job.room_id + 1}"

        # Для первой задачи нет перехода — начинаем ровно с начала смены
        if prev_key is None:
            transit = 0
        else:
            transit = _transit_minutes(prev_key, job_key, dist_cache, room_centers)
        last_end = last_end_by_room.get(job_key)

        # Заполняем смену непрерывно: начинаем следующую задачу сразу после
        # предыдущей (плюс переход). Идеальное время влияет на ПОРЯДОК задач,
        # но не создаёт застоев — задачи идут подряд по смене.
        if occupied:
            candidate = occupied[-1][1] + transit
        else:
            candidate = shift_start
        if last_end is not None:
            candidate = max(candidate, last_end + MIN_GAP_BETWEEN_SAME_ROOM)

        start_min = _shift_to_next_free(candidate, job.duration_min, shift_start, shift_end,
                                        break_intervals, occupied)

        if start_min is None:
            unscheduled.append({
                "room": [job.floor_idx, job.room_id],
                "room_name": room_name,
                "required_minutes": job.duration_min,
                "scheduled_minutes": 0.0,
                "status": "UNSCHEDULED",
                "reason": "Не хватает времени в смене",
                "cleaning_index": job.freq_idx + 1,
                "critical": job.critical,
            })
            continue

        end_min = start_min + int(job.duration_min)
        start_dt = datetime.combine(current_date, time()) + timedelta(minutes=start_min)
        end_dt = datetime.combine(current_date, time()) + timedelta(minutes=end_min)

        tasks.append(CleaningTask(job.room_id, job.floor_idx, start_dt, end_dt, job.employee))
        occupied.append((start_min, end_min))
        occupied.sort(key=lambda x: x[0])
        last_end_by_room[job_key] = end_min
        prev_key = job_key

    tasks.sort(key=lambda t: t.start_dt)
    return tasks, unscheduled


def _plan_for_employee_on_day(
    emp_idx: int,
    rooms_with_floor: List[Tuple[int, Room]],
    shift: Shift,
    breaks: List[Tuple[str, str]],
    weather: float,
    current_date: date,
    room_map: Dict[Tuple[int, int], Room],
    dist_cache: Dict[Tuple[Tuple[int, int], Tuple[int, int]], float],
    room_centers: Dict[Tuple[int, int], Tuple[float, float]],
) -> Tuple[List[CleaningTask], List[Dict[str, Any]]]:
    jobs = _build_jobs_for_employee(emp_idx, rooms_with_floor, weather)
    return _plan_jobs_for_employee(jobs, shift, breaks, current_date,
                                   room_map, room_centers, dist_cache)


def _build_room_map_global(project: Project) -> Dict[Tuple[int, int], Room]:
    return {(fi, room.id): room
            for fi, floor in enumerate(project.floors) for room in floor.rooms}


def _build_room_centers_global(project: Project) -> Dict[Tuple[int, int], Tuple[float, float]]:
    return {(fi, room.id): _room_center(room)
            for fi, floor in enumerate(project.floors) for room in floor.rooms}


def _build_zones_per_floor(project: Project, emp_count: int) -> List[Zone]:
    from zone_manager import manual_distribution
    zones = []
    zone_id = 0
    for fi, floor in enumerate(project.floors):
        floor_active = [r for r in floor.rooms if not r.disabled]
        if not floor_active:
            continue
        percents = [100.0 / emp_count] * emp_count
        floor_zones = manual_distribution(floor_active, percents)
        for z in floor_zones:
            z.id = zone_id
            zone_id += 1
            z.floor_index = fi
            zones.append(z)
    return zones


def _collect_unzoned_rooms_by_floor(project: Project) -> Dict[int, List[Room]]:
    zoned_by_floor = defaultdict(set)
    for zone in project.zones:
        zoned_by_floor[zone.floor_index].update(zone.room_ids)
    result = defaultdict(list)
    for fi, floor in enumerate(project.floors):
        for room in floor.rooms:
            if room.disabled:
                continue
            if room.id not in zoned_by_floor[fi]:
                result[fi].append(room)
    return result


def _get_shift_minutes(shift: Shift, breaks: List[Tuple[str, str]]) -> int:
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
    if target_date is None:
        target_date = project.start_date

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]

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

    print(f"[scheduler] режим: ONE_DAY")
    print(f"[scheduler] дата: {target_date.isoformat()}")
    print(f"[scheduler] комнаты: {len(active_rooms)}")
    print(f"[scheduler] сотрудников: {emp_count}")
    print(f"[scheduler] смена: {shift.start_time}-{shift.end_time}")
    if breaks:
        print(f"[scheduler] обед: {breaks[0][0]}-{breaks[0][1]}")

    # Строим зоны для заданного количества сотрудников
    if employees is not None and employees != project.employees_count:
        project.zones = _build_zones_per_floor(project, emp_count)
    elif not project.zones:
        project.zones = _build_zones_per_floor(project, emp_count)

    emp_rooms = defaultdict(list)
    for zone in project.zones:
        emp = zone.employee_index
        fi = zone.floor_index
        if fi < len(project.floors):
            for room in project.floors[fi].rooms:
                if room.id in zone.room_ids and not room.disabled:
                    emp_rooms[emp].append((fi, room))

    unzoned_by_floor = _collect_unzoned_rooms_by_floor(project)
    for fi, rooms_on_floor in unzoned_by_floor.items():
        for i, room in enumerate(rooms_on_floor):
            emp = i % emp_count
            emp_rooms[emp].append((fi, room))

    room_map = _build_room_map_global(project)
    room_centers = _build_room_centers_global(project)
    dist_cache = {}

    all_tasks = []
    all_unscheduled = []

    for emp, rooms_with_floor in emp_rooms.items():
        if not rooms_with_floor:
            continue
        emp_tasks, emp_unscheduled = _plan_for_employee_on_day(
            emp, rooms_with_floor, shift, breaks, weather,
            target_date, room_map, dist_cache, room_centers,
        )
        all_tasks.extend(emp_tasks)
        all_unscheduled.extend(emp_unscheduled)

    all_tasks.sort(key=lambda t: (t.employee, t.start_dt))

    scheduled_room_keys = {(t.floor_index, t.room_id) for t in all_tasks}
    scheduled_rooms = len(scheduled_room_keys)
    active_room_keys = set()
    for fi, floor in enumerate(project.floors):
        for room in floor.rooms:
            if not room.disabled:
                active_room_keys.add((fi, room.id))

    unscheduled_room_keys = active_room_keys - scheduled_room_keys

    required_cleanings = 0
    for room in active_rooms:
        required_cleanings += _get_effective_freq(room.room_type, weather)
    scheduled_cleanings = len(all_tasks)
    missed_cleanings = max(0, required_cleanings - scheduled_cleanings)

    cleaning_minutes = sum(
        (t.end_dt - t.start_dt).total_seconds() / 60.0 for t in all_tasks
    )

    transit_minutes = 0.0
    for emp in set(t.employee for t in all_tasks):
        emp_tasks = [t for t in all_tasks if t.employee == emp]
        emp_tasks.sort(key=lambda t: t.start_dt)
        for i in range(len(emp_tasks) - 1):
            gap = (emp_tasks[i + 1].start_dt - emp_tasks[i].end_dt).total_seconds() / 60.0
            if gap > 0:
                transit_minutes += min(gap, 10.0)

    total_workload_minutes = cleaning_minutes + transit_minutes

    shift_minutes = _get_shift_minutes(shift, breaks)
    available_minutes = emp_count * shift_minutes
    capacity_deficit = max(0.0, total_workload_minutes - available_minutes)

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

    employee_loads = {}
    for emp in sorted(set(t.employee for t in all_tasks)):
        emp_tasks = [t for t in all_tasks if t.employee == emp]
        emp_tasks.sort(key=lambda t: t.start_dt)
        emp_clean = sum((t.end_dt - t.start_dt).total_seconds() / 60.0 for t in emp_tasks)
        emp_transit = 0.0
        for i in range(len(emp_tasks) - 1):
            gap = (emp_tasks[i + 1].start_dt - emp_tasks[i].end_dt).total_seconds() / 60.0
            if gap > 0:
                emp_transit += min(gap, 10.0)
        idle = max(0.0, shift_minutes - emp_clean - emp_transit)
        util = (emp_clean + emp_transit) / shift_minutes * 100.0 if shift_minutes > 0 else 0.0
        employee_loads[emp] = {
            "cleaning_minutes": round(emp_clean, 1),
            "transit_minutes": round(emp_transit, 1),
            "idle_minutes": round(idle, 1),
            "total_work_minutes": round(emp_clean + emp_transit, 1),
            "available_minutes": shift_minutes,
            "utilization_percent": round(util, 1),
        }

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
        "idle_minutes": round(max(0.0, available_minutes - total_workload_minutes), 1),
        "total_workload_minutes": round(total_workload_minutes, 1),
        "total_workload_hours": round(total_workload_minutes / 60.0, 2),
        "available_minutes": available_minutes,
        "capacity_deficit": round(capacity_deficit, 1),
        "employee_loads": employee_loads,
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
    # MULTI_DAY режим (не используется в основном сценарии, но оставлен для совместимости)
    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]
    if not project.shifts:
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

    all_tasks = []
    for day_offset in range(num_days):
        current_date = start_date + timedelta(days=day_offset)
        result = schedule_single_shift(project, target_date=current_date, employees=employees, allow_partial_schedule=True)
        all_tasks.extend(result["tasks"])
    project.cleaning_tasks = all_tasks
    return all_tasks


def compute_recommended_employees(project: Project) -> int:
    from cost_calculator import estimate_required_employees
    rough = estimate_required_employees(project)
    return rough["employees"]