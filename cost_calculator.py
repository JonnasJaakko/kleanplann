from typing import Dict, Any
from project import Project, Zone
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day
import math

TIME_PER_SQ_M_BASE = 0.05  # часа на м²
SHIFT_HOURS = 8


def estimate_required_employees(project: Project) -> Dict[str, Any]:
    """Estimate daily staffing from imported rooms and the configured shifts.

    The calculation deliberately reserves 15% of a shift for moving between
    rooms, preparation and small unplanned work.  It uses all floors, so the
    answer remains valid immediately after a multi-floor DXF import.
    """
    rooms = project.all_rooms()
    if not rooms:
        return {"employees": 1, "daily_minutes": 0.0, "capacity_minutes": 0.0}

    daily_minutes = 0.0
    for room in rooms:
        frequency = get_frequency_per_day(room.room_type) * project.weather_factor
        daily_minutes += get_cleaning_time_minutes(room.room_type, room.area_m2) * frequency

    # A worker can normally work across all configured shifts in a day.
    shift_minutes = 0
    for shift in project.shifts:
        try:
            start_h, start_m = map(int, shift.start_time.split(':'))
            end_h, end_m = map(int, shift.end_time.split(':'))
            shift_minutes += max(0, (end_h * 60 + end_m) - (start_h * 60 + start_m))
        except (ValueError, AttributeError):
            continue
    shift_minutes = shift_minutes or int(SHIFT_HOURS * 60)
    capacity = max(1.0, shift_minutes * 0.85)
    employees = max(1, math.ceil(daily_minutes / capacity))
    return {
        "employees": employees,
        "daily_minutes": round(daily_minutes, 1),
        "capacity_minutes": round(capacity, 1),
    }

def _cleaning_frequency(traffic: int, weather_factor: float = 1.0) -> float:
    base = 3.0
    if traffic > 30: base = 1.0
    elif traffic >= 10: base = 2.0
    return base / weather_factor

def _room_priority(room, mode: str) -> float:
    if mode == 'traffic': return room.traffic
    if mode == 'complexity': return 1.0 / (room.area_m2 + 1)
    return room.area_m2  # area

def calculate_cost(project: Project) -> Dict[str, Any]:
    rooms_map = {r.id: r for r in project.rooms}
    weather = project.weather_factor
    total_time = 0.0
    for zone in project.zones:
        # время для каждой комнаты в зоне
        for rid in zone.room_ids:
            room = rooms_map.get(rid)
            if room:
                freq = _cleaning_frequency(room.traffic, weather)
                cleans_per_shift = SHIFT_HOURS / freq
                time_per_clean = room.area_m2 * TIME_PER_SQ_M_BASE
                total_time += time_per_clean * cleans_per_shift
    staff = project.employees_count
    rate = project.hourly_rate
    staff_hours = staff * SHIFT_HOURS
    if total_time <= 0:
        return {'total_time_hours':0.0,'staff_count':staff,'staff_hours':staff_hours,
                'overtime_hours':0.0,'cost_with_overtime':0.0,'needed_employees':staff,
                'cost_hire':staff_hours*rate,'recommendation':'недостаточно данных'}
    if total_time <= staff_hours:
        overtime = 0.0
        cost_staff = total_time * rate
    else:
        overtime = total_time - staff_hours
        cost_staff = staff_hours * rate + overtime * rate * 1.5
    needed_emp = math.ceil(total_time / SHIFT_HOURS)
    cost_hire = needed_emp * SHIFT_HOURS * rate
    if staff > needed_emp and needed_emp > 0:
        savings = cost_staff - (needed_emp * SHIFT_HOURS * rate)
        recommendation = f"сократить штат до {needed_emp} (экономия {savings:.0f} руб.)"
    elif total_time <= staff_hours:
        recommendation = "оставить штат"
    else:
        recommendation = f"нанять сотрудников до {needed_emp} чел."
    return {
        'total_time_hours': round(total_time, 2),
        'staff_count': staff,
        'staff_hours': staff_hours,
        'overtime_hours': round(overtime, 2),
        'cost_with_overtime': round(cost_staff, 2),
        'needed_employees': needed_emp,
        'cost_hire': round(cost_hire, 2),
        'recommendation': recommendation
    }
