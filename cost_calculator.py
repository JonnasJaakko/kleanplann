"""
Расчёт стоимости и практической потребности в персонале.

Расчёт больше не использует простое ceil(total_hours / shift_hours)
для рекомендации штата. Для каждого варианта численности запускается
тот же scheduler и проверяется, сколько уборок пришлось вынести
за пределы смены.
"""
from typing import Dict, Any
import copy
import math

from project import Project
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day


def _get_effective_freq(room_type: str, weather: float) -> int:
    return max(
        1,
        int(round(get_frequency_per_day(room_type) * weather))
    )


def _get_shift_minutes(project: Project) -> int:
    if not project.shifts:
        return 480

    shift = project.shifts[0]
    start_h, start_m = map(int, shift.start_time.split(":"))
    end_h, end_m = map(int, shift.end_time.split(":"))
    total = max(0, (end_h * 60 + end_m) - (start_h * 60 + start_m))

    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs_h, bs_m = map(int, b_start.split(":"))
            be_h, be_m = map(int, b_end.split(":"))
            total -= max(
                0,
                min(end_h * 60 + end_m, be_h * 60 + be_m)
                - max(start_h * 60 + start_m, bs_h * 60 + bs_m)
            )
        except Exception:
            continue

    return max(1, total)


def _daily_norm_minutes(project: Project) -> float:
    total = 0.0
    weather = getattr(project, "weather_factor", 1.0)

    for room in project.all_rooms():
        if getattr(room, "disabled", False):
            continue
        total += (
            get_cleaning_time_minutes(
                room.room_type,
                room.area_m2,
                weather,
                getattr(project, "cleaning_type", "поддерживающая")
            )
            * _get_effective_freq(room.room_type, weather)
        )
    return total


def estimate_required_employees(project: Project) -> Dict[str, Any]:
    """
    Быстрая нижняя оценка. Для окончательной рекомендации используется
    симуляция scheduler в calculate_cost().
    """
    active = [
        r for r in project.all_rooms()
        if not getattr(r, "disabled", False)
    ]
    if not active:
        return {
            "employees": 1,
            "daily_minutes": 0.0,
            "capacity_minutes": float(_get_shift_minutes(project)),
        }

    daily = _daily_norm_minutes(project)
    capacity = _get_shift_minutes(project)

    # 10% практический резерв на переходы/мелкие задержки.
    effective_capacity = capacity * 0.90
    employees = max(1, math.ceil(daily / effective_capacity))

    return {
        "employees": employees,
        "daily_minutes": round(daily, 1),
        "capacity_minutes": round(effective_capacity, 1),
    }


def _schedule_metrics(project, employees: int) -> Dict[str, Any]:
    from scheduler import schedule_single_shift

    p = copy.deepcopy(project)
    p.employees_count = employees
    p.employee_names = [
        f"Сотрудник {i + 1}" for i in range(employees)
    ]
    # При сравнении вариантов численности старые зоны нельзя считать
    # жёстким ограничением: иначе проект на 6 зонах никогда не сможет
    # честно оценить вариант на 4 сотрудника.
    p.zones = []
    result = schedule_single_shift(p, employees)

    tasks = result["tasks"]
    cleaning = sum(
        (t.end_dt - t.start_dt).total_seconds() / 60.0
        for t in tasks
    )
    overtime = sum(
        (t.end_dt - t.start_dt).total_seconds() / 60.0
        for t in tasks
        if getattr(t, "is_overtime", False)
    )
    employees_used = {
        t.employee for t in tasks
    }

    return {
        "employees": employees,
        "tasks": len(tasks),
        "cleaning_minutes": cleaning,
        "overtime_minutes": overtime,
        "employees_used": len(employees_used),
        "feasible": bool(result.get("feasible", False)),
        "result": result,
    }


