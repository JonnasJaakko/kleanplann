"""
schedule_validator — проверка физической выполнимости расписания.

После генерации scheduler-ом проверяет:
  * пересечения задач у одного сотрудника;
  * задачи вне смены;
  * задачи через обед;
  * negative/zero длительность;
  * пропущенные комнаты (unscheduled);
  * дубликаты комнат между сотрудниками;
  * частоту уборки;
  * disabled rooms;
  * загрузку и стоимость.
"""
from typing import List, Dict, Any, Set, Tuple
from datetime import datetime, date, timedelta, time
from project import Project, Room, CleaningTask, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day
import math


def validate_schedule(project: Project) -> Dict[str, Any]:
    """Валидирует расписание и возвращает детальный отчёт."""
    result: Dict[str, Any] = {
        "valid": True,
        # ROOMS
        "rooms_total": 0,
        "active_rooms": 0,
        "disabled_rooms": 0,
        "scheduled_rooms": 0,
        "unscheduled_rooms": 0,
        "duplicate_assignments": 0,
        # TASKS
        "tasks_total": 0,
        "scheduled_tasks": 0,
        "unscheduled_tasks": 0,
        "time_conflicts": 0,
        "break_violations": 0,
        "out_of_shift_tasks": 0,
        "negative_duration_tasks": 0,
        # FREQUENCY
        "frequency_required": 0,
        "frequency_scheduled": 0,
        "frequency_violations": 0,
        # WORKLOAD
        "cleaning_minutes": 0.0,
        "transit_minutes": 0.0,
        "total_minutes": 0.0,
        "total_hours": 0.0,
        # STAFF
        "employees": 0,
        "employee_loads": {},
        # COST
        "overtime_minutes": 0.0,
        "cost": 0.0,
        # Details
        "missed_rooms": [],
        "unscheduled_room_keys": [],
        "conflict_details": [],
        "break_violation_details": [],
        "out_of_shift_details": [],
        "frequency_details": [],
        "warnings": [],
    }

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]
    disabled_rooms = [r for r in all_rooms if r.disabled]
    result["rooms_total"] = len(all_rooms)
    result["active_rooms"] = len(active_rooms)
    result["disabled_rooms"] = len(disabled_rooms)

    # Смена и обед
    if not project.shifts:
        result["valid"] = False
        result["warnings"].append("Нет настроек смены")
        return result

    shift = project.shifts[0]
    try:
        shift_start = _time_to_minutes(shift.start_time)
        shift_end = _time_to_minutes(shift.end_time)
    except (ValueError, AttributeError):
        result["valid"] = False
        result["warnings"].append("Некорректное время смены")
        return result
    shift_len_min = max(0, shift_end - shift_start)

    break_intervals: List[Tuple[int, int]] = []
    for b_start, b_end in project.breaks:
        try:
            bs = _time_to_minutes(b_start)
            be = _time_to_minutes(b_end)
            if bs < be:
                break_intervals.append((bs, be))
        except (ValueError, AttributeError):
            pass

    tasks = project.cleaning_tasks or []

    # Группируем задачи по сотруднику
    tasks_by_emp: Dict[int, List[CleaningTask]] = {}
    for t in tasks:
        tasks_by_emp.setdefault(t.employee, []).append(t)

    result["tasks_total"] = len(tasks)
    result["scheduled_tasks"] = len(tasks)

    # === 1. Пересечения у одного сотрудника
    for emp, emp_tasks in tasks_by_emp.items():
        sorted_tasks = sorted(emp_tasks, key=lambda x: x.start_dt)
        for i in range(len(sorted_tasks) - 1):
            t1 = sorted_tasks[i]
            t2 = sorted_tasks[i + 1]
            if t1.end_dt > t2.start_dt:
                result["time_conflicts"] += 1
                result["conflict_details"].append(
                    f"Сотрудник {emp + 1}: {t1.start_dt.strftime('%H:%M')}-{t1.end_dt.strftime('%H:%M')} "
                    f"(ком. {t1.room_id}) и {t2.start_dt.strftime('%H:%M')}-{t2.end_dt.strftime('%H:%M')} "
                    f"(ком. {t2.room_id})"
                )
                result["valid"] = False

    # === 2. Задачи вне смены
    for t in tasks:
        t_start_min = t.start_dt.hour * 60 + t.start_dt.minute
        t_end_min = t.end_dt.hour * 60 + t.end_dt.minute
        if t_start_min < shift_start or t_end_min > shift_end:
            result["out_of_shift_tasks"] += 1
            result["out_of_shift_details"].append(
                f"Сотрудник {t.employee + 1}: {t.start_dt.strftime('%H:%M')}-{t.end_dt.strftime('%H:%M')} "
                f"(ком. {t.room_id}) — смена {shift.start_time}-{shift.end_time}"
            )
            result["valid"] = False

    # === 3. Задачи через обед
    for t in tasks:
        t_start_min = t.start_dt.hour * 60 + t.start_dt.minute
        t_end_min = t.end_dt.hour * 60 + t.end_dt.minute
        for bs, be in break_intervals:
            if t_start_min < be and t_end_min > bs:
                result["break_violations"] += 1
                result["break_violation_details"].append(
                    f"Сотрудник {t.employee + 1}: {t.start_dt.strftime('%H:%M')}-{t.end_dt.strftime('%H:%M')} "
                    f"(ком. {t.room_id}) пересекает обед"
                )
                result["valid"] = False

    # === 4. Отрицательная длительность
    for t in tasks:
        dur = (t.end_dt - t.start_dt).total_seconds()
        if dur <= 0:
            result["negative_duration_tasks"] += 1
            result["valid"] = False

    # === 5. Scheduled/unscheduled комнаты
    scheduled_rooms: Set[Tuple[int, int]] = set()
    for t in tasks:
        scheduled_rooms.add((t.floor_index, t.room_id))

    result["scheduled_rooms"] = len(scheduled_rooms)

    active_room_keys: Set[Tuple[int, int]] = set()
    for fi, floor in enumerate(project.floors):
        for room in floor.rooms:
            if not room.disabled:
                active_room_keys.add((fi, room.id))

    unscheduled_keys = active_room_keys - scheduled_rooms
    result["unscheduled_rooms"] = len(unscheduled_keys)
    result["unscheduled_room_keys"] = [list(k) for k in sorted(unscheduled_keys)]

    for fi, rid in sorted(unscheduled_keys):
        if fi < len(project.floors):
            room = next((r for r in project.floors[fi].rooms if r.id == rid), None)
            if room:
                result["missed_rooms"].append(
                    f"{project.floors[fi].name}: {room.name} (№{room.id + 1}, {room.area_m2:.0f} м²)"
                )

    # === 5b. Disabled rooms в расписании (warnings)
    for room in disabled_rooms:
        for fi, floor in enumerate(project.floors):
            if room in floor.rooms:
                if (fi, room.id) in scheduled_rooms:
                    result["warnings"].append(
                        f"Disabled room {room.name} (№{room.id + 1}) присутствует в расписании"
                    )
                break

    # === 6. Дубликаты комнат между сотрудниками
    room_emp: Dict[Tuple[int, int], int] = {}
    for t in tasks:
        key = (t.floor_index, t.room_id)
        if key in room_emp and room_emp[key] != t.employee:
            result["duplicate_assignments"] += 1
        room_emp[key] = t.employee

    # === 7. Частота уборки
    for room in active_rooms:
        required_freq = get_frequency_per_day(room.room_type)
        required_freq = max(1, int(round(required_freq * project.weather_factor)))
        result["frequency_required"] += required_freq
        actual_count = sum(
            1 for t in tasks
            if t.room_id == room.id and t.floor_index == _find_floor_index(project, room)
        )
        result["frequency_scheduled"] += actual_count
        if actual_count < required_freq:
            result["frequency_violations"] += 1
            result["frequency_details"].append(
                f"{room.name} (№{room.id + 1}): требуется {required_freq}, "
                f"запланировано {actual_count}"
            )
            result["valid"] = False

    # === 8. Загрузка сотрудников + transit + cost
    total_cleaning = 0.0
    total_transit = 0.0
    result["employees"] = len(tasks_by_emp)

    for emp, emp_tasks in tasks_by_emp.items():
        emp_sorted = sorted(emp_tasks, key=lambda x: x.start_dt)
        emp_total = 0.0
        for t in emp_sorted:
            emp_total += (t.end_dt - t.start_dt).total_seconds() / 60.0
        total_cleaning += emp_total
        result["employee_loads"][emp] = round(emp_total, 1)

        # Transit между задачами (оценка по промежуткам, не более 10 мин)
        for i in range(len(emp_sorted) - 1):
            gap = (emp_sorted[i + 1].start_dt - emp_sorted[i].end_dt).total_seconds() / 60.0
            if gap > 0:
                total_transit += min(gap, 10.0)

    result["cleaning_minutes"] = round(total_cleaning, 1)
    result["transit_minutes"] = round(total_transit, 1)
    result["total_minutes"] = round(total_cleaning + total_transit, 1)
    result["total_hours"] = round(result["total_minutes"] / 60.0, 2)

    # === 9. Overtime и cost
    # Доступное время смены с вычетом обеда
    avail_shift = shift_len_min
    for bs, be in break_intervals:
        # Пересечение перерыва со сменой
        overlap = max(0, min(shift_end, be) - max(shift_start, bs))
        avail_shift = max(0, avail_shift - overlap)

    rate = getattr(project, 'hourly_rate', 200.0)
    for emp, emp_total in result["employee_loads"].items():
        if emp_total > avail_shift:
            ot = emp_total - avail_shift
            result["overtime_minutes"] += ot

    # Стоимость: (clean_min / 60 * rate) + overtime bonus
    reg_hours = result["cleaning_minutes"] / 60.0
    ot_hours = result["overtime_minutes"] / 60.0
    result["cost"] = round(reg_hours * rate + ot_hours * rate * 1.5, 2)

    # === 10. valid = False, если есть unscheduled активные комнаты
    if result["unscheduled_rooms"] > 0:
        result["valid"] = False
        result["warnings"].append(
            f"Не запланированы {result['unscheduled_rooms']} активных комнат"
        )

    return result


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _find_floor_index(project: Project, room: Room) -> int:
    for fi, floor in enumerate(project.floors):
        if room in floor.rooms:
            return fi
    return 0