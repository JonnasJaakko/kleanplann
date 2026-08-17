"""
Валидатор практического расписания.

Проверяет:
- все активные помещения;
- нормативную кратность по каждому дню;
- зоны ответственности;
- отсутствие пересечений;
- обед/перерывы;
- физическую возможность переходов;
- минимальный интервал повторной уборки;
- фактическую переработку;
- загрузку каждого сотрудника.

Сверхурочная задача НЕ считается "пропавшей": она считается выполненной,
но отдельно отображается как overtime. Поэтому отчёт может одновременно
говорить "все уборки выполнены" и показывать, сколько минут пришлось
вынести за пределы смены.
"""
from typing import Dict, Any, List
from collections import defaultdict
from datetime import datetime

from project import Project, Room, CleaningTask
from sanitarnorm import get_frequency_per_day


COOLDOWN_BY_TYPE = {
    "санузел": 180,
    "кухня": 180,
    "коридор": 120,
    "default": 90,
}
TRANSIT_SAME_FLOOR = 1
TRANSIT_TO_OTHER_FLOOR = 5
TRANSIT_TOILET = 3
MAX_OVERTIME_PER_EMPLOYEE = 180


def _time_to_minutes(value: str) -> int:
    h, m = map(int, value.split(":"))
    return h * 60 + m


def _type_key(room_type: str) -> str:
    s = (room_type or "").lower()
    for key in ("санузел", "кухня", "коридор"):
        if key in s:
            return key
    return "default"


def _cooldown(room_type: str) -> int:
    return COOLDOWN_BY_TYPE[_type_key(room_type)]


def _transit(prev_room_type: str, prev_floor: int, next_floor: int) -> int:
    if prev_room_type and _type_key(prev_room_type) == "санузел":
        return TRANSIT_TOILET
    if prev_floor != next_floor:
        return TRANSIT_TO_OTHER_FLOOR
    return TRANSIT_SAME_FLOOR


def _find_floor_index(project: Project, room: Room) -> int:
    for fi, floor in enumerate(project.floors):
        if room in floor.rooms:
            return fi
    return 0


def _required_frequency(project, room) -> int:
    return max(
        1,
        int(round(
            get_frequency_per_day(room.room_type)
            * float(getattr(project, "weather_factor", 1.0))
        )),
    )


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


def _overlaps_break(start, end, breaks):
    return any(start < be and end > bs for bs, be in breaks)


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
        "unapproved_out_of_shift_tasks": 0,
        "overtime_tasks": 0,
        "negative_duration_tasks": 0,

        "frequency_required": 0,
        "frequency_scheduled": 0,
        "frequency_violations": 0,
        "repeat_interval_violations": 0,

        "transition_violations": 0,
        "zone_violations": 0,
        "empty_employees": 0,
        "idle_violations": 0,

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
        "transition_details": [],
        "repeat_interval_details": [],
        "zone_details": [],
        "warnings": [],
    }

    all_rooms = project.all_rooms()
    active_rooms = [
        r for r in all_rooms
        if not getattr(r, "disabled", False)
    ]
    result["rooms_total"] = len(all_rooms)
    result["active_rooms"] = len(active_rooms)
    result["disabled_rooms"] = len(all_rooms) - len(active_rooms)

    shift_info, break_intervals = _shift_intervals(project)
    if shift_info is None:
        result["valid"] = False
        result["warnings"].append("Нет настроек смены")
        return result

    shift_start, shift_end = shift_info
    regular_windows = _regular_windows_from_shift(
        shift_start, shift_end, break_intervals
    )
    regular_capacity = sum(b - a for a, b in regular_windows)

    tasks = list(getattr(project, "cleaning_tasks", []) or [])
    result["tasks_total"] = len(tasks)
    result["scheduled_tasks"] = len(tasks)

    tasks_by_emp = defaultdict(list)
    for task in tasks:
        tasks_by_emp[int(task.employee)].append(task)

    # ---------- конфликты одного сотрудника ----------
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

    # ---------- время, обед, overtime ----------
    overtime_by_emp = defaultdict(float)
    for task in tasks:
        start = task.start_dt.hour * 60 + task.start_dt.minute
        end = task.end_dt.hour * 60 + task.end_dt.minute
        is_overtime = bool(getattr(task, "is_overtime", False))

        if end <= start:
            result["negative_duration_tasks"] += 1
            result["valid"] = False

        if start < shift_start or end > shift_end:
            result["out_of_shift_tasks"] += 1
            result["out_of_shift_details"].append(
                f"Сотрудник {task.employee + 1}: "
                f"ком. {task.room_id + 1}, "
                f"{task.start_dt:%H:%M}-{task.end_dt:%H:%M}"
                + (" — СВЕРХ СМЕНЫ" if is_overtime else "")
            )

            if is_overtime:
                result["overtime_tasks"] += 1
                ot = max(0, end - max(start, shift_end))
                overtime_by_emp[task.employee] += ot
                result["overtime_minutes"] += ot
                result["overtime_details"].append(
                    f"Сотрудник {task.employee + 1}: "
                    f"ком. {task.room_id + 1}, "
                    f"{task.start_dt:%H:%M}-{task.end_dt:%H:%M}"
                )
            else:
                result["unapproved_out_of_shift_tasks"] += 1
                result["valid"] = False

        if _overlaps_break(start, end, break_intervals):
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

    scheduled_keys = {
        (int(t.floor_index), int(t.room_id))
        for t in tasks
    }

    result["scheduled_rooms"] = len(active_keys & scheduled_keys)
    missing = active_keys - scheduled_keys
    result["unscheduled_rooms"] = len(missing)
    result["unscheduled_room_keys"] = [list(k) for k in sorted(missing)]

    if missing:
        result["valid"] = False
        for fi, rid in sorted(missing):
            room = next(
                (r for r in project.floors[fi].rooms if r.id == rid),
                None,
            )
            if room:
                result["missed_rooms"].append(
                    f"{project.floors[fi].name}: {room.name} "
                    f"(№{room.id + 1}, {room.area_m2:.0f} м²)"
                )

    # ---------- зоны ----------
    zone_map = {}
    for zone in getattr(project, "zones", []) or []:
        fi = getattr(zone, "floor_index", 0)
        emp = getattr(zone, "employee_index", 0)
        for rid in getattr(zone, "room_ids", []) or []:
            zone_map[(fi, rid)] = emp

    for t in tasks:
        key = (int(t.floor_index), int(t.room_id))
        preferred = zone_map.get(key)
        if preferred is not None and preferred != int(t.employee):
            result["zone_violations"] += 1
            result["zone_details"].append(
                f"Ком. {t.room_id + 1}: сотрудник {t.employee + 1}, "
                f"закреплён за сотрудником {preferred + 1}"
            )

    if result["zone_violations"]:
        result["valid"] = False

    # ---------- дублирование комнаты у сотрудников ----------
    room_employees = defaultdict(set)
    for t in tasks:
        room_employees[(t.floor_index, t.room_id)].add(t.employee)

    for key, emps in room_employees.items():
        if len(emps) > 1:
            result["duplicate_assignments"] += 1

    # ---------- частота по дням ----------
    counts = defaultdict(int)
    room_tasks = defaultdict(list)
    for t in tasks:
        counts[(t.start_dt.date(), t.floor_index, t.room_id)] += 1
        room_tasks[(t.floor_index, t.room_id)].append(t)

    schedule_days = sorted({t.start_dt.date() for t in tasks})
    if not schedule_days:
        schedule_days = [
            getattr(project, "start_date", datetime.today().date())
        ]

    for room in active_rooms:
        fi = _find_floor_index(project, room)
        required = _required_frequency(project, room)

        for day in schedule_days:
            actual = counts.get((day, fi, room.id), 0)
            result["frequency_required"] += required
            result["frequency_scheduled"] += actual

            if actual != required:
                result["frequency_violations"] += 1
                result["frequency_details"].append(
                    f"{day:%d.%m.%Y} — {room.name} (№{room.id + 1}): "
                    f"требуется {required}, запланировано {actual}"
                )

    if result["frequency_violations"]:
        result["valid"] = False

    # ---------- повторные уборки и переходы ----------
    room_map = {
        (fi, room.id): room
        for fi, floor in enumerate(project.floors)
        for room in floor.rooms
    }

    for key, rtasks in room_tasks.items():
        ordered = sorted(rtasks, key=lambda t: t.start_dt)
        room = room_map.get(key)
        if not room:
            continue

        cooldown = _cooldown(room.room_type)
        for a, b in zip(ordered, ordered[1:]):
            gap = (b.start_dt - a.end_dt).total_seconds() / 60.0
            if gap < cooldown:
                result["repeat_interval_violations"] += 1
                result["repeat_interval_details"].append(
                    f"Ком. {room.id + 1}: между уборками только "
                    f"{gap:.0f} мин, требуется не менее {cooldown} мин"
                )

    if result["repeat_interval_violations"]:
        result["valid"] = False

    for emp in range(result["employees"]):
        ordered = sorted(
            tasks_by_emp.get(emp, []),
            key=lambda t: t.start_dt,
        )

        cleaning = 0.0
        transit = 0.0

        for t in ordered:
            cleaning += max(
                0.0,
                (t.end_dt - t.start_dt).total_seconds() / 60.0,
            )
            transit += max(
                0,
                int(getattr(t, "transit_after_minutes", 0)),
            )

        result["employee_loads"][emp] = round(cleaning, 1)
        result["employee_idle"][emp] = round(
            max(0.0, regular_capacity - cleaning - transit),
            1,
        )

        if not ordered:
            result["empty_employees"] += 1
            result["warnings"].append(
                f"Сотрудник {emp + 1} не получил ни одной задачи"
            )
        else:
            idle = result["employee_idle"][emp]
            if idle > 15:
                result["idle_violations"] += 1
                result["underutilized_employees"].append({
                    "employee": emp,
                    "idle_minutes": idle,
                    "utilization_percent": round(
                        max(
                            0.0,
                            (regular_capacity - idle)
                            / regular_capacity * 100,
                        ) if regular_capacity else 0.0,
                        1,
                    ),
                })

        # Переход проверяется по реальному разрыву между задачами.
        for a, b in zip(ordered, ordered[1:]):
            prev_room = room_map.get((a.floor_index, a.room_id))
            if not prev_room:
                continue

            required_transit = _transit(
                prev_room.room_type,
                a.floor_index,
                b.floor_index,
            )
            gap = (b.start_dt - a.end_dt).total_seconds() / 60.0
            if gap < required_transit:
                result["transition_violations"] += 1
                result["transition_details"].append(
                    f"Сотрудник {emp + 1}: "
                    f"между ком. {a.room_id + 1} и {b.room_id + 1} "
                    f"{gap:.0f} мин, требуется {required_transit} мин"
                )

    if result["time_conflicts"] or result["break_violations"]:
        result["valid"] = False
    if result["transition_violations"]:
        result["valid"] = False

    # Пустой сотрудник — нарушение явного требования пользователя.
    if result["empty_employees"]:
        result["valid"] = False

    # ---------- общая трудоёмкость ----------
    result["cleaning_minutes"] = round(
        sum(
            max(0.0, (t.end_dt - t.start_dt).total_seconds() / 60.0)
            for t in tasks
        ),
        1,
    )
    result["transit_minutes"] = round(
        sum(
            max(0, int(getattr(t, "transit_after_minutes", 0)))
            for t in tasks
        ),
        1,
    )
    result["total_minutes"] = round(
        result["cleaning_minutes"] + result["transit_minutes"],
        1,
    )
    result["total_hours"] = round(result["total_minutes"] / 60.0, 2)

    # ---------- overtime ----------
    result["overtime_minutes"] = round(
        float(result["overtime_minutes"]), 1
    )
    for emp, value in overtime_by_emp.items():
        if value > MAX_OVERTIME_PER_EMPLOYEE:
            result["warnings"].append(
                f"Сотрудник {emp + 1}: переработка {value:.0f} мин "
                f"превышает практический предел "
                f"{MAX_OVERTIME_PER_EMPLOYEE} мин"
            )
            result["valid"] = False

    # ---------- стоимость ----------
    rate = float(getattr(project, "hourly_rate", 200.0))
    overtime = result["overtime_minutes"]
    regular_total = max(
        0.0,
        result["total_minutes"] - overtime,
    )
    result["cost"] = round(
        regular_total / 60.0 * rate
        + overtime / 60.0 * rate * 1.5,
        2,
    )

    return result