def calculate_cost(project: Project) -> Dict[str, Any]:
    rate = float(getattr(project, "hourly_rate", 200.0))
    staff = max(1, int(getattr(project, "employees_count", 1)))
    shift_minutes = _get_shift_minutes(project)
    shift_hours = shift_minutes / 60.0

    tasks = list(getattr(project, "cleaning_tasks", []) or [])

    if tasks:
        cleaning_minutes = sum(
            (t.end_dt - t.start_dt).total_seconds() / 60.0
            for t in tasks
        )

        # Переходы — часть рабочего времени сотрудника, а не "пустая"
        # разница между двумя задачами.
        transit_minutes = 0.0
        by_emp = {}
        for t in tasks:
            by_emp.setdefault(t.employee, []).append(t)
        for emp_tasks in by_emp.values():
            ordered = sorted(emp_tasks, key=lambda x: x.start_dt)
            for t in ordered[:-1]:
                transit_minutes += max(
                    0,
                    int(getattr(t, "transit_after_minutes", 0))
                )

        total_minutes = cleaning_minutes + transit_minutes

        overtime_minutes = 0.0
        for t in tasks:
            duration = (t.end_dt - t.start_dt).total_seconds() / 60.0
            if getattr(t, "is_overtime", False):
                overtime_minutes += duration
                # Переход после сверхурочной задачи также оплачиваем,
                # если он явно записан scheduler-ом.
                overtime_minutes += max(
                    0,
                    int(getattr(t, "transit_after_minutes", 0))
                )
    else:
        cleaning_minutes = _daily_norm_minutes(project)
        transit_minutes = 0.0
        total_minutes = cleaning_minutes
        overtime_minutes = 0.0

    regular_minutes = max(0.0, total_minutes - overtime_minutes)

    # В стоимость входит фактически выполненная уборка.
    # Сверхурочные оплачиваются с коэффициентом 1.5.
    cost_regular = regular_minutes / 60.0 * rate
    premium = max(0.0, float(getattr(project, "overtime_premium_percent", 50.0))) / 100.0
    cost_overtime = overtime_minutes / 60.0 * rate * (1.0 + premium)
    cost_with_overtime = cost_regular + cost_overtime

    staff_hours = staff * shift_hours
    full_staff_cost = staff_hours * rate

    # Ищем минимальный штат, при котором scheduler укладывает все
    # уборки в обычную смену без overtime.
    quick = estimate_required_employees(project)
    start_n = 1
    upper = max(
        staff * 2,
        quick["employees"] + 3,
        1
    )

    feasible_without_overtime = None
    simulations = []

    for n in range(start_n, upper + 1):
        metrics = _schedule_metrics(project, n)
        simulations.append(metrics)

        # Небольшая переработка неизбежна из-за технологических
        # ограничений и не должна автоматически считаться причиной
        # увеличения штата. До 15 минут в сутки считаем допустимым
        # практическим запасом.
        if (
            metrics["feasible"]
            and metrics["overtime_minutes"] <= 15.0
            and metrics["employees_used"] == n
        ):
            feasible_without_overtime = n
            break

    if feasible_without_overtime is None:
        feasible_without_overtime = upper

    needed_emp = feasible_without_overtime
    cost_hire = needed_emp * shift_hours * rate

    if needed_emp < staff:
        savings = full_staff_cost - cost_hire
        recommendation = (
            f"сократить штат до {needed_emp} "
            f"(экономия {savings:.0f} руб./день)"
        )
    elif needed_emp > staff:
        extra = needed_emp - staff
        recommendation = (
            f"нанять ещё {extra} чел. "
            f"(до {needed_emp})"
        )
    elif overtime_minutes > 0:
        recommendation = (
            f"оставить {staff} чел. и допустить "
            f"{overtime_minutes:.0f} мин переработки"
        )
    else:
        recommendation = "оставить штат"

    return {
        "total_time_hours": round(total_minutes / 60.0, 2),
        "staff_count": staff,
        "staff_hours": round(staff_hours, 2),
        "overtime_hours": round(overtime_minutes / 60.0, 2),
        "cost_with_overtime": round(cost_with_overtime, 2),
        "needed_employees": needed_emp,
        "cost_hire": round(cost_hire, 2),
        "recommendation": recommendation,
        "total_minutes": round(total_minutes, 1),
        "shift_hours": round(shift_hours, 2),
        "overtime_premium_percent": round(float(getattr(project, "overtime_premium_percent", 50.0)), 2),
        "overtime_premium_percent": round(float(getattr(project, "overtime_premium_percent", 50.0)), 2),
        "simulations": [
            {
                "employees": x["employees"],
                "overtime_minutes": round(x["overtime_minutes"], 1),
                "tasks": x["tasks"],
                "employees_used": x["employees_used"],
            }
            for x in simulations
        ],
    }
