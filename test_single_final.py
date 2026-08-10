"""PHASE 8: Real scenario 88 rooms - staffing search."""
import sys; sys.path.insert(0, '.')
from project import Project, Room, Floor, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day
from scheduler import schedule_single_shift, _get_effective_freq
from zone_manager import manual_distribution

# ===== CREATE PROJECT =====
p = Project('Test 88 rooms')
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
p.employees_count = 8
p.shifts = [Shift('Main','09:00','18:00')]
p.breaks = [('12:00','13:00')]
p.weather_factor = 1.0

all_rooms = p.all_rooms()
p.zones = manual_distribution(all_rooms, [100.0/8]*8)
for z in p.zones:
    z.floor_index = 0

# ===== WORKLOAD AUDIT =====
total_daily = 0.0
for room in all_rooms:
    eff = _get_effective_freq(room.room_type, 1.0)
    tp = get_cleaning_time_minutes(room.room_type, room.area_m2)
    total_daily += tp * eff

print('=== WORKLOAD AUDIT ===')
print(f'total_area: {sum(r.area_m2 for r in all_rooms):.0f} m2')
print(f'daily_cleaning_minutes: {total_daily:.1f}')
print(f'daily_cleaning_hours: {total_daily/60:.1f}')

# ===== STAFFING SEARCH =====
print()
print('=== STAFFING SEARCH ===')
for emp in [8, 12, 16, 20, 24, 26, 28, 30]:
    r = schedule_single_shift(p, employees=emp, target_date=__import__('datetime').date(2026,8,5))
    print(f'emp={emp}: sched={r["scheduled_rooms"]}/{r["active_rooms"]}, '
          f'missed_clean={r["missed_cleanings"]}, '
          f'workload_h={r["total_workload_hours"]:.1f}, av_h={r["available_minutes"]/60:.1f}, '
          f'feasible={r["feasible"]}, conflicts={r["violations"]["time_conflicts"]}, '
          f'break_viol={r["violations"]["break_violations"]}')