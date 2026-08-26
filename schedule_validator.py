"""Strict validation of a KleanPlann schedule for production export."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Any

from project import Project, CleaningTask
from sanitarnorm import get_effective_frequency
from scheduler import _get_transit_minutes, _get_room_for_task, _regular_windows_from_shift


def _normalize_date(value, fallback=None):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try: return datetime.strptime(value, fmt).date()
            except ValueError: pass
    return fallback or date.today()


def _schedule_day(project, schedule_date=None):
    return _normalize_date(schedule_date if schedule_date is not None else getattr(project, "start_date", None))


def _time_to_minutes(value):
    h, m = map(int, str(value).split(":")); return h*60+m


def _shift_info(project):
    if not getattr(project, "shifts", None): return None, []
    s = project.shifts[0]
    start, end = _time_to_minutes(s.start_time), _time_to_minutes(s.end_time)
    if end <= start: return None, []
    breaks = []
    for a,b in getattr(project, "breaks", []) or []:
        try:
            x,y = _time_to_minutes(a), _time_to_minutes(b)
        except Exception: continue
        if y > x: breaks.append((x,y))
    return (start,end), breaks


def _required_frequency(project, room):
    return max(1, int(get_effective_frequency(room.room_type, getattr(project, "weather_factor", 1.0))))


def _get_overtime_limit(project):
    try: return _time_to_minutes(getattr(project, "overtime_limit", "23:00"))
    except Exception: return 23*60


def _actual_overtime_minutes(task, shift_end):
    if not getattr(task, "is_overtime", False): return 0.0
    boundary = task.start_dt.replace(hour=shift_end//60, minute=shift_end%60, second=0, microsecond=0)
    return max(0.0, (task.end_dt - max(task.start_dt, boundary)).total_seconds()/60.0)


def validate_schedule(project: Project, schedule_date=None) -> Dict[str, Any]:
    day = _schedule_day(project, schedule_date)
    period = [day] if day else []
    result = {
        "valid": True, "schedule_date": day, "period_days": 1 if period else 0,
        "period_start": period[0] if period else None, "period_end": period[-1] if period else None,
        "rooms_total": 0, "active_rooms": 0, "disabled_rooms": 0,
        "scheduled_rooms": 0, "unscheduled_rooms": 0, "duplicate_assignments": 0,
        "tasks_total": 0, "scheduled_tasks": 0, "expected_tasks": 0, "missing_cleanings": 0,
        "wrong_room_tasks": 0, "wrong_employee_tasks": 0, "outside_period_tasks": 0,
        "time_conflicts": 0, "transit_violations": 0, "break_violations": 0,
        "out_of_shift_tasks": 0, "overtime_tasks": 0, "overtime_minutes": 0.0,
        "overtime_limit_violations": 0, "negative_duration_tasks": 0,
        "frequency_required": 0, "frequency_scheduled": 0, "frequency_violations": 0,
        "cleaning_minutes": 0.0, "transit_minutes": 0.0, "total_minutes": 0.0, "total_hours": 0.0,
        "employees": max(1, int(getattr(project, "employees_count", 1))),
        "employee_loads": {}, "employee_idle": {}, "underutilized_employees": [],
        "cost": 0.0, "missed_rooms": [], "unscheduled_room_keys": [],
        "conflict_details": [], "transit_violation_details": [], "break_violation_details": [],
        "out_of_shift_details": [], "overtime_limit_details": [], "frequency_details": [],
        "overtime_details": [], "warnings": [], "violations_summary": "",
        "scheduled_room_keys": [],
    }
    if not period:
        result["valid"] = False; result["warnings"].append("Некорректный период проекта")
        result["violations_summary"] = "❌ Некорректный период проекта"; return result

    all_rooms = project.all_rooms()
    active_rooms = [r for r in all_rooms if not getattr(r, "disabled", False)]
    result["rooms_total"] = len(all_rooms); result["active_rooms"] = len(active_rooms); result["disabled_rooms"] = len(all_rooms)-len(active_rooms)

    shift, breaks = _shift_info(project)
    if shift is None:
        result["valid"] = False; result["warnings"].append("Нет корректных настроек смены")
        result["violations_summary"] = "❌ Нет корректных настроек смены"; return result
    shift_start, shift_end = shift
    windows = _regular_windows_from_shift(shift_start, shift_end, breaks)
    regular_capacity = sum(b-a for a,b in windows)
    overtime_limit = _get_overtime_limit(project)
    if overtime_limit <= shift_end: overtime_limit = shift_end

    tasks = list(getattr(project, "cleaning_tasks", []) or [])
    result["tasks_total"] = result["scheduled_tasks"] = len(tasks)
    tasks_by_emp = defaultdict(list)
    active_keys = {(fi, r.id) for fi,f in enumerate(project.floors) for r in f.rooms if not getattr(r, "disabled", False)}
    valid_task_keys = set()

    for task in tasks:
        emp = int(getattr(task, "employee", -1))
        if not (0 <= emp < result["employees"]): result["wrong_employee_tasks"] += 1; result["valid"] = False
        else: tasks_by_emp[emp].append(task)
        fi = int(getattr(task, "floor_index", -1)); rid = int(getattr(task, "room_id", -1))
        if (fi,rid) not in active_keys:
            result["wrong_room_tasks"] += 1; result["valid"] = False
        else: valid_task_keys.add((fi,rid))
        if task.start_dt.date() not in period:
            result["outside_period_tasks"] += 1; result["valid"] = False

    for emp, emp_tasks in tasks_by_emp.items():
        ordered = sorted(emp_tasks, key=lambda t:t.start_dt)
        for a,b in zip(ordered, ordered[1:]):
            if a.start_dt.date()!=b.start_dt.date(): continue
            if a.end_dt > b.start_dt:
                result["time_conflicts"] += 1; result["valid"] = False
                result["conflict_details"].append(f"Сотрудник {emp+1}: {a.start_dt:%H:%M}–{a.end_dt:%H:%M} и {b.start_dt:%H:%M}–{b.end_dt:%H:%M}")
            if a.end_dt <= b.start_dt:
                prev_room = _get_room_for_task(project,a)
                required = _get_transit_minutes(getattr(prev_room,"room_type","") if prev_room else "", a.floor_index, b.floor_index)
                gap = (b.start_dt-a.end_dt).total_seconds()/60
                if gap + 1e-9 < required:
                    result["transit_violations"] += 1; result["valid"] = False
                    result["transit_violation_details"].append(f"Сотрудник {emp+1}: №{a.room_id+1} (этаж {a.floor_index+1}) → №{b.room_id+1} (этаж {b.floor_index+1}): требуется {required} мин., фактически {gap:.1f} мин.")
                if int(getattr(b,"transit_before_minutes",0)) < required:
                    result["transit_violations"] += 1; result["valid"] = False
                    result["transit_violation_details"].append(f"Сотрудник {emp+1}: metadata transit_before для №{b.room_id+1} меньше нормы")

    for task in tasks:
        duration = (task.end_dt-task.start_dt).total_seconds()/60
        if duration <= 0:
            result["negative_duration_tasks"] += 1; result["valid"] = False
        if task.start_dt.date() not in period: continue
        sm = task.start_dt.hour*60+task.start_dt.minute; em = task.end_dt.hour*60+task.end_dt.minute
        if getattr(task,"is_overtime",False):
            result["overtime_tasks"] += 1
            ot = _actual_overtime_minutes(task, shift_end); result["overtime_minutes"] += ot
            result["overtime_details"].append(f"Сотрудник {task.employee+1}: №{task.room_id+1}, {task.start_dt:%H:%M}–{task.end_dt:%H:%M}")
            if em > overtime_limit:
                result["overtime_limit_violations"] += 1; result["valid"] = False
                result["overtime_limit_details"].append(f"Сотрудник {task.employee+1}: №{task.room_id+1} заканчивает {task.end_dt:%H:%M}, лимит {overtime_limit//60:02d}:{overtime_limit%60:02d}")
        else:
            if sm < shift_start or em > shift_end:
                result["out_of_shift_tasks"] += 1; result["valid"] = False
                result["out_of_shift_details"].append(f"Сотрудник {task.employee+1}: №{task.room_id+1}, {task.start_dt:%H:%M}–{task.end_dt:%H:%M} вне смены")
        for bs,be in breaks:
            if sm < be and em > bs:
                result["break_violations"] += 1; result["valid"] = False
                result["break_violation_details"].append(f"Сотрудник {task.employee+1}: №{task.room_id+1}, {task.start_dt:%H:%M}–{task.end_dt:%H:%M} пересекает обед")

    result["scheduled_rooms"] = len(active_keys & valid_task_keys)
    missing_rooms = active_keys - valid_task_keys
    result["unscheduled_rooms"] = len(missing_rooms); result["unscheduled_room_keys"] = [list(k) for k in sorted(missing_rooms)]
    if missing_rooms:
        result["valid"] = False
        for fi,rid in sorted(missing_rooms):
            room = next((r for r in project.floors[fi].rooms if r.id==rid),None)
            if room: result["missed_rooms"].append(f"{project.floors[fi].name}: {room.name} (№{room.id+1}, {room.area_m2:.1f} м²)")

    assignments = defaultdict(set)
    for t in tasks: assignments[(t.start_dt.date(),t.floor_index,t.room_id)].add(t.employee)
    # Different employees may legitimately perform separate daily occurrences
    # of the same room when its normative frequency is greater than one.
    # A duplicate is therefore only an over-allocation beyond the required
    # number of occurrences on the same calendar day.
    counts = defaultdict(int)
    for t in tasks:
        counts[(t.start_dt.date(),t.floor_index,t.room_id)] += 1
    room_lookup = {(fi, r.id): r for fi, f in enumerate(project.floors) for r in f.rooms}
    for (day, fi, rid), assigned_emps in assignments.items():
        room = room_lookup.get((fi, rid))
        required = _required_frequency(project, room) if room is not None else 1
        if counts[(day, fi, rid)] > required:
            result["duplicate_assignments"] += 1
    if result["duplicate_assignments"]: result["valid"] = False

    for room in active_rooms:
        fi = next((i for i,f in enumerate(project.floors) if room in f.rooms),0)
        required = _required_frequency(project,room)
        for day in period:
            actual = counts.get((day,fi,room.id),0)
            result["frequency_required"] += required; result["frequency_scheduled"] += actual
            if actual != required:
                result["frequency_violations"] += 1; result["frequency_details"].append(f"{day:%d.%m.%Y} — {project.floors[fi].name}: {room.name} (№{room.id+1}) требуется {required}, запланировано {actual}")
    if result["frequency_violations"]: result["valid"] = False

    total_cleaning = total_transit = 0.0
    for emp in range(result["employees"]):
        emp_tasks = sorted(tasks_by_emp.get(emp,[]), key=lambda t:t.start_dt)
        clean = sum(max(0.0,(t.end_dt-t.start_dt).total_seconds()/60) for t in emp_tasks)
        transit = sum(max(0.0,float(getattr(t,"transit_before_minutes",0))) for t in emp_tasks)
        total_cleaning += clean; total_transit += transit
        regular_clean = sum(max(0.0,(t.end_dt-t.start_dt).total_seconds()/60-_actual_overtime_minutes(t,shift_end)) for t in emp_tasks)
        idle = max(0.0, regular_capacity*len(period)-regular_clean)
        result["employee_loads"][emp]=round(clean,1); result["employee_idle"][emp]=round(idle,1)
        if emp_tasks:
            result["underutilized_employees"].append({"employee":emp,"idle_minutes":round(idle,1),"utilization_percent":round(regular_clean/(regular_capacity*len(period))*100,1) if regular_capacity else 0.0})
    result["cleaning_minutes"]=round(total_cleaning,1); result["transit_minutes"]=round(total_transit,1); result["total_minutes"]=round(total_cleaning+total_transit,1); result["total_hours"]=round(result["total_minutes"]/60,2)
    expected = sum(_required_frequency(project,r)*len(period) for r in active_rooms)
    result["expected_tasks"]=expected; result["missing_cleanings"]=max(0,expected-result["scheduled_tasks"])
    if result["scheduled_tasks"] != expected: result["valid"] = False

    try:
        from cost_calculator import _actual_cost_components
        result["cost"] = round(_actual_cost_components(project)["total_cost"],2)
    except Exception as exc:
        result["warnings"].append(f"Не удалось вычислить стоимость: {exc}")

    summary=[]
    if result["valid"]: summary.append("✅ Расписание физически и нормативно выполнимо")
    else:
        summary.append("❌ Обнаружены нарушения:")
        mapping=[("time_conflicts","Пересечения задач"),("transit_violations","Нарушения переходов"),("break_violations","Пересечения обеда"),("out_of_shift_tasks","Задачи вне смены"),("overtime_limit_violations","Превышение лимита переработки"),("frequency_violations","Нарушения частоты"),("unscheduled_rooms","Не запланированные помещения"),("missing_cleanings","Пропущенные уборки"),("duplicate_assignments","Дублирующие назначения"),("wrong_room_tasks","Неверные помещения"),("outside_period_tasks","Задачи вне периода")]
        for key,label in mapping:
            if result.get(key): summary.append(f"  • {label}: {result[key]}")
        for key,title in (("frequency_details","Детали частоты"),("transit_violation_details","Детали переходов"),("conflict_details","Детали пересечений"),("overtime_limit_details","Детали переработки")):
            if result[key]:
                summary.append(f"  {title}:")
                for d in result[key][:5]: summary.append(f"    - {d}")
                if len(result[key])>5: summary.append(f"    ... и ещё {len(result[key])-5}")
    result["violations_summary"]="\n".join(summary)
    result["rooms_active"] = result["active_rooms"]
    result["rooms_scheduled"] = result["scheduled_rooms"]
    result["total_tasks"] = result["tasks_total"]
    result["scheduled_room_keys"] = sorted(valid_task_keys)
    result["unscheduled_tasks"] = result["missing_cleanings"]
    return result
