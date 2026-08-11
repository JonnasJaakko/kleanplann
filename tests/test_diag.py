import sys, time
sys.path.insert(0, '.')
from project import Project, Room, Floor, Shift

p = Project('Тест 88 комнат')
floor = Floor(index=0, name='Этаж 1')
types = ['коридор','санузел','кабинет','склад','зал','кухня']
areas = {'коридор':50,'санузел':15,'кабинет':20,'склад':60,'зал':200,'кухня':30}
for i in range(88):
    t = types[i % 6]
    a = areas[t]
    x = (i % 11) * 20
    y = (i // 11) * 20
    pts = [(x,y),(x+9,y),(x+9,y+9),(x,y+9)]
    r = Room(i, pts, area_m2=float(a)); r.room_type = t; r.name = f'{t} {i+1}'
    floor.rooms.append(r)
p.floors = [floor]
p.employees_count = 8
p.shifts = [Shift('Основная','09:00','18:00')]
p.breaks = [('12:00','13:00')]
p.weather_factor = 1.0

from zone_manager import manual_distribution
all_rooms = p.all_rooms()
p.zones = manual_distribution(all_rooms, [100.0/8]*8)
for z in p.zones:
    z.floor_index = 0

t0 = time.time()
from scheduler import plan_cleaning_schedule
tasks = plan_cleaning_schedule(p, fixed_employees=8, max_days=1)
print(f'plan_cleaning_schedule: {time.time()-t0:.3f}s, {len(tasks)} задач')

t0 = time.time()
from schedule_validator import validate_schedule
v = validate_schedule(p)
print(f'validate_schedule: {time.time()-t0:.3f}s')
print(f'  valid={v["valid"]}, rooms_scheduled={v["rooms_scheduled"]}/{v["rooms_active"]}, tasks={v["total_tasks"]}')
print(f'  conflicts={v["time_conflicts"]}, break_violations={v["break_violations"]}, out_of_shift={v["out_of_shift_tasks"]}')
print(f'  freq_violations={v["frequency_violations"]}, missed={len(v["missed_rooms"])}')

t0 = time.time()
from cost_calculator import calculate_cost
cost = calculate_cost(p)
print(f'calculate_cost: {time.time()-t0:.3f}s')
print(f'  total={cost["total_time_hours"]}ч, needed={cost["needed_employees"]}, rec={cost["recommendation"]}')
print('ALL DONE')