"""PRODUCTION TEST: 88 rooms, 7 employees, SINGLE_SHIFT."""
import sys; sys.path.insert(0, '.')
from datetime import date
from project import Project, Room, Floor, Shift
from scheduler import schedule_single_shift
from schedule_validator import validate_schedule
from zone_manager import manual_distribution

# ===== CREATE PROJECT =====
p = Project('Production Test')
floor = Floor(index=0, name='Floor 1')
types = ['corridor','wc','office','store','hall','kitchen']
areas_d = {'corridor':50,'wc':15,'office':20,'store':60,'hall':200,'kitchen':30}
for i in range(88):
    t = types[i % 6]
    a = areas_d[t]
    x = (i % 11) * 20
    y = (i // 11) * 20
    pts = [(x,y),(x+9,y),(x+9,y+9),(x,y+9)]
    r = Room(i, pts, area_m2=float(a)); r.room_type = t; r.name = f'Room {i+1}'
    floor.rooms.append(r)
p.floors = [floor]
p.employees_count = 7
p.shifts = [Shift('Main','09:00','18:00')]
p.breaks = [('12:00','13:00')]
p.weather_factor = 1.0
p.hourly_rate = 200.0

# Use existing zone distribution (7 employees)
p.zones = manual_distribution(p.all_rooms(), [100.0/7]*7)
for z in p.zones:
    z.floor_index = 0

target = date(2026, 8, 5)
result = schedule_single_shift(p, target_date=target, employees=7)
tasks = result["tasks"]

# ===== OUTPUT =====
print("=" * 60)
print("PRODUCTION TEST: 88 rooms / 7 employees / SINGLE_SHIFT")
print("=" * 60)
print(f"DATE: {result['date']}")
print(f"SHIFT: {result['shift_start']} - {result['shift_end']}")
print(f"BREAK: {result['break_start']} - {result['break_end']}")
print()
print(f"ACTIVE ROOMS: {result['active_rooms']}")
print(f"SCHEDULED ROOMS: {result['scheduled_rooms']}")
print(f"UNSCHEDULED ROOMS: {result['unscheduled_rooms']}")
print()
print(f"REQUIRED CLEANINGS: {result['required_cleanings']}")
print(f"SCHEDULED CLEANINGS: {result['scheduled_cleanings']}")
print(f"MISSED CLEANINGS: {result['missed_cleanings']}")
print()
print(f"CLEANING MINUTES: {result['cleaning_minutes']}")
print(f"CLEANING HOURS: {result['cleaning_hours']}")
print(f"TRANSIT MINUTES: {result['transit_minutes']}")
print(f"TOTAL WORKLOAD MINUTES: {result['total_workload_minutes']}")
print(f"TOTAL WORKLOAD HOURS: {result['total_workload_hours']}")
print()
print(f"AVAILABLE MINUTES: {result['available_minutes']} ({result['employees']} x 480)")
print(f"CAPACITY DEFICIT: {result['capacity_deficit']}")
print()
print(f"EMPLOYEES: {result['employees']}")
print(f"FEASIBLE: {result['feasible']}")
print()
print("VIOLATIONS:")
for k, v in result['violations'].items():
    print(f"  {k}: {v}")
print()

# ===== VALIDATOR =====
v = validate_schedule(p)
print("=" * 60)
print("VALIDATOR")
print("=" * 60)
print(f"valid: {v['valid']}")
print(f"rooms_total: {v['rooms_total']}")
print(f"active_rooms: {v['active_rooms']}")
print(f"scheduled_rooms: {v['scheduled_rooms']}")
print(f"unscheduled_rooms: {v['unscheduled_rooms']}")
print(f"duplicate_assignments: {v['duplicate_assignments']}")
print(f"tasks_total: {v['tasks_total']}")
print(f"time_conflicts: {v['time_conflicts']}")
print(f"break_violations: {v['break_violations']}")
print(f"out_of_shift_tasks: {v['out_of_shift_tasks']}")
print(f"frequency_required: {v['frequency_required']}")
print(f"frequency_scheduled: {v['frequency_scheduled']}")
print(f"frequency_violations: {v['frequency_violations']}")
print(f"cleaning_minutes: {v['cleaning_minutes']}")
print(f"transit_minutes: {v['transit_minutes']}")
print(f"total_minutes: {v['total_minutes']}")
print(f"total_hours: {v['total_hours']}")
print(f"employees: {v['employees']}")
print(f"overtime_minutes: {v['overtime_minutes']}")
print(f"cost: {v['cost']}")
print()

# ===== UNSCHEDULED DETAILS =====
if result['unscheduled_rooms_list']:
    print("UNSCHEDULED TASKS (first 10):")
    for u in result['unscheduled_rooms_list'][:10]:
        print(f"  - {u['room_name']} (floor={u['room'][0]}, room={u['room'][1]}), "
              f"cleaning #{u['cleaning_index']}, reason={u['reason']}, "
              f"required={u['required_minutes']:.0f} min")
    if len(result['unscheduled_rooms_list']) > 10:
        print(f"  ... and {len(result['unscheduled_rooms_list']) - 10} more")

# ===== UNIQUE DATES CHECK =====
unique_dates = {t.start_dt.date() for t in tasks}
print()
print(f"UNIQUE DATES: {len(unique_dates)}")
print(f"ALL ON TARGET DATE: {all(t.start_dt.date() == target for t in tasks)}")

# ===== SORTING CHECK =====
print()
print("SORTING CHECK (first employee):")
if tasks:
    emp0 = sorted([t for t in tasks if t.employee == 0], key=lambda t: t.start_dt)
    for t in emp0[:5]:
        print(f"  {t.start_dt.strftime('%H:%M')} - {t.end_dt.strftime('%H:%M')} room={t.room_id}")
    sorted_ok = all(emp0[i].start_dt <= emp0[i+1].start_dt for i in range(len(emp0)-1))
    print(f"  sorted: {sorted_ok}")