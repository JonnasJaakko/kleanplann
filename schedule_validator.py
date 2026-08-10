"""
schedule_validator — проверка физической выполнимости расписания.

После генерации scheduler-ом проверяет:
  * пересечения задач у одного сотрудника;
  * задачи вне смены;
  * задачи через обед;
  * отрицательная/нулевая длительность;
  * пропущенные комнаты;
  * дубликаты комнат между сотрудниками;
  * частота уборки;
  * disabled rooms.
"""
from typing import List, Dict, Any, Set, Tuple
from datetime import datetime, date, timedelta, time
from project import Project, Room, CleaningTask, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day


def validate_schedule(project: Project) -> Dict[str, Any]:
    """Валидирует расписание и возвращает детальный отчёт."""
    result: Dict[str, Any] = {
        "valid": True,
        "rooms_total": 0,
        "rooms_active": 0,
        "rooms_scheduled": 0,
        "rooms_disabled": 0,
        "duplicate_assignments": 0,
        "time_conflicts": 0,
        "break_violations": 0,
        "out_of_shift_tasks": 0,
        "negative_duration_tasks": 0,
        "frequency_violations": 0,
        "missed_rooms": [],
        "conflict_details": [],
        "break_violation_details": [],
        "out_of_shift_details": [],
        "frequency_details": [],
        "employee_load": {},
        "total_tasks": 0,
        "total_minutes": 0.0,
        "warnings": [],
    }

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not r.disabled]
    disabled_rooms = [r for r in all_rooms if r.disabled]
    result["rooms_total"] = len(all_rooms)
    result["rooms_active"] = len(active_rooms)
    result["rooms_disabled"] = len(disabled_rooms)

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

    break_intervals = []
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

    result["total_tasks"] = len(tasks)

    # 1. Проверка пересечений у одного сотрудника
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

    # 2. Проверка задач вне смены
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

    # 3. Проверка задач через обед
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

    # 4. Проверка отрицательной/нулевой длительности
    for t in tasks:
        dur = (t.end_dt - t.start_dt).total_seconds()
        if dur <= 0:
            result["negative_duration_tasks"] += 1
            result["valid"] = False

    # 5. Какие комнаты назначены, какие — нет
    scheduled_rooms: Set[Tuple[int, int]] = set()  # (floor_index, room_id)
    for t in tasks:
        scheduled_rooms.add((t.floor_index, t.room_id))

    # 5a. Пропущенные активные комнаты
    result["rooms_scheduled"] = len(scheduled_rooms)
    for room in active_rooms:
        # Ищем комнату на её этаже
        for fi, floor in enumerate(project.floors):
            if room in floor.rooms:
                if (fi, room.id) not in scheduled_rooms:
                    result["missed_rooms"].append(
                        f"{floor.name}: {room.name} (№{room.id + 1}, {room.area_m2:.0f} м²)"
                    )
                break

    # 5b. Disabled rooms в расписании
    for room in disabled_rooms:
        for fi, floor in enumerate(project.floors):
            if room in floor.rooms:
                if (fi, room.id) in scheduled_rooms:
                    result["warnings"].append(
                        f"Disabled room {room.name} (№{room.id + 1}) присутствует в расписании"
                    )
                break

    # 6. Дубликаты комнат между сотрудниками (на одном этаже)
    room_emp: Dict[Tuple[int, int], int] = {}
    for t in tasks:
        key = (t.floor_index, t.room_id)
        if key in room_emp and room_emp[key] != t.employee:
            result["duplicate_assignments"] += 1
        room_emp[key] = t.employee

    # 7. Частота уборки
    for room in active_rooms:
        required_freq = get_frequency_per_day(room.room_type)
        required_freq = max(1, int(round(required_freq * project.weather_factor)))
        actual_count = sum(
            1 for t in tasks
            if t.room_id == room.id and t.floor_index == _find_floor_index(project, room)
        )
        if actual_count < required_freq:
            result["frequency_violations"] += 1
            result["frequency_details"].append(
                f"{room.name} (№{room.id + 1}): требуется {required_freq}, "
                f"запланировано {actual_count}"
            )
            result["valid"] = False

    # 8. Загрузка сотрудников
    for emp, emp_tasks in tasks_by_emp.items():
        total_min = 0
        for t in emp_tasks:
            total_min += (t.end_dt - t.start_dt).total_seconds() / 60
        result["employee_load"][emp] = round(total_min, 1)

    result["total_minutes"] = sum(result["employee_load"].values())

    if not result["warnings"] and result["valid"]:
        result["valid"] = True

    return result


def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _find_floor_index(project: Project, room: Room) -> int:
    for fi, floor in enumerate(project.floors):
        if room in floor.rooms:
            return fi
    return 0