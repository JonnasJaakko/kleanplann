"""
Калькулятор стоимости — единая модель трудоёмкости из sanitarnorm.

Использует ТОЛЬКО:
  sanitarnorm.get_cleaning_time_minutes()
  sanitarnorm.get_frequency_per_day()

Никакой независимой модели трудоёмкости.
Никакого SHIFT_HOURS / frequency.
"""
from typing import Dict, Any, List, Optional
from project import Project, Room
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day
import math


def _get_effective_freq(room_type: str, weather: float) -> int:
    """Возвращает частоту уборки с учётом погодного коэффициента."""
    freq = get_frequency_per_day(room_type)
    return max(1, int(round(freq * weather)))


def _get_shift_minutes(project: Project) -> int:
    """Доступное время смены с вычетом обеда (минуты)."""
    shift_minutes = 0
    for shift in project.shifts:
        try:
            start_h, start_m = map(int, shift.start_time.split(':'))
            end_h, end_m = map(int, shift.end_time.split(':'))
            shift_minutes += max(0, (end_h * 60 + end_m) - (start_h * 60 + start_m))
        except (ValueError, AttributeError):
            continue
    # Вычитаем обеденные перерывы
    for b_start, b_end in project.breaks:
        try:
            bs_h, bs_m = map(int, b_start.split(':'))
            be_h, be_m = map(int, b_end.split(':'))
            break_min = max(0, (be_h * 60 + be_m) - (bs_h * 60 + bs_m))
            shift_minutes = max(0, shift_minutes - break_min)
        except (ValueError, AttributeError):
            pass
    return max(1, shift_minutes or 480)


def estimate_required_employees(project: Project) -> Dict[str, Any]:
    """Оценка минимального количества сотрудников (грубая, без учёта переходов).
    
    Учитывает обед (недоступное время).
    """
    active = [r for r in project.all_rooms() if not r.disabled]
    if not active:
        return {"employees": 1, "daily_minutes": 0.0, "capacity_minutes": 0.0}

    weather = project.weather_factor
    daily_minutes = 0.0
    for room in active:
        freq = _get_effective_freq(room.room_type, weather)
        time_per = get_cleaning_time_minutes(room.room_type, room.area_m2)
        daily_minutes += time_per * freq

    shift_minutes = _get_shift_minutes(project)
    # 15% резерв на переходы
    capacity = max(1.0, shift_minutes * 0.85)
    employees = max(1, math.ceil(daily_minutes / capacity))
    return {
        "employees": employees,
        "daily_minutes": round(daily_minutes, 1),
        "capacity_minutes": round(capacity, 1),
    }


def calculate_cost(project: Project) -> Dict[str, Any]:
    """Расчёт стоимости на основе scheduler-результатов.
    
    Если cleaning_tasks есть — использует их фактические минуты.
    Иначе — оценка по sanitarnorm.
    """
    weather = project.weather_factor
    rate = project.hourly_rate
    staff = project.employees_count

    # Фактическое время из расписания (если есть)
    if project.cleaning_tasks:
        total_minutes = 0.0
        for task in project.cleaning_tasks:
            total_minutes += (task.end_dt - task.start_dt).total_seconds() / 60.0
    else:
        # Оценка по sanitarnorm
        active = [r for r in project.all_rooms() if not r.disabled]
        total_minutes = 0.0
        for room in active:
            freq = _get_effective_freq(room.room_type, weather)
            time_per = get_cleaning_time_minutes(room.room_type, room.area_m2)
            total_minutes += time_per * freq

    total_hours = total_minutes / 60.0
    shift_minutes = _get_shift_minutes(project)
    shift_hours = shift_minutes / 60.0
    staff_hours = staff * shift_hours

    if total_hours <= 0:
        return {
            'total_time_hours': 0.0,
            'staff_count': staff,
            'staff_hours': staff_hours,
            'overtime_hours': 0.0,
            'cost_with_overtime': 0.0,
            'needed_employees': staff,
            'cost_hire': staff_hours * rate,
            'recommendation': 'недостаточно данных',
            'total_minutes': 0.0,
            'shift_hours': shift_hours,
        }

    # Стоимость с переработкой
    if total_hours <= staff_hours:
        overtime = 0.0
        cost_staff = total_hours * rate
    else:
        overtime = total_hours - staff_hours
        cost_staff = staff_hours * rate + overtime * rate * 1.5

    needed_emp = max(1, math.ceil(total_hours / shift_hours)) if shift_hours > 0 else staff
    cost_hire = needed_emp * shift_hours * rate

    # Рекомендация
    if needed_emp < staff:
        savings = (staff * shift_hours * rate) - cost_hire
        recommendation = f"сократить штат до {needed_emp} (экономия {savings:.0f} руб./день)"
    elif total_hours <= staff_hours:
        recommendation = "оставить штат"
    elif needed_emp > staff:
        hire_more = needed_emp - staff
        recommendation = f"нанять ещё {hire_more} чел. (до {needed_emp}) — {cost_hire:.0f} руб./день"
    else:
        recommendation = "оставить штат"

    return {
        'total_time_hours': round(total_hours, 2),
        'staff_count': staff,
        'staff_hours': round(staff_hours, 2),
        'overtime_hours': round(overtime, 2),
        'cost_with_overtime': round(cost_staff, 2),
        'needed_employees': needed_emp,
        'cost_hire': round(cost_hire, 2),
        'recommendation': recommendation,
        'total_minutes': round(total_minutes, 1),
        'shift_hours': round(shift_hours, 2),
    }