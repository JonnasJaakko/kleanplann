"""Адаптивный однодневный планировщик KleanPlann.

Ключевые свойства production-версии:
- расписание строится только на одну выбранную дату;
- каждый норматив формируется из единой sanitarnorm-модели;
- transit учитывается до следующей уборки;
- поиск слотов конечный и гарантированно продвигается;
- пользовательские назначения сотрудника являются жёстким ограничением;
- пользовательские предпочтения времени учитываются как release-time, а
  зафиксированные времена являются неизменяемыми слотами;
- режим priority_mode == "time" балансирует фактическую рабочую нагрузку и
  старается выровнять время окончания смен сотрудников.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional
import math

import sanitarnorm
from project import CleaningTask

COOLDOWN_BY_TYPE = {"санузел": 0, "кухня": 0, "коридор": 0, "default": 0}
TRANSIT_SAME_FLOOR = 1
TRANSIT_TO_OTHER_FLOOR = 5
TRANSIT_TOILET = 3
LOOKAHEAD_MINUTES = 20
DISTANCE_WEIGHT = 0.05
LOAD_WEIGHT = 1.0
PRIORITY_BONUS = 35.0


class CleaningJob:
    def __init__(self, room_id, floor_index, duration, occurrence, frequency,
                 priority=False, employee=0, is_overtime=False):
        self.room_id = room_id
        self.floor_index = floor_index
        self.duration = duration
        self.occurrence = occurrence
        self.frequency = frequency
        self.priority = priority
        self.employee = employee
        self.is_overtime = is_overtime


def _job_key(floor_index: int, room_id: int, occurrence: int) -> str:
    return f"{int(floor_index)}:{int(room_id)}:{int(occurrence)}"


def _normalize_date(value, fallback=None):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return fallback or date.today()


def _to_minutes(value: str) -> int:
    h, m = map(int, str(value).split(":"))
    return h * 60 + m


def _time_to_datetime(base_date: date, minute_from_midnight: int) -> datetime:
    return datetime.combine(base_date, datetime.min.time()) + timedelta(minutes=int(minute_from_midnight))


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _parse_optional_minutes(value):
    if value in (None, ""):
        return None
    try:
        return _to_minutes(str(value))
    except Exception:
        return None


def _lock_for_job(project, fi: int, rid: int, occurrence: int):
    locks = getattr(project, "schedule_locks", {}) or {}
    value = locks.get(_job_key(fi, rid, occurrence))
    return dict(value) if isinstance(value, dict) else None


def _compute_ideal_start(job, shift_start, shift_end):
    span = max(0, int(shift_end) - int(shift_start))
    freq = max(1, int(getattr(job, "frequency", 1)))
    occurrence = max(0, int(getattr(job, "occurrence", 0)))
    return int(shift_start + span * occurrence / freq)


def _get_effective_freq(room_type: str, weather_factor: float = 1.0) -> int:
    return sanitarnorm.get_effective_frequency(room_type, weather_factor)


def _type_key(room_type: str) -> str:
    return sanitarnorm.normalize_room_type(room_type)


def _get_cooldown(room_type: str) -> int:
    return COOLDOWN_BY_TYPE.get(_type_key(room_type), 0)


def _get_transit_minutes(prev_room_type: str, prev_floor: int, next_floor: int) -> int:
    if prev_room_type and _type_key(prev_room_type) == "санузел":
        return TRANSIT_TOILET
    if prev_floor != next_floor:
        return TRANSIT_TO_OTHER_FLOOR
    return TRANSIT_SAME_FLOOR


def _regular_windows(project) -> Tuple[int, int, List[Tuple[int, int]]]:
    if not getattr(project, "shifts", None):
        start, end = 9 * 60, 19 * 60
    else:
        shift = project.shifts[0]
        start, end = _to_minutes(shift.start_time), _to_minutes(shift.end_time)
    if end <= start:
        raise ValueError("Конец смены должен быть позже начала смены")

    windows = [(start, end)]
    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs, be = _to_minutes(b_start), _to_minutes(b_end)
        except Exception:
            continue
        if be <= bs:
            continue
        new = []
        for ws, we in windows:
            if be <= ws or bs >= we:
                new.append((ws, we))
                continue
            if ws < bs:
                new.append((ws, min(bs, we)))
            if be < we:
                new.append((max(be, ws), we))
        windows = [(a, b) for a, b in new if b > a]
    return start, end, windows


def _break_intervals(project, shift_start, shift_end):
    out = []
    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs, be = _to_minutes(b_start), _to_minutes(b_end)
        except Exception:
            continue
        bs, be = max(shift_start, bs), min(shift_end, be)
        if be > bs:
            out.append((bs, be))
    return out


def _working_minute_to_clock(windows, offset_minutes: int) -> int:
    remaining = max(0, int(offset_minutes))
    for ws, we in windows:
        length = we - ws
        if remaining <= length:
            return ws + remaining
        remaining -= length
    return windows[-1][1] if windows else 0


def _build_release_times(frequency: int, windows: List[Tuple[int, int]]) -> List[int]:
    if not windows:
        return []
    frequency = max(1, int(frequency))
    total = sum(b - a for a, b in windows)
    if frequency == 1:
        return [windows[0][0]]
    return [_working_minute_to_clock(windows, round(total * i / frequency)) for i in range(frequency)]


def _zone_map(project) -> Dict[Tuple[int, int], int]:
    result: Dict[Tuple[int, int], int] = {}
    for zone in getattr(project, "zones", []) or []:
        try:
            fi = int(getattr(zone, "floor_index", 0))
            emp = int(getattr(zone, "employee_index", 0))
        except (TypeError, ValueError):
            continue
        for rid in getattr(zone, "room_ids", []) or []:
            try:
                result[(fi, int(rid))] = emp
            except (TypeError, ValueError):
                continue
    for key, emp in (getattr(project, "manual_assignments", {}) or {}).items():
        try:
            fi, rid = map(int, str(key).split(":", 1))
            result[(fi, rid)] = int(emp)
        except Exception:
            continue
    return result


def _room_map(project):
    return {(fi, room.id): room for fi, floor in enumerate(project.floors) for room in floor.rooms}


def _safe_employee_count(project, employees: Optional[int]) -> int:
    requested = employees if employees is not None else getattr(project, "employees_count", 1)
    return max(1, int(requested))


def _room_center(room):
    pts = getattr(room, "points", None) or []
    xy = [(float(p[0]), float(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not xy:
        return (0.0, 0.0)
    return (sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy))


def _distance(room_a, room_b):
    if room_a is None or room_b is None:
        return 0.0
    a, b = _room_center(room_a), _room_center(room_b)
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _room_for_task(project, task):
    try:
        floor = project.floors[int(task.floor_index)]
    except (IndexError, TypeError, ValueError):
        return None
    return next((room for room in floor.rooms if room.id == int(task.room_id)), None)


_get_room_for_task = _room_for_task


def _required_transit_between(project, previous, current_room, current_floor):
    if previous is None:
        return 0
    prev_room = _room_for_task(project, previous)
    return _get_transit_minutes(
        getattr(prev_room, "room_type", "") if prev_room else "",
        int(previous.floor_index),
        int(current_floor),
    )


def _job_duration(project, room) -> int:
    return max(1, int(math.ceil(sanitarnorm.get_cleaning_time_minutes(
        room.room_type,
        room.area_m2,
        getattr(project, "weather_factor", 1.0),
        getattr(project, "cleaning_type", "поддерживающая"),
    ))))


def _build_jobs(project, windows):
    zone_map = _zone_map(project)
    jobs = []
    sequence = 0
    weather = getattr(project, "weather_factor", 1.0)
    for fi, floor in enumerate(project.floors):
        for room in floor.rooms:
            if getattr(room, "disabled", False):
                continue
            frequency = _get_effective_freq(room.room_type, weather)
            releases = _build_release_times(frequency, windows)
            duration = _job_duration(project, room)
            for occurrence, release in enumerate(releases):
                lock = _lock_for_job(project, fi, room.id, occurrence)
                employee_override = None
                preferred_start = None
                fixed_start = None
                fixed = False
                if lock:
                    try:
                        employee_override = int(lock.get("employee"))
                    except (TypeError, ValueError):
                        employee_override = None
                    preferred_start = _parse_optional_minutes(lock.get("start"))
                    fixed = bool(lock.get("fixed", False))
                    if fixed:
                        fixed_start = preferred_start
                    elif preferred_start is not None:
                        release = max(release, preferred_start)

                jobs.append({
                    "floor_index": fi,
                    "room_id": room.id,
                    "room": room,
                    "duration": duration,
                    "frequency": frequency,
                    "occurrence": occurrence,
                    "release": release,
                    "original_release": release,
                    "priority": bool(getattr(room, "priority", False)),
                    "preferred_employee": employee_override if employee_override is not None else zone_map.get((fi, room.id)),
                    "fixed": fixed,
                    "fixed_start": fixed_start,
                    "preferred_start": preferred_start,
                    "sequence": sequence,
                })
                sequence += 1
    jobs.sort(key=lambda j: (j["fixed"] is False, j["fixed_start"] if j["fixed"] is not None else j["release"], not j["priority"], -j["duration"], j["sequence"]))
    return jobs


def _next_feasible_start(current, duration, release, windows, deadline=None):
    candidate = max(int(current), int(release))
    for ws, we in windows:
        if candidate < ws:
            candidate = ws
        if candidate >= we:
            continue
        if candidate + duration <= we:
            if deadline is not None and candidate + duration > deadline:
                return None
            return candidate
        candidate = we
    return None


def _fits_in_windows(start, duration, windows):
    for ws, we in windows:
        if ws <= start and start + duration <= we:
            return True
    return False


def _overtime_start(current, duration, release, shift_end, overtime_limit):
    start = max(int(current), int(release), int(shift_end))
    if start + duration <= overtime_limit:
        return start
    return None


def _choose_next_job(project, remaining, current_minute, previous_task, next_fixed=None, windows=None):
    if not remaining:
        return None
    windows = windows or _regular_windows(project)[2]
    next_fixed_start = next_fixed.get("fixed_start") if next_fixed else None
    prev_room = _room_for_task(project, previous_task) if previous_task else None
    candidates = []

    for job in remaining:
        transit = _required_transit_between(project, previous_task, job["room"], job["floor_index"])
        start = _next_feasible_start(
            current_minute + transit,
            job["duration"],
            job["release"],
            windows,
            deadline=(next_fixed_start if next_fixed else None),
        )
        if start is None:
            continue
        if next_fixed:
            transit_to_fixed = _get_transit_minutes(
                job["room"].room_type,
                int(job["floor_index"]),
                int(next_fixed["floor_index"]),
            )
            if start + job["duration"] + transit_to_fixed > int(next_fixed_start):
                continue
        distance = _distance(prev_room, job["room"]) if prev_room else 0.0
        candidates.append({
            "job": job,
            "start": int(start),
            "distance": float(distance),
            "priority": bool(job["priority"]),
        })

    if not candidates:
        return None

    earliest = min(c["start"] for c in candidates)
    mode = getattr(project, "priority_mode", "balanced")
    # «Близость» не имеет права создать большой простой ради геометрии. Берём
    # только кандидатов, которые начинаются максимум на 10 минут позже самого
    # раннего доступного, а внутри этого окна предпочитаем близкие помещения.
    proximity_window = 10 if mode == "proximity" else 0
    pool = [c for c in candidates if c["start"] <= earliest + proximity_window]
    if mode == "proximity":
        pool.sort(key=lambda c: (-int(c["priority"]), c["distance"], c["start"], c["job"]["duration"], c["job"]["sequence"]))
    elif mode == "time":
        pool.sort(key=lambda c: (c["start"], c["distance"], -int(c["priority"]), c["job"]["duration"], c["job"]["sequence"]))
    else:
        pool.sort(key=lambda c: (c["start"], -int(c["priority"]), c["distance"], c["job"]["duration"], c["job"]["sequence"]))
    return pool[0]["job"]


def _rough_assignment(jobs, employees, project):
    """Назначает работы сотрудникам с учётом зон и жёстких ручных назначений.

    В режиме «Время» свободные задачи всегда получают сотрудника с минимальной
    фактической накопленной трудоёмкостью. Для остальных режимов сохраняется
    логика близости/зон из рабочей версии.
    """
    assignment = {i: [] for i in range(employees)}
    loads = {i: 0.0 for i in range(employees)}
    last_rooms = {i: None for i in range(employees)}
    mode = getattr(project, "priority_mode", "balanced")

    for job in jobs:
        preferred = job.get("preferred_employee")
        if preferred is not None and 0 <= int(preferred) < employees:
            emp = int(preferred)
        elif mode == "time":
            emp = min(range(employees), key=lambda i: (loads[i], i))
        else:
            def cost(emp_idx):
                dist = _distance(last_rooms[emp_idx], job["room"]) if last_rooms[emp_idx] else 0.0
                return loads[emp_idx] * LOAD_WEIGHT + dist * DISTANCE_WEIGHT
            emp = min(range(employees), key=cost)
        assignment[emp].append(job)
        loads[emp] += job["duration"]
        last_rooms[emp] = job["room"]
    return assignment


def _insert_sorted_task(tasks, task):
    tasks.append(task)
    tasks.sort(key=lambda x: x.start_dt)


def _schedule_employee_jobs(project, jobs, target_date, windows, shift_end, overtime_limit):
    """Конечный сегментный планировщик.

    Зафиксированные задачи образуют неизменяемые точки. Между ними свободные
    задачи заполняются с максимально раннего допустимого момента. Перед каждой
    задачей транзит учитывается как часть временного интервала.
    """
    fixed_jobs = sorted([j for j in jobs if j.get("fixed")], key=lambda j: (j["fixed_start"], j["sequence"]))
    free_jobs = [j for j in jobs if not j.get("fixed")]
    tasks: List[CleaningTask] = []
    unscheduled = []
    locked_conflicts = []
    current = windows[0][0] if windows else shift_end
    previous = None

    # Сначала проверяем жёсткие точки: они должны быть внутри рабочей области и
    # не должны пересекаться между собой.
    prev_fixed_end = None
    for job in fixed_jobs:
        start = int(job["fixed_start"]) if job["fixed_start"] is not None else None
        if start is None or not _fits_in_windows(start, job["duration"], windows):
            locked_conflicts.append({"job": job, "reason": "Зафиксированное время вне рабочего окна"})
            continue
        end = start + job["duration"]
        if prev_fixed_end is not None and start < prev_fixed_end:
            locked_conflicts.append({"job": job, "reason": "Зафиксированные уборки пересекаются"})
            continue
        prev_fixed_end = end

    def make_task(job, start, is_overtime=False, transit_before=0):
        end = int(start + job["duration"])
        task = CleaningTask(
            room_id=job["room_id"],
            floor_index=job["floor_index"],
            start_dt=_time_to_datetime(target_date, start),
            end_dt=_time_to_datetime(target_date, end),
            employee=0,
            is_overtime=is_overtime,
            transit_after_minutes=0,
            priority=job["priority"],
            occurrence=int(job["occurrence"]),
            fixed=bool(job.get("fixed", False)),
            user_preferred_start=job.get("preferred_start"),
            release_minute=int(job.get("release", 0)),
        )
        task.transit_before_minutes = int(max(0, transit_before))
        task.transit_after_minutes = 0
        return task

    def place_free_until(deadline, next_fixed):
        nonlocal current, previous, free_jobs
        while free_jobs:
            job = _choose_next_job(project, free_jobs, current, previous, next_fixed=next_fixed, windows=windows)
            if job is None:
                return
            transit = _required_transit_between(project, previous, job["room"], job["floor_index"])
            start = _next_feasible_start(current + transit, job["duration"], job["release"], windows,
                                         deadline=(deadline if deadline is not None else None))
            if start is None:
                return
            if deadline is not None:
                transit_to_fixed = _get_transit_minutes(job["room"].room_type, int(job["floor_index"]), int(next_fixed["floor_index"]))
                if start + job["duration"] + transit_to_fixed > deadline:
                    return
            task = make_task(job, start, is_overtime=False, transit_before=transit)
            tasks.append(task)
            current = start + job["duration"]
            previous = task
            free_jobs.remove(job)

    # Проходим по фиксированным точкам и заполняем промежутки между ними.
    for fixed_index, fixed_job in enumerate(fixed_jobs):
        fixed_start = int(fixed_job["fixed_start"])
        place_free_until(fixed_start, fixed_job)
        transit_to_fixed = _required_transit_between(project, previous, fixed_job["room"], fixed_job["floor_index"])
        if previous is not None and current + transit_to_fixed > fixed_start:
            locked_conflicts.append({"job": fixed_job, "reason": "До зафиксированной уборки не помещается необходимый переход"})
            # Фиксированная точка не двигается; оставляем её в расписании только
            # если она не конфликтует с уже поставленной задачей.
            if previous.end_dt <= _time_to_datetime(target_date, fixed_start):
                locked_conflicts.append({"job": fixed_job, "reason": "Жёсткое время нарушает физическую доступность"})
            continue
        task = make_task(fixed_job, fixed_start, False, transit_to_fixed)
        # Не допускаем перекрытия с уже поставленными задачами.
        if any(not (task.end_dt <= t.start_dt or task.start_dt >= t.end_dt) for t in tasks):
            locked_conflicts.append({"job": fixed_job, "reason": "Пересечение с другим заданием сотрудника"})
            continue
        tasks.append(task)
        tasks.sort(key=lambda t: t.start_dt)
        current = fixed_start + fixed_job["duration"]
        previous = task

    # После последней фиксированной точки заполняем остаток смены.
    place_free_until(None, None)

    # Если свободная работа не влезла в основную смену, пробуем только явную
    # сверхурочную область. Это конечная попытка — без циклического поиска.
    if free_jobs:
        # Сначала планируем по исходному порядку, чтобы не нарушать приоритеты.
        overtime_current = max(current, shift_end)
        for job in list(free_jobs):
            transit = _required_transit_between(project, previous, job["room"], job["floor_index"])
            start = _overtime_start(overtime_current + transit, job["duration"], job["release"], shift_end, overtime_limit)
            if start is None:
                unscheduled.append(job)
                free_jobs.remove(job)
                continue
            task = make_task(job, start, True, transit)
            tasks.append(task)
            tasks.sort(key=lambda t: t.start_dt)
            overtime_current = start + job["duration"]
            previous = task
            free_jobs.remove(job)

    # Все оставшиеся job — действительно неразмещённые.
    unscheduled.extend(free_jobs)
    unscheduled.extend(item["job"] for item in locked_conflicts if item.get("job") is not None)

    tasks.sort(key=lambda t: t.start_dt)
    for idx, task in enumerate(tasks):
        if idx == 0:
            task.transit_before_minutes = 0
            continue
        prev = tasks[idx - 1]
        req = _required_transit_between(project, prev, _room_for_task(project, task), task.floor_index)
        task.transit_before_minutes = int(req)
        prev.transit_after_minutes = int(req)
    if tasks:
        tasks[-1].transit_after_minutes = 0

    return tasks, unscheduled, locked_conflicts


def _latest_slot_before(deadline, duration, release, windows):
    """Находит самое позднее окно для задачи, не нарушая release."""
    for ws, we in reversed(windows):
        latest_end = min(int(deadline), we)
        if latest_end - duration < ws:
            continue
        start = latest_end - duration
        if start >= max(ws, int(release)):
            return start
    return None


def _schedule_tasks_backward_to_end(project, tasks, target_end_minute, windows):
    """Перестраивает незакреплённые задачи назад от общего времени финиша."""
    if not tasks or any(getattr(t, "fixed", False) for t in tasks):
        return False
    ordered = sorted(tasks, key=lambda t: t.start_dt)
    end_cursor = int(target_end_minute)
    planned = []
    for idx in range(len(ordered) - 1, -1, -1):
        task = ordered[idx]
        duration = int(round((task.end_dt - task.start_dt).total_seconds() / 60))
        if idx < len(ordered) - 1:
            nxt = ordered[idx + 1]
            room = _room_for_task(project, task)
            transit = _get_transit_minutes(
                getattr(room, "room_type", "") if room else "",
                int(task.floor_index), int(nxt.floor_index),
            )
            deadline = end_cursor - transit
        else:
            deadline = end_cursor
        release = int(getattr(task, "release_minute", 0) or 0)
        start = _latest_slot_before(deadline, duration, release, windows)
        if start is None:
            return False
        end = start + duration
        task.start_dt = _time_to_datetime(task.start_dt.date(), start)
        task.end_dt = _time_to_datetime(task.end_dt.date(), end)
        planned.append(task)
        end_cursor = start
    return True


def _apply_time_priority_alignment(project, employee_tasks, target_date, windows, overtime_limit):
    if getattr(project, "priority_mode", "balanced") != "time":
        return
    if not employee_tasks:
        return

    # Фиксированные задачи являются сильнее режима «Время». Их положение задаёт
    # верхнюю границу, остальные сотрудники пытаются закончить точно там же.
    end_minutes = []
    for tasks in employee_tasks.values():
        if tasks:
            end_minutes.append(max(t.end_dt.hour * 60 + t.end_dt.minute for t in tasks))
    if not end_minutes:
        return
    target = max(end_minutes)
    shift_end = windows[-1][1] if windows else target
    # В штатном режиме общий финиш не должен сам создавать новую переработку.
    target = min(target, shift_end)

    for emp in sorted(employee_tasks):
        tasks = employee_tasks[emp]
        if not tasks or any(getattr(t, "fixed", False) for t in tasks):
            continue
        _schedule_tasks_backward_to_end(project, tasks, target, windows)


def _schedule_day(project, target_date, employees, allow_partial_schedule=True):
    shift_start, shift_end, windows = _regular_windows(project)
    overtime_limit = _to_minutes(getattr(project, "overtime_limit", "23:00"))
    overtime_limit = max(shift_end, overtime_limit)
    jobs = _build_jobs(project, windows)
    assignments = _rough_assignment(jobs, employees, project)

    employee_tasks = {i: [] for i in range(employees)}
    unscheduled = []
    lock_conflicts = []
    for emp in range(employees):
        tasks, missing, conflicts = _schedule_employee_jobs(
            project, assignments[emp], target_date, windows, shift_end, overtime_limit
        )
        for task in tasks:
            task.employee = emp
        employee_tasks[emp] = tasks
        unscheduled.extend(missing)
        lock_conflicts.extend(conflicts)

    _apply_time_priority_alignment(project, employee_tasks, target_date, windows, overtime_limit)

    final_tasks = sorted([t for group in employee_tasks.values() for t in group], key=lambda t: (t.employee, t.start_dt))

    # Пересчитываем transit после возможного time-alignment.
    for emp in range(employees):
        ordered = sorted(employee_tasks[emp], key=lambda t: t.start_dt)
        for idx, task in enumerate(ordered):
            if idx == 0:
                task.transit_before_minutes = 0
            else:
                prev = ordered[idx - 1]
                req = _required_transit_between(project, prev, _room_for_task(project, task), task.floor_index)
                task.transit_before_minutes = int(req)
                prev.transit_after_minutes = int(req)
        if ordered:
            ordered[-1].transit_after_minutes = 0

    regular_capacity = sum(b - a for a, b in windows)
    analytics = {}
    for emp in range(employees):
        tasks = employee_tasks[emp]
        cleaning = sum((t.end_dt - t.start_dt).total_seconds() / 60 for t in tasks)
        transit = sum(int(getattr(t, "transit_before_minutes", 0)) for t in tasks)
        work = cleaning + transit
        overtime = 0.0
        for t in tasks:
            if getattr(t, "is_overtime", False):
                overtime += max(0.0, (t.end_dt - _time_to_datetime(target_date, shift_end)).total_seconds() / 60)
        regular_work = max(0.0, work - overtime)
        last_end = max((t.end_dt for t in tasks), default=None)
        analytics[emp] = {
            "cleaning_minutes": round(cleaning, 1),
            "working_minutes": round(work, 1),
            "overtime_minutes": round(overtime, 1),
            "transit_minutes": int(transit),
            "capacity_minutes": regular_capacity,
            "utilization_percent": round(regular_work / regular_capacity * 100, 1) if regular_capacity else 0.0,
            "idle_minutes": round(max(0.0, regular_capacity - regular_work), 1),
            "end_time": last_end.strftime("%H:%M") if last_end else None,
        }

    end_minutes = []
    for a in analytics.values():
        if a.get("end_time"):
            end_minutes.append(_to_minutes(a["end_time"]))
    end_spread = max(end_minutes) - min(end_minutes) if end_minutes else 0

    result_breaks = _break_intervals(project, shift_start, shift_end)
    result = {
        "date": target_date,
        "tasks": final_tasks,
        "employees": employees,
        "shift_start": _time_to_datetime(target_date, shift_start),
        "shift_end": _time_to_datetime(target_date, shift_end),
        "breaks": result_breaks,
        "active_rooms": len([r for r in project.all_rooms() if not getattr(r, "disabled", False)]),
        "scheduled_rooms": len({(t.floor_index, t.room_id) for t in final_tasks}),
        "required_cleanings": len(jobs),
        "scheduled_cleanings": len(final_tasks),
        "missed_cleanings": len(unscheduled),
        "unscheduled_rooms_list": [
            {
                "floor_index": j["floor_index"],
                "room_id": j["room_id"],
                "room_name": j["room"].name,
                "cleaning_index": j["occurrence"] + 1,
                "required_minutes": j["duration"],
                "room": (j["floor_index"], j["room_id"]),
                "reason": "Не удалось разместить уборку в смене и разрешённой переработке",
            }
            for j in unscheduled
        ],
        "lock_conflicts": [
            {"floor_index": c["job"]["floor_index"], "room_id": c["job"]["room_id"], "cleaning_index": c["job"]["occurrence"] + 1, "reason": c["reason"]}
            for c in lock_conflicts
        ],
        "cleaning_minutes": round(sum((t.end_dt - t.start_dt).total_seconds() / 60 for t in final_tasks), 1),
        "transit_minutes": int(sum(int(getattr(t, "transit_before_minutes", 0)) for t in final_tasks)),
        "available_minutes": regular_capacity * employees,
        "employee_analytics": analytics,
        "overtime_tasks": sum(1 for t in final_tasks if getattr(t, "is_overtime", False)),
        "feasible": len(unscheduled) == 0 and len(lock_conflicts) == 0,
        "scheduled_room_keys": sorted({(t.floor_index, t.room_id) for t in final_tasks}),
        "break_start": _time_to_datetime(target_date, result_breaks[0][0]) if result_breaks else None,
        "break_end": _time_to_datetime(target_date, result_breaks[0][1]) if result_breaks else None,
        "end_time_spread_minutes": int(end_spread),
        "working_load_spread_minutes": int(max((x["working_minutes"] for x in analytics.values()), default=0) - min((x["working_minutes"] for x in analytics.values()), default=0)),
    }
    return result


def schedule_single_shift(project, target_date: Optional[date] = None, employees: Optional[int] = None,
                          allow_partial_schedule: bool = True):
    target = _normalize_date(target_date if target_date is not None else getattr(project, "start_date", None))
    emp_count = _safe_employee_count(project, employees)
    result = _schedule_day(project, target, emp_count, allow_partial_schedule)
    project.cleaning_tasks = list(result["tasks"])
    try:
        from schedule_validator import validate_schedule
        result["validation"] = validate_schedule(project, schedule_date=target)
        if result.get("lock_conflicts"):
            result["validation"]["valid"] = False
            result["validation"]["lock_conflicts"] = result["lock_conflicts"]
            result["validation"]["violations_summary"] = (result["validation"].get("violations_summary", "") + "\n" +
                "Зафиксированные задачи: " + "; ".join(c["reason"] for c in result["lock_conflicts"])).strip()
    except Exception as exc:
        result["validation"] = {"valid": False, "error": str(exc)}
    result["employee_loads"] = result["employee_analytics"]
    result["idle_minutes"] = round(sum(v["idle_minutes"] for v in result["employee_analytics"].values()), 1)
    result["rooms_active"] = result["active_rooms"]
    result["rooms_scheduled"] = result["scheduled_rooms"]
    result["total_tasks"] = result["scheduled_cleanings"]
    result["unscheduled_rooms"] = result["missed_cleanings"]
    result["total_workload_minutes"] = round(result["cleaning_minutes"] + result["transit_minutes"], 1)
    result["total_workload_hours"] = round(result["total_workload_minutes"] / 60, 2)
    result["cleaning_hours"] = result["cleaning_minutes"] / 60.0
    result["capacity_deficit"] = max(0.0, result["total_workload_minutes"] - result["available_minutes"])
    result["violations"] = result.get("validation", {})
    return result


def plan_cleaning_schedule(project, fixed_employees: Optional[int] = None, max_days: Optional[int] = None):
    if max_days is not None and int(max_days) != 1:
        raise ValueError("Production-режим создаёт расписание только на один рабочий день")
    return schedule_single_shift(project, target_date=getattr(project, "start_date", None), employees=fixed_employees).get("tasks", [])


def plan_cleaning_period(project, employees: Optional[int] = None, allow_partial_schedule: bool = True,
                         start_date=None, end_date=None):
    target = _normalize_date(start_date if start_date is not None else getattr(project, "start_date", None))
    result = schedule_single_shift(project, target_date=target, employees=employees, allow_partial_schedule=allow_partial_schedule)
    return {
        "tasks": result["tasks"],
        "days": [result],
        "employees": result["employees"],
        "period_days": 1,
        "expected_tasks": result["required_cleanings"],
        "scheduled_tasks": result["scheduled_cleanings"],
        "missed_cleanings": result["missed_cleanings"],
        "feasible": result["feasible"],
    }


def compute_recommended_employees(project):
    from cost_calculator import estimate_required_employees
    return estimate_required_employees(project)["employees"]


def _regular_windows_from_shift(shift_start, shift_end, breaks):
    windows = [(shift_start, shift_end)]
    for bs, be in breaks:
        new = []
        for ws, we in windows:
            if be <= ws or bs >= we:
                new.append((ws, we))
                continue
            if ws < bs:
                new.append((ws, bs))
            if be < we:
                new.append((be, we))
        windows = [(a, b) for a, b in new if b > a]
    return windows
