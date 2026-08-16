# scheduler.py
import math
from datetime import datetime, timedelta
from project import CleaningTask
import sanitarnorm

# Технологический интервал тишины для объекта (в минутах)
MIN_COOLDOWN_MINUTES = {
    "санузел": 180,       # Между визитами строго не менее 3 часов покоя!
    "кухня": 180,
    "коридор": 120,
    "default": 120
}

def _get_cooldown(room_name: str) -> int:
    name_lower = room_name.lower()
    for k, v in MIN_COOLDOWN_MINUTES.items():
        if k in name_lower: return v
    return MIN_COOLDOWN_MINUTES["default"]

def _get_transit_time(room_name: str, current_floor: int, next_floor: int) -> int:
    if current_floor != next_floor: return 5
    if "санузел" in room_name.lower(): return 3
    return 1

def _find_strict_slot(start_search, duration, employee_occupied, room_history, room_id, room_name, current_floor, last_floor, total_shift_min, is_priority):
    cooldown = _get_cooldown(room_name)
    transit = _get_transit_time(room_name, last_floor, current_floor)
    current_start = start_search
    
    while current_start + duration <= total_shift_min:
        current_end = current_start + duration
        
        # 1. Глобальная проверка буфера тишины для комнаты
        too_early = False
        if room_id in room_history:
            for last_start, last_end in room_history[room_id]:
                if current_start < last_end + cooldown:
                    current_start = last_end + cooldown
                    too_early = True
                    break
        if too_early: 
            if is_priority:
                continue
            else:
                # Рутинным задачам разрешаем сдвигаться дальше, если буфер пробит
                continue

        # 2. Проверка пересечений занятости сотрудника
        overlap = False
        for occ_start, occ_end in employee_occupied:
            if not (current_end <= occ_start or current_start >= occ_end):
                current_start = occ_end
                overlap = True
                break
        
        if not overlap:
            return current_start, current_end, current_end + transit
            
    return None

def compute_recommended_employees(project) -> int:
    from cost_calculator import estimate_required_employees
    res = estimate_required_employees(project)
    return res["employees"]

def schedule_single_shift(project, employees: int, allow_partial_schedule: bool = True):
    shifts_list = project.shifts
    if isinstance(shifts_list, list) and len(shifts_list) > 0:
        shift = shifts_list[0]
        s_start_time = shift.start_time
        s_end_time = shift.end_time
    elif hasattr(shifts_list, 'start_time'):
        s_start_time = shifts_list.start_time
        s_end_time = shifts_list.end_time
    else:
        s_start_time = "09:00"
        s_end_time = "19:00"
        
    if hasattr(project, 'start_date') and project.start_date:
        base_date = datetime.strptime(project.start_date, "%d.%m.%Y").date() if isinstance(project.start_date, str) else project.start_date
    else:
        base_date = datetime.today().date()
    
    shift_start_dt = datetime.combine(base_date, datetime.strptime(s_start_time, "%H:%M").time())
    shift_end_dt = datetime.combine(base_date, datetime.strptime(s_end_time, "%H:%M").time())
    total_shift_min = int((shift_end_dt - shift_start_dt).seconds / 60)
    
    unique_emp_ids = set()
    for zone in project.zones:
        if hasattr(zone, 'employee_index'): unique_emp_ids.add(zone.employee_index)
    actual_emp_count = max(employees, max(unique_emp_ids) + 1 if unique_emp_ids else 0)
    
    employees_occupied = {i: [] for i in range(actual_emp_count)}
    employee_last_floor = {i: 0 for i in range(actual_emp_count)}
    
    stats_cleaning = {i: 0 for i in range(actual_emp_count)}
    stats_transit = {i: 0 for i in range(actual_emp_count)}
    stats_lunch = {i: 0 for i in range(actual_emp_count)}
    
    room_history = {}
    
    # Блокируем обеденный перерыв
    if project.breaks:
        for b_start, b_end in project.breaks:
            try:
                bs_dt = datetime.combine(base_date, datetime.strptime(b_start, "%H:%M").time())
                be_dt = datetime.combine(base_date, datetime.strptime(b_end, "%H:%M").time())
                bs = int((bs_dt - shift_start_dt).seconds / 60)
                be = int((be_dt - shift_start_dt).seconds / 60)
                for emp_idx in employees_occupied:
                    employees_occupied[emp_idx].append((bs, be))
                    stats_lunch[emp_idx] = be - bs
            except Exception:
                pass

    final_tasks = []
    unscheduled_rooms_list = []
    all_jobs = []
    
    for zone in project.zones:
        emp_idx = zone.employee_index
        zone.name = f"Сотрудник {emp_idx + 1}"
        
        for r_id in zone.room_ids:
            room = next((r for r in project.all_rooms() if r.id == r_id), None)
            if not room or room.disabled: continue
                
            duration_min = sanitarnorm.get_cleaning_time_minutes(room.room_type, room.area_m2, project.weather_factor)
            freq = sanitarnorm.get_frequency_per_day(room.room_type)
            
            all_jobs.append({
                'room_id': room.id, 'room_name': room.name, 'primary_employee': emp_idx,
                'duration': int(duration_min), 'frequency': freq, 'priority': room.priority,
                'floor_index': zone.floor_index if hasattr(zone, 'floor_index') else 1
            })
            
    # Сортировка: критические задачи высокой частоты идут первыми
    all_jobs.sort(key=lambda j: (not j['priority'], j['frequency']), reverse=True)
    
    for job in all_jobs:
        step_min = total_shift_min / job['frequency']
        
        for iteration in range(job['frequency']):
            target_start = int(iteration * step_min)
            
            emp_to_try = job['primary_employee']
            last_flr = employee_last_floor[emp_to_try]
            
            # Попытка 1: Планируем сотруднику его родную задачу в расчётное время
            slot = _find_strict_slot(
                target_start, job['duration'], employees_occupied[emp_to_try], 
                room_history, job['room_id'], job['room_name'], job['floor_index'], last_flr, total_shift_min, job['priority']
            )
            
            # Попытка 2 (Шеринг задач): Если у основного сотрудника затор, ищем ПО-НАСТОЯЩЕМУ свободного коллегу
            if not slot:
                # Сортируем помощников по фактической сумме занятых клинингом минут (разница концов и начал отрезков)
                sorted_emps = sorted(
                    range(actual_emp_count), 
                    key=lambda e: sum((interval[1] - interval[0]) for interval in employees_occupied[e])
                )
                
                for alternative_emp in sorted_emps:
                    if alternative_emp == job['primary_employee']: continue
                    alt_last_flr = employee_last_floor[alternative_emp]
                    
                    # Для альтернативного сотрудника: если задача РУТИННАЯ, разрешаем искать слот 
                    # не от жесткого target_start, а от ЛЮБОГО текущего свободного момента на его временной сетке!
                    search_start_time = target_start if job['priority'] else 0
                    
                    slot = _find_strict_slot(
                        search_start_time, job['duration'], employees_occupied[alternative_emp], 
                        room_history, job['room_id'], job['room_name'], job['floor_index'], alt_last_flr, total_shift_min, job['priority']
                    )
                    if slot:
                        emp_to_try = alternative_emp
                        break
                        
            if slot:
                start_m, end_m, end_with_transit = slot
                employees_occupied[emp_to_try].append((start_m, end_with_transit))
                employee_last_floor[emp_to_try] = job['floor_index']
                room_history.setdefault(job['room_id'], []).append((start_m, end_m))
                
                stats_cleaning[emp_to_try] += job['duration']
                stats_transit[emp_to_try] += (end_with_transit - end_m)
                
                t_start = shift_start_dt + timedelta(minutes=start_m)
                t_end = shift_start_dt + timedelta(minutes=end_m)
                
                final_tasks.append(CleaningTask(
                    room_id=job['room_id'], floor_index=job['floor_index'],
                    start_dt=t_start, end_dt=t_end, employee=emp_to_try
                ))
            else:
                unscheduled_rooms_list.append({
                    'room': (job['floor_index'], job['room_id']), 'room_name': job['room_name'],
                    'reason': f"Повтор {iteration+1} превысил фонд смены", 'critical': job['priority']
                })

    employee_analytics_strings = {}
    for i in range(actual_emp_count):
        clean_h = round(stats_cleaning[i] / 60, 2)
        trans_h = round(stats_transit[i] / 60, 2)
        lunch_h = round(stats_lunch[i] / 60, 2)
        total_worked_h = round(clean_h + trans_h + lunch_h, 2)
        
        employee_analytics_strings[i] = (
            f"{total_worked_h} ч (чистая уборка: {clean_h} ч, "
            f"перемещения: {trans_h} ч, обед: {lunch_h} ч)"
        )

    final_tasks.sort(key=lambda t: (t.employee, t.start_dt))
    return {
        "tasks": final_tasks, "unscheduled_rooms": len(unscheduled_rooms_list),
        "missed_cleanings": len(unscheduled_rooms_list), "unscheduled_rooms_list": unscheduled_rooms_list,
        "feasible": len(unscheduled_rooms_list) == 0,
        "employee_analytics": employee_analytics_strings
    }

def plan_cleaning_schedule(project):
    return schedule_single_shift(project, project.employees_count)
