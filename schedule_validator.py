"""
Проверка расписания на физическую выполнимость.

Важное отличие от старой версии:
- переработка является допустимой, если задача явно помечена is_overtime;
- частота проверяется отдельно по каждому дню;
- сотрудник считается существующим даже если у него пока нет задач;
- переходы берутся из metadata scheduler, а не из любого случайного простоя;
- задача не должна пересекать обед;
- все активные комнаты и все требуемые уборки должны присутствовать.
"""
from typing import List, Dict, Any, Set, Tuple
from datetime import datetime
from collections import defaultdict
import math

from project import Project, Room, CleaningTask
from sanitarnorm import get_frequency_per_day


def _time_to_minutes(value: str) -> int:
    h, m = map(int, value.split(":"))
    return h * 60 + m


def _find_floor_index(project: Project, room: Room) -> int:
    for fi, floor in enumerate(project.floors):
        if room in floor.rooms:
            return fi
    return 0


def _shift_intervals(project):
    if not project.shifts:
        return None, []

    shift = project.shifts[0]
    start = _time_to_minutes(shift.start_time)
    end = _time_to_minutes(shift.end_time)

    breaks = []
    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs, be = _time_to_minutes(b_start), _time_to_minutes(b_end)
        except Exception:
            continue
        if be > bs:
            breaks.append((bs, be))

    return (start, end), breaks


def _required_frequency(project, room) -> int:
    freq = get_frequency_per_day(room.room_type)
    weather = getattr(project, "weather_factor", 1.0)
    return max(1, int(round(freq * weather)))


def validate_schedule(project: Project) -> Dict[str, Any]:
    result = {
        "valid": True,

        "rooms_total": 0,
        "active_rooms": 0,
        "disabled_rooms": 0,
        "scheduled_rooms": 0,
        "unscheduled_rooms": 0,
        "duplicate_assignments": 0,

        "tasks_total": 0,
        "scheduled_tasks": 0,
        "unscheduled_tasks": 0,
        "time_conflicts": 0,
        "break_violations": 0,
        "out_of_shift_tasks": 0,
        "overtime_tasks": 0,
        "negative_duration_tasks": 0,

        "frequency_required": 0,
        "frequency_scheduled": 0,
        "frequency_violations": 0,

        "cleaning_minutes": 0.0,
        "transit_minutes": 0.0,
        "total_minutes": 0.0,
        "total_hours": 0.0,

        "employees": max(1, int(getattr(project, "employees_count", 1))),
        "employee_loads": {},
        "employee_idle": {},
        "underutilized_employees": [],

        "overtime_minutes": 0.0,
        "cost": 0.0,

        "missed_rooms": [],
        "unscheduled_room_keys": [],
        "conflict_details": [],
        "break_violation_details": [],
        "out_of_shift_details": [],
        "frequency_details": [],
        "overtime_details": [],
        "warnings": [],
    }

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not getattr(r, "disabled", False)]
    disabled_rooms = [r for r in all_rooms if getattr(r, "disabled", False)]

    result["rooms_total"] = len(all_rooms)
    result["active_rooms"] = len(active_rooms)
    result["disabled_rooms"] = len(disabled_rooms)

    shift_info, break_intervals = _shift_intervals(project)
    if shift_info is None:
        result["valid"] = False
        result["warnings"].append("Нет настроек смены")
        return result

    shift_start, shift_end = shift_info

    tasks = list(getattr(project, "cleaning_tasks", []) or [])
    result["tasks_total"] = len(tasks)
    result["scheduled_tasks"] = len(tasks)

    # ---------- задачи по сотрудникам ----------
    tasks_by_emp: Dict[int, List[CleaningTask]] = defaultdict(list)
    for task in tasks:
        tasks_by_emp[int(task.employee)].append(task)

    # ---------- пересечения ----------
    for emp, emp_tasks in tasks_by_emp.items():
        ordered = sorted(emp_tasks, key=lambda t: t.start_dt)
        for a, b in zip(ordered, ordered[1:]):
            if a.end_dt > b.start_dt:
                result["time_conflicts"] += 1
                result["conflict_details"].append(
                    f"Сотрудник {emp + 1}: "
                    f"{a.start_dt:%H:%M}-{a.end_dt:%H:%M} и "
                    f"{b.start_dt:%H:%M}-{b.end_dt:%H:%M}"
                )

    if result["time_conflicts"]:
        result["valid"] = False

    # ---------- время смены / переработка ----------
    for task in tasks:
        start = task.start_dt.hour * 60 + task.start_dt.minute
        end = task.end_dt.hour * 60 + task.end_dt.minute
        is_overtime = bool(getattr(task, "is_overtime", False))

        if end <= start:
            result["negative_duration_tasks"] += 1
            result["valid"] = False

        if is_overtime:
            result["overtime_tasks"] += 1
            ot = max(0, end - max(start, shift_end))
            result["overtime_minutes"] += ot
            result["overtime_details"].append(
                f"Сотрудник {task.employee + 1}: "
                f"ком. {task.room_id + 1}, {task.start_dt:%H:%M}-{task.end_dt:%H:%M}"
            )
        else:
            # Обычная задача обязана полностью находиться в смене.
            if start < shift_start or end > shift_end:
                result["out_of_shift_tasks"] += 1
                result["out_of_shift_details"].append(
                    f"Сотрудник {task.employee + 1}: "
                    f"{task.start_dt:%H:%M}-{task.end_dt:%H:%M} "
                    f"(ком. {task.room_id + 1})"
                )
                result["valid"] = False

        # Даже сверхурочная уборка не может пересекать обед.
        for bs, be in break_intervals:
            if start < be and end > bs:
                result["break_violations"] += 1
                result["break_violation_details"].append(
                    f"Сотрудник {task.employee + 1}: "
                    f"{task.start_dt:%H:%M}-{task.end_dt:%H:%M} "
                    f"(ком. {task.room_id + 1}) пересекает обед"
                )
                result["valid"] = False

    # ---------- комнаты ----------
    active_keys = {
        (fi, room.id)
        for fi, floor in enumerate(project.floors)
        for room in floor.rooms
        if not getattr(room, "disabled", False)
    }

    scheduled_keys = {(t.floor_index, t.room_id) for t in tasks}
    result["scheduled_rooms"] = len(active_keys & scheduled_keys)

    missing = active_keys - scheduled_keys
    result["unscheduled_rooms"] = len(missing)
    result["unscheduled_room_keys"] = [list(k) for k in sorted(missing)]

    if missing:
        result["valid"] = False
        for fi, rid in sorted(missing):
            room = next(
                (r for r in project.floors[fi].rooms if r.id == rid),
                None
            )
            if room:
                result["missed_rooms"].append(
                    f"{project.floors[fi].name}: {room.name} "
                    f"(№{room.id + 1}, {room.area_m2:.0f} м²)"
                )

    # Дубликат одного и того же помещения у разных сотрудников.
    room_employees = defaultdict(set)
    for t in tasks:
        room_employees[(t.floor_index, t.room_id)].add(t.employee)

    for key, emps in room_employees.items():
        if len(emps) > 1:
            result["duplicate_assignments"] += 1

    # ---------- частота по дням ----------
    counts = defaultdict(int)
    for t in tasks:
        counts[(t.start_dt.date(), t.floor_index, t.room_id)] += 1

    # Планировщик текущей версии строит один день. Поэтому валидируем
    # каждый день, для которого есть расписание.
    schedule_days = sorted({t.start_dt.date() for t in tasks})
    if not schedule_days:
        schedule_days = [getattr(project, "start_date", datetime.today().date())]

    for room in active_rooms:
        fi = _find_floor_index(project, room)
        required = _required_frequency(project, room)

        for day in schedule_days:
            actual = counts.get((day, fi, room.id), 0)
            result["frequency_required"] += required
            result["frequency_scheduled"] += actual

            if actual < required:
                result["frequency_violations"] += 1
                result["frequency_details"].append(
                    f"{day:%d.%m.%Y} — {room.name} (№{room.id + 1}): "
                    f"требуется {required}, запланировано {actual}"
                )

    if result["frequency_violations"]:
        result["valid"] = False

    # ---------- нагрузка ----------
    regular_capacity = 0
    for a, b in _regular_windows_from_shift(shift_start, shift_end, break_intervals):
        regular_capacity += b - a

    total_cleaning = 0.0
    total_transit = 0.0

    for emp in range(result["employees"]):
        emp_tasks = sorted(tasks_by_emp.get(emp, []), key=lambda t: t.start_dt)

        clean = sum(
            max(0.0, (t.end_dt - t.start_dt).total_seconds() / 60.0)
            for t in emp_tasks
        )
        transit = sum(
            max(0, int(getattr(t, "transit_after_minutes", 0)))
            for t in emp_tasks
        )

        # Не считаем последний переход после окончания смены.
        if emp_tasks and emp_tasks[-1].end_dt.hour * 60 + emp_tasks[-1].end_dt.minute >= shift_end:
            transit = max(
                0,
                transit - int(getattr(emp_tasks[-1], "transit_after_minutes", 0))
            )

        total_cleaning += clean
        total_transit += transit
        result["employee_loads"][emp] = round(clean, 1)

        regular_clean = sum(
            max(0.0, (t.end_dt - t.start_dt).total_seconds() / 60.0)
            for t in emp_tasks
            if not getattr(t, "is_overtime", False)
        )
        idle = max(0.0, regular_capacity - regular_clean)
        result["employee_idle"][emp] = round(idle, 1)

        # Пустой сотрудник — важная проблема, но не "ошибка норматива":
        # это означает, что задано больше людей, чем реально требуется.
        if emp_tasks and regular_clean > 0:
            result["underutilized_employees"].append({
                "employee": emp,
                "idle_minutes": round(idle, 1),
                "utilization_percent": round(
                    regular_clean / regular_capacity * 100, 1
                ) if regular_capacity else 0.0,
            })

    result["cleaning_minutes"] = round(total_cleaning, 1)
    result["transit_minutes"] = round(total_transit, 1)
    result["total_minutes"] = round(total_cleaning + total_transit, 1)
    result["total_hours"] = round(result["total_minutes"] / 60.0, 2)

    # ---------- стоимость ----------
    rate = float(getattr(project, "hourly_rate", 200.0))
    regular_minutes = max(0.0, total_cleaning - result["overtime_minutes"])
    regular_hours = regular_minutes / 60.0
    overtime_hours = result["overtime_minutes"] / 60.0
    result["cost"] = round(
        regular_hours * rate + overtime_hours * rate * 1.5,
        2
    )

    # Пустые сотрудники предупреждают о завышенном штате.
    for emp in range(result["employees"]):
        if not tasks_by_emp.get(emp):
            result["warnings"].append(
                f"Сотрудник {emp + 1} не получил ни одной задачи"
            )

    if result["out_of_shift_tasks"] or result["break_violations"]:
        result["valid"] = False

    return result


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
