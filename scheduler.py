"""Адаптивный однодневный планировщик KleanPlann.

Главные принципы:
- планирование всегда строится на один рабочий день;
- частоты и длительности берутся из sanitarnorm;
- погодная поправка не размножает уборки всех помещений подряд;
- зоны ответственности являются жёстким предпочтением/назначением;
- внутри назначенной зоны задачи упорядочиваются по готовности, приоритету,
  близости помещений и трудоёмкости;
- «идеальное время» является временем разрешения, а не точкой, в которой
  сотрудника заставляют простаивать;
- переходы учитываются до начала следующей задачи;
- алгоритм имеет конечное число операций и не содержит циклического поиска слота.
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
LOOKAHEAD_MINUTES = 25
DISTANCE_WEIGHT = 0.06
GAP_WEIGHT = 2.5
LOAD_WEIGHT = 0.015
PRIORITY_BONUS = 50.0


class CleaningJob:
    def __init__(self, room_id, floor_index, duration, occurrence, frequency, priority=False, employee=0, is_overtime=False):
        self.room_id = room_id
        self.floor_index = floor_index
        self.duration = duration
        self.occurrence = occurrence
        self.frequency = frequency
        self.priority = priority
        self.employee = employee
        self.is_overtime = is_overtime


def _compute_ideal_start(job, shift_start, shift_end):
    span = max(0, int(shift_end) - int(shift_start))
    freq = max(1, int(getattr(job, "frequency", 1)))
    occurrence = max(0, int(getattr(job, "occurrence", 0)))
    # Legacy helper: returns a soft target, never a hard scheduling point.
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


def _to_minutes(value: str) -> int:
    h, m = map(int, str(value).split(":"))
    return h * 60 + m


def _time_to_datetime(base_date: date, minute_from_midnight: int) -> datetime:
    return datetime.combine(base_date, datetime.min.time()) + timedelta(minutes=minute_from_midnight)


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
    """Map cumulative working minutes to an absolute minute-of-day."""
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




# Compatibility alias used by older diagnostics/validators.
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
                jobs.append({
                    "floor_index": fi,
                    "room_id": room.id,
                    "room": room,
                    "duration": duration,
                    "frequency": frequency,
                    "occurrence": occurrence,
                    "release": release,
                    "priority": bool(getattr(room, "priority", False)),
                    "preferred_employee": zone_map.get((fi, room.id)),
                    "sequence": sequence,
                })
                sequence += 1
    jobs.sort(key=lambda j: (j["release"], not j["priority"], -j["duration"], j["floor_index"], j["room_id"], j["occurrence"]))
    return jobs


def _advance_past_break(start, duration, windows):
    for ws, we in windows:
        if start < ws:
            start = ws
        if ws <= start < we:
            if start + duration <= we:
                return start
            continue
    return None


def _next_feasible_start(current, duration, release, windows):
    candidate = max(int(current), int(release))
    for ws, we in windows:
        if candidate < ws:
            candidate = ws
        if candidate >= we:
            continue
        if candidate + duration <= we:
            return candidate
        candidate = we
    return None


def _overtime_start(current, duration, release, shift_end, overtime_limit):
    start = max(int(current), int(release), int(shift_end))
    if start + duration <= overtime_limit:
        return start
    return None


def _choose_next_job(project, remaining, current_minute, previous_task):
    if not remaining:
        return None
    eligible = [j for j in remaining if j["release"] <= current_minute + LOOKAHEAD_MINUTES]
    pool = eligible if eligible else remaining
    prev_room = _room_for_task(project, previous_task) if previous_task else None

    def score(job):
        distance = _distance(prev_room, job["room"]) if prev_room else 0.0
        wait = max(0, job["release"] - current_minute)
        lateness = max(0, current_minute - job["release"])
        priority = -PRIORITY_BONUS if job["priority"] else 0.0
        return (
            priority
            + wait * 1.6
            + lateness * 3.0
            + distance * DISTANCE_WEIGHT
            + job["duration"] * 0.02
        )

    return min(pool, key=lambda j: (score(j), j["release"], j["sequence"]))


def _rough_assignment(jobs, employees, project):
    """Назначает неназначенные задачи без разрушения существующих зон."""
    assignment = {i: [] for i in range(employees)}
    loads = {i: 0.0 for i in range(employees)}
    last_rooms = {i: None for i in range(employees)}

    for job in jobs:
        preferred = job.get("preferred_employee")
        if preferred is not None and 0 <= int(preferred) < employees:
            emp = int(preferred)
        else:
            candidates = range(employees)
            def cost(emp_idx):
                dist = _distance(last_rooms[emp_idx], job["room"]) if last_rooms[emp_idx] else 0.0
                return loads[emp_idx] * LOAD_WEIGHT + dist * DISTANCE_WEIGHT
            emp = min(candidates, key=cost)
        assignment[emp].append(job)
        loads[emp] += job["duration"]
        last_rooms[emp] = job["room"]
    return assignment


def _schedule_employee_jobs(project, jobs, target_date, windows, shift_end, overtime_limit):
    remaining = list(jobs)
    tasks: List[CleaningTask] = []
    current = windows[0][0] if windows else shift_end
    previous = None
    overtime_count = 0
    unscheduled = []

    while remaining:
        job = _choose_next_job(project, remaining, current, previous)
        if job is None:
            break
        remaining.remove(job)

        transit = _required_transit_between(project, previous, job["room"], job["floor_index"])
        ready = current + transit
        release = job["release"]
        start = _next_feasible_start(ready, job["duration"], release, windows)
        is_overtime = False
        if start is None:
            start = _overtime_start(ready, job["duration"], release, shift_end, overtime_limit)
            if start is None:
                unscheduled.append(job)
                continue
            is_overtime = True
            overtime_count += 1

        end = start + job["duration"]
        task = CleaningTask(
            room_id=job["room_id"],
            floor_index=job["floor_index"],
            start_dt=_time_to_datetime(target_date, start),
            end_dt=_time_to_datetime(target_date, end),
            employee=0,
            is_overtime=is_overtime,
            transit_after_minutes=0,
            priority=job["priority"],
        )
        task.transit_before_minutes = int(transit)
        task.transit_after_minutes = 0
        tasks.append(task)
        current = end
        previous = task

    # metadata transit_after is derived after the order is final.
    for a, b in zip(tasks, tasks[1:]):
        req = _required_transit_between(project, a, _room_for_task(project, b), b.floor_index)
        a.transit_after_minutes = int(req)
        b.transit_before_minutes = int(req)

    return tasks, unscheduled, overtime_count


def _schedule_day(project, target_date, employees, allow_partial_schedule=True):
    shift_start, shift_end, windows = _regular_windows(project)
    overtime_limit = _to_minutes(getattr(project, "overtime_limit", "23:00"))
    overtime_limit = max(shift_end, overtime_limit)
    jobs = _build_jobs(project, windows)
    assignments = _rough_assignment(jobs, employees, project)

    employee_tasks = {i: [] for i in range(employees)}
    unscheduled = []
    for emp in range(employees):
        tasks, missing, _ = _schedule_employee_jobs(
            project, assignments[emp], target_date, windows, shift_end, overtime_limit
        )
        for task in tasks:
            task.employee = emp
        employee_tasks[emp] = tasks
        unscheduled.extend(missing)

    final_tasks = sorted([t for group in employee_tasks.values() for t in group], key=lambda t: (t.employee, t.start_dt))

    # Записываем transit metadata заново уже по фактическому графику.
    for emp in range(employees):
        ordered = employee_tasks[emp]
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
        overtime = 0.0
        for t in tasks:
            if getattr(t, "is_overtime", False):
                overtime += max(0.0, (t.end_dt - _time_to_datetime(target_date, shift_end)).total_seconds() / 60)
        transit = sum(int(getattr(t, "transit_before_minutes", 0)) for t in tasks)
        regular_cleaning = max(0.0, cleaning - overtime)
        analytics[emp] = {
            "cleaning_minutes": round(cleaning, 1),
            "overtime_minutes": round(overtime, 1),
            "transit_minutes": int(transit),
            "capacity_minutes": regular_capacity,
            "utilization_percent": round(regular_cleaning / regular_capacity * 100, 1) if regular_capacity else 0.0,
            "idle_minutes": round(max(0.0, regular_capacity - regular_cleaning), 1),
        }

    result = {
        "date": target_date,
        "tasks": final_tasks,
        "employees": employees,
        "shift_start": _time_to_datetime(target_date, shift_start),
        "shift_end": _time_to_datetime(target_date, shift_end),
        "breaks": _break_intervals(project, shift_start, shift_end),
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
        "cleaning_minutes": round(sum((t.end_dt - t.start_dt).total_seconds() / 60 for t in final_tasks), 1),
        "transit_minutes": int(sum(int(getattr(t, "transit_before_minutes", 0)) for t in final_tasks)),
        "available_minutes": regular_capacity * employees,
        "employee_analytics": analytics,
        "overtime_tasks": sum(1 for t in final_tasks if getattr(t, "is_overtime", False)),
        "feasible": len(unscheduled) == 0,
        "scheduled_room_keys": sorted({(t.floor_index, t.room_id) for t in final_tasks}),
        "break_start": _time_to_datetime(target_date, result_breaks[0][0]) if (result_breaks := _break_intervals(project, shift_start, shift_end)) else None,
        "break_end": _time_to_datetime(target_date, result_breaks[0][1]) if result_breaks else None,
    }
    return result


def schedule_single_shift(project, target_date: Optional[date] = None, employees: Optional[int] = None, allow_partial_schedule: bool = True):
    target = _normalize_date(target_date if target_date is not None else getattr(project, "start_date", None))
    emp_count = _safe_employee_count(project, employees)
    result = _schedule_day(project, target, emp_count, allow_partial_schedule)
    project.cleaning_tasks = list(result["tasks"])
    try:
        from schedule_validator import validate_schedule
        result["validation"] = validate_schedule(project, schedule_date=target)
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
    # Производственный режим KleanPlann — однодневный график.
    if max_days is not None and int(max_days) != 1:
        raise ValueError("Production-режим создаёт расписание только на один рабочий день")
    return schedule_single_shift(project, target_date=getattr(project, "start_date", None), employees=fixed_employees).get("tasks", [])


def plan_cleaning_period(project, employees: Optional[int] = None, allow_partial_schedule: bool = True, start_date=None, end_date=None):
    """Совместимость со старым API: production-сценарий всё равно однодневный."""
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
