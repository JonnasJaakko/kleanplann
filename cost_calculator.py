"""Расчёт трудоёмкости, штатности и оплаты KleanPlann.

Все решения по штату подтверждаются фактическим запуском scheduler + validator,
а не только делением минут. Это позволяет учитывать частоту, обед, переходы,
географию зон и конкретные ограничения смены.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict

from sanitarnorm import get_cleaning_time_minutes, get_effective_frequency


def _freq(project, room):
    return max(1, int(get_effective_frequency(room.room_type, getattr(project, "weather_factor", 1.0))))


def _time(project, room):
    return max(1, int(math.ceil(get_cleaning_time_minutes(
        room.room_type,
        room.area_m2,
        getattr(project, "weather_factor", 1.0),
        getattr(project, "cleaning_type", "поддерживающая"),
    ))))


def _shift_bounds(project):
    if not getattr(project, "shifts", None):
        return 9 * 60, 19 * 60
    a = [int(x) for x in project.shifts[0].start_time.split(":")]
    b = [int(x) for x in project.shifts[0].end_time.split(":")]
    return a[0] * 60 + a[1], b[0] * 60 + b[1]


def _shift_minutes(project):
    start, end = _shift_bounds(project)
    total = max(0, end - start)
    for bs, be in getattr(project, "breaks", []) or []:
        try:
            x, y = map(int, bs.split(":")); u, v = map(int, be.split(":"))
            total -= max(0, min(end, u * 60 + v) - max(start, x * 60 + y))
        except Exception:
            continue
    return max(1, total)


def _active_rooms(project):
    return [r for r in project.all_rooms() if not getattr(r, "disabled", False)]


def _daily_norm_minutes(project):
    return sum(_time(project, r) * _freq(project, r) for r in _active_rooms(project))


def _estimate_transit_minutes(project, employee_count=1):
    """Консервативная нижняя оценка переходов для staffing bound."""
    rooms = _active_rooms(project)
    daily_visits = sum(_freq(project, r) for r in rooms)
    if daily_visits <= employee_count:
        return float(max(0, daily_visits - employee_count))
    same_floor = max(0, daily_visits - len(project.floors))
    floor_changes = max(0, len(project.floors) - 1) * employee_count
    return float(same_floor * 1 + floor_changes * 2)


def _build_hypothetical_zones(project, employees):
    try:
        from zone_manager import distribute_project_zones
        return distribute_project_zones(project, employees, getattr(project, "priority_mode", "balanced"))
    except Exception:
        return []


def _candidate_is_feasible(project, employees):
    from scheduler import schedule_single_shift
    from schedule_validator import validate_schedule

    candidate = copy.deepcopy(project)
    candidate.employees_count = int(employees)
    candidate.employee_names = [f"Сотрудник {i + 1}" for i in range(employees)]
    candidate.manual_assignments = {}
    candidate.zones = _build_hypothetical_zones(candidate, employees)
    candidate.end_date = candidate.start_date

    result = schedule_single_shift(
        candidate,
        target_date=candidate.start_date,
        employees=employees,
        allow_partial_schedule=False,
    )
    candidate.cleaning_tasks = list(result.get("tasks", []))
    validation = validate_schedule(candidate, schedule_date=candidate.start_date)
    zero_overtime = int(validation.get("overtime_tasks", 0)) == 0 and float(validation.get("overtime_minutes", 0.0)) <= 0.1
    feasible = bool(validation.get("valid")) and bool(result.get("feasible"))
    return feasible, result, validation


def _room(project, fi, rid):
    if 0 <= fi < len(project.floors):
        return next((r for r in project.floors[fi].rooms if r.id == rid), None)
    return None


def _employee_metrics(project):
    result = defaultdict(lambda: {
        "tasks": [], "minutes": 0.0, "regular_minutes": 0.0,
        "overtime": 0.0, "area": 0.0, "rooms": set(), "days": set(), "transit_minutes": 0.0,
    })
    _, shift_end = _shift_bounds(project)
    for task in getattr(project, "cleaning_tasks", []) or []:
        m = result[int(task.employee)]
        cleaning = max(0.0, (task.end_dt - task.start_dt).total_seconds() / 60)
        transit = max(0.0, float(getattr(task, "transit_before_minutes", 0)))
        overtime = 0.0
        if getattr(task, "is_overtime", False):
            boundary = task.start_dt.replace(hour=shift_end // 60, minute=shift_end % 60, second=0, microsecond=0)
            overtime = max(0.0, (task.end_dt - max(task.start_dt, boundary)).total_seconds() / 60)
        m["tasks"].append(task)
        m["minutes"] += cleaning
        m["transit_minutes"] += transit
        m["overtime"] += overtime
        m["regular_minutes"] += max(0.0, cleaning - overtime)
        m["rooms"].add((task.floor_index, task.room_id))
        m["days"].add(task.start_dt.date())
    for m in result.values():
        m["area"] = sum((_room(project, fi, rid).area_m2 if _room(project, fi, rid) else 0.0) for fi, rid in m["rooms"])
    return dict(result)


def _actual_cost_components(project):
    tasks = list(getattr(project, "cleaning_tasks", []) or [])
    salary_type = getattr(project, "salary_type", "hour")
    value = float(getattr(project, "salary_value", getattr(project, "hourly_rate", 200.0)))
    premium_type = getattr(project, "overtime_type", "percent")
    premium_value = float(getattr(project, "overtime_value", getattr(project, "overtime_premium_percent", 50.0)))
    _, shift_end = _shift_bounds(project)

    cleaning_minutes = transit_minutes = overtime_minutes = 0.0
    employee_days = set()
    regular_cost = overtime_cost = 0.0
    per_sqm_base = 0.0

    for task in tasks:
        cleaning = max(0.0, (task.end_dt - task.start_dt).total_seconds() / 60)
        transit = max(0.0, float(getattr(task, "transit_before_minutes", 0)))
        cleaning_minutes += cleaning
        transit_minutes += transit
        employee_days.add((int(task.employee), task.start_dt.date()))
        overtime = 0.0
        if getattr(task, "is_overtime", False):
            boundary = task.start_dt.replace(hour=shift_end // 60, minute=shift_end % 60, second=0, microsecond=0)
            overtime = max(0.0, (task.end_dt - max(task.start_dt, boundary)).total_seconds() / 60)
        overtime_minutes += overtime
        regular_work = max(0.0, cleaning - overtime) + (transit if not getattr(task, "is_overtime", False) else max(0.0, transit))

        room = _room(project, task.floor_index, task.room_id)
        if salary_type == "hour":
            regular_cost += regular_work / 60.0 * value
            if overtime:
                overtime_rate = value + premium_value if premium_type == "per_hour" else value * (1 + premium_value / 100.0)
                overtime_cost += overtime / 60.0 * overtime_rate
        elif salary_type == "per_sqm":
            base = (room.area_m2 if room else 0.0) * value
            per_sqm_base += base
            if overtime:
                overtime_cost += base * (premium_value / 100.0) if premium_type == "percent" else overtime / 60.0 * premium_value
        # fixed_shift is accounted once per employee/day below.

    if salary_type == "fixed_shift":
        regular_cost = len(employee_days) * value
        shift_hours = _shift_minutes(project) / 60.0
        hourly_equiv = value / shift_hours if shift_hours else value
        overtime_cost = overtime_minutes / 60.0 * (
            premium_value if premium_type == "per_hour" else hourly_equiv * (1 + premium_value / 100.0)
        )
    elif salary_type == "per_sqm":
        regular_cost = per_sqm_base

    return {
        "cleaning_minutes": cleaning_minutes,
        "transit_minutes": transit_minutes,
        "overtime_minutes": overtime_minutes,
        "regular_cost": regular_cost,
        "overtime_cost": overtime_cost,
        "total_cost": regular_cost + overtime_cost,
    }


def _candidate_total_cost(project):
    """Стоимость уже построенного кандидата без повторного расчёта штатности."""
    return float(_actual_cost_components(project)["total_cost"])


def estimate_required_employees(project):
    active = _active_rooms(project)
    daily = _daily_norm_minutes(project)
    capacity = _shift_minutes(project)
    if not active:
        return {
            "employees": 1,
            "daily_minutes": 0.0,
            "capacity_minutes": float(capacity),
            "minimum_by_capacity": 1,
            "minimum_feasible": 1,
            "zero_overtime_employees": 1,
            "feasible": True,
            "tested": {1: True},
            "diagnostics": {},
            "reason": "Нет активных помещений",
        }

    conservative_per_employee = max(1.0, capacity * 0.90)
    lower = max(1, math.ceil((daily + _estimate_transit_minutes(project, 1)) / conservative_per_employee))
    upper = min(20, max(lower + 8, lower))

    tested = {}
    diagnostics = {}
    candidates = []
    first_feasible = None
    zero_overtime = None

    for employees in range(lower, upper + 1):
        try:
            ok, result, validation = _candidate_is_feasible(project, employees)
            candidate = copy.deepcopy(project)
            candidate.employees_count = employees
            candidate.employee_names = [f"Сотрудник {i+1}" for i in range(employees)]
            candidate.manual_assignments = {}
            candidate.zones = _build_hypothetical_zones(candidate, employees)
            candidate.cleaning_tasks = list(result.get("tasks", []))
            candidate_cost = _candidate_total_cost(candidate) if ok else float("inf")
            overtime = float(validation.get("overtime_minutes", 0.0))
            diagnostics[employees] = {
                "missed_cleanings": result.get("missed_cleanings", 0),
                "transit_violations": validation.get("transit_violations", 0),
                "time_conflicts": validation.get("time_conflicts", 0),
                "frequency_violations": validation.get("frequency_violations", 0),
                "overtime_minutes": overtime,
                "cleaning_minutes": result.get("cleaning_minutes", 0.0),
                "transit_minutes": result.get("transit_minutes", 0.0),
                "candidate_cost": round(candidate_cost, 2) if math.isfinite(candidate_cost) else None,
            }
            if ok:
                tested[employees] = True
                if first_feasible is None:
                    first_feasible = employees
                if overtime <= 0.1 and zero_overtime is None:
                    zero_overtime = employees
                candidates.append((candidate_cost, overtime, employees))
            else:
                tested[employees] = False
        except Exception as exc:
            tested[employees] = False
            diagnostics[employees] = {"error": str(exc)}

    if candidates:
        # Организационная рекомендация: не добавляем людей только ради экономии
        # нескольких минут переходов в почасовой модели. Сначала добиваемся
        # выполнимости и приемлемой переработки, затем минимального штата.
        overtime_threshold = max(30.0, round(capacity * 0.05))
        acceptable_ot = [c for c in candidates if c[1] <= overtime_threshold]
        if acceptable_ot:
            recommended = min(acceptable_ot, key=lambda x: (x[2], x[1], x[0]))[2]
            basis = f"минимальный штат с переработкой не более {overtime_threshold:.0f} мин."
        else:
            min_ot = min(candidates, key=lambda x: (x[1], x[2], x[0]))
            recommended = min_ot[2]
            basis = "минимальный штат; приоритет у меньшей переработки"
        cheapest = min(candidates, key=lambda x: (x[0], x[1], x[2]))[2]
        return {
            "employees": recommended,
            "daily_minutes": round(daily, 1),
            "capacity_minutes": round(capacity, 1),
            "minimum_by_capacity": lower,
            "minimum_feasible": first_feasible,
            "zero_overtime_employees": zero_overtime,
            "cheapest_employees": cheapest,
            "overtime_threshold_minutes": overtime_threshold,
            "feasible": True,
            "tested": tested,
            "diagnostics": diagnostics,
            "reason": f"Рекомендован {basis}. Наименьшая прямая стоимость у варианта {cheapest} чел., но он не выбирается автоматически только ради экономии на переходах.",
        }

    # Не выдаём 30–50 сотрудников из-за отказа эвристики.
    best = min(
        diagnostics or {lower: {}},
        key=lambda n: (
            diagnostics.get(n, {}).get("missed_cleanings", 10**9),
            diagnostics.get(n, {}).get("overtime_minutes", 10**9),
            diagnostics.get(n, {}).get("transit_violations", 10**9),
            n,
        ),
    )
    return {
        "employees": best,
        "daily_minutes": round(daily, 1),
        "capacity_minutes": round(capacity, 1),
        "minimum_by_capacity": lower,
        "minimum_feasible": None,
        "zero_overtime_employees": None,
        "feasible": False,
        "tested": tested,
        "diagnostics": diagnostics,
        "reason": "В проверенном диапазоне нет полностью выполнимого варианта; увеличение штата выше диапазона автоматически не производится",
    }


def calculate_cost(project):
    tasks = list(getattr(project, "cleaning_tasks", []) or [])
    if tasks:
        actual = _actual_cost_components(project)
    else:
        actual = {
            "cleaning_minutes": _daily_norm_minutes(project),
            "transit_minutes": 0.0,
            "overtime_minutes": 0.0,
            "regular_cost": 0.0,
            "overtime_cost": 0.0,
            "total_cost": 0.0,
        }

    needed_info = estimate_required_employees(project)
    needed = int(needed_info["employees"])
    salary_type = getattr(project, "salary_type", "hour")
    value = float(getattr(project, "salary_value", getattr(project, "hourly_rate", 200.0)))
    shift_hours = _shift_minutes(project) / 60.0

    if salary_type == "hour":
        hire_cost = needed * shift_hours * value
    elif salary_type == "fixed_shift":
        hire_cost = needed * value
    else:
        hire_cost = sum(r.area_m2 * _freq(project, r) * value for r in _active_rooms(project))

    metrics = _employee_metrics(project)
    premium_type = getattr(project, "overtime_type", "percent")
    premium_value = float(getattr(project, "overtime_value", getattr(project, "overtime_premium_percent", 50.0)))
    employee_pay, employee_base_pay, employee_overtime_pay = {}, {}, {}
    shift_end = _shift_bounds(project)[1]

    for i in range(int(getattr(project, "employees_count", 1))):
        m = metrics.get(i, {"minutes": 0.0, "overtime": 0.0, "transit_minutes": 0.0, "days": set(), "area": 0.0})
        regular_minutes = max(0.0, m.get("minutes", 0.0) - m.get("overtime", 0.0)) + m.get("transit_minutes", 0.0)
        if salary_type == "hour":
            base = regular_minutes / 60.0 * value
            extra = m.get("overtime", 0.0) / 60.0 * (value + premium_value if premium_type == "per_hour" else value * (1 + premium_value / 100.0))
        elif salary_type == "fixed_shift":
            base = len(m.get("days", set())) * value
            hourly_equiv = value / shift_hours if shift_hours else value
            extra = m.get("overtime", 0.0) / 60.0 * (premium_value if premium_type == "per_hour" else hourly_equiv * (1 + premium_value / 100.0))
        else:
            base = sum((_room(project, fi, rid).area_m2 if _room(project, fi, rid) else 0.0) * value for fi, rid in m.get("rooms", set()))
            extra = m.get("overtime", 0.0) / 60.0 * premium_value if premium_type == "per_hour" else (base * premium_value / 100.0 if m.get("overtime", 0.0) else 0.0)
        employee_base_pay[i] = round(base, 2)
        employee_overtime_pay[i] = round(extra, 2)
        employee_pay[i] = round(base + extra, 2)

    total_minutes = actual["cleaning_minutes"] + actual["transit_minutes"]
    current_cost = sum(employee_pay.values())
    current_workers = int(getattr(project, "employees_count", 1))
    if needed > current_workers:
        recommendation = f"нанять ещё {needed - current_workers} чел."
    elif needed < current_workers:
        recommendation = f"сократить штат до {needed}"
    else:
        recommendation = "оставить штат"

    return {
        "total_time_hours": round(total_minutes / 60, 2),
        "total_minutes": round(total_minutes, 1),
        "cleaning_minutes": round(actual["cleaning_minutes"], 1),
        "transit_minutes": round(actual["transit_minutes"], 1),
        "staff_count": current_workers,
        "staff_hours": round(current_workers * shift_hours, 2),
        "overtime_hours": round(actual["overtime_minutes"] / 60, 2),
        "cost_with_overtime": round(current_cost, 2),
        "needed_employees": needed,
        "staffing_feasible": bool(needed_info.get("feasible")),
        "staffing_reason": needed_info.get("reason", ""),
        "staffing_minimum_by_capacity": needed_info.get("minimum_by_capacity", needed),
        "staffing_minimum_feasible": needed_info.get("minimum_feasible"),
        "staffing_zero_overtime": needed_info.get("zero_overtime_employees"),
        "staffing_cheapest": needed_info.get("cheapest_employees"),
        "staffing_overtime_threshold": needed_info.get("overtime_threshold_minutes", 30),
        "staffing_tested": needed_info.get("tested", {}),
        "staffing_diagnostics": needed_info.get("diagnostics", {}),
        "cost_hire": round(hire_cost, 2),
        "recommendation": recommendation,
        "salary_type": salary_type,
        "salary_value": value,
        "overtime_type": premium_type,
        "overtime_value": premium_value,
        "employee_metrics": metrics,
        "employee_pay": employee_pay,
        "employee_base_pay": employee_base_pay,
        "employee_overtime_pay": employee_overtime_pay,
    }
