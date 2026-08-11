"""Staffing search: find minimum feasible employees."""
import sys; sys.path.insert(0, '.')
from datetime import date
from project import Project, Room, Floor, Shift
from scheduler import schedule_single_shift
from zone_manager import manual_distribution

p = Project('Staffing')
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
p.zones = manual_distribution(p.all_rooms(), [100.0/7]*7)
for z in p.zones:
    z.floor_index = 0

target = date(2026, 8, 5)
print('=== STAFFING SEARCH ===')
for emp in [7, 8, 10, 12, 14, 16, 18, 20, 22, 24]:
    r = schedule_single_shift(p, target_date=target, employees=emp)
    print(f'emp={emp}: sched={r["scheduled_rooms"]}/{r["active_rooms"]}, '
          f'missed={r["missed_cleanings"]}, feasible={r["feasible"]}, '
          f'workload_h={r["total_workload_hours"]:.1f}, av_h={r["available_minutes"]/60:.1f}')