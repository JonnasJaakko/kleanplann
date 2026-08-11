"""Targeted test: SINGLE_SHIFT mode - one day, one shift."""
import sys; sys.path.insert(0, '.')
from datetime import date
from project import Project, Room, Floor, Shift
from scheduler import schedule_single_shift
from zone_manager import manual_distribution

# ===== TEST 1: 88 rooms, SINGLE_SHIFT =====
print("=== TEST 1: 88 rooms SINGLE_SHIFT ===")
p = Project('Test 88')
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
p.zones = manual_distribution(p.all_rooms(), [100.0/8]*8)
for z in p.zones:
    z.floor_index = 0

target = date(2026, 8, 5)
result = schedule_single_shift(p, target_date=target, employees=8)
tasks = result["tasks"]

unique_dates = {t.start_dt.date() for t in tasks}
print(f"unique_dates: {len(unique_dates)}")
print(f"selected_date in unique_dates: {target in unique_dates}")
print(f"tasks: {len(tasks)}")
print(f"all tasks on selected_date: {all(t.start_dt.date() == target for t in tasks)}")
print(f"feasible: {result['feasible']}")
print(f"scheduled_rooms: {result['scheduled_rooms']}/{result['active_rooms']}")
print(f"missed_cleanings: {result['missed_cleanings']}")

assert len(unique_dates) == 1, f"FAIL: expected 1 unique date, got {len(unique_dates)}"
assert target in unique_dates, f"FAIL: selected date not in unique_dates"
assert all(t.start_dt.date() == target for t in tasks), "FAIL: some tasks on wrong date"
print("TEST 1: PASS (unique_dates=1)")

# ===== TEST 2: Frequency test =====
print()
print("=== TEST 2: Frequency test (3 rooms) ===")
p2 = Project('Freq Test')
floor2 = Floor(index=0, name='Floor 1')
# Room 1: freq=1 (кабинет), Room 2: freq=2 (коридор), Room 3: freq=3 (санузел)
r1 = Room(0, [(0,0),(5,0),(5,5),(0,5)], area_m2=20.0); r1.room_type = 'кабинет'; r1.name = 'Office'
r2 = Room(1, [(10,0),(15,0),(15,5),(10,5)], area_m2=50.0); r2.room_type = 'коридор'; r2.name = 'Corridor'
r3 = Room(2, [(20,0),(25,0),(25,5),(20,5)], area_m2=15.0); r3.room_type = 'санузел'; r3.name = 'WC'
floor2.rooms = [r1, r2, r3]
p2.floors = [floor2]
p2.employees_count = 1
p2.shifts = [Shift('Main','09:00','18:00')]
p2.breaks = [('12:00','13:00')]
p2.weather_factor = 1.0
p2.zones = manual_distribution(p2.all_rooms(), [100.0])

result2 = schedule_single_shift(p2, target_date=target, employees=1)
tasks2 = result2["tasks"]
unique_dates2 = {t.start_dt.date() for t in tasks2}
print(f"unique_dates: {len(unique_dates2)}")
print(f"tasks: {len(tasks2)}")
print(f"required_cleanings: {result2['required_cleanings']}")
print(f"scheduled_cleanings: {result2['scheduled_cleanings']}")
print(f"missed_cleanings: {result2['missed_cleanings']}")
print(f"feasible: {result2['feasible']}")

assert len(unique_dates2) == 1, f"FAIL: expected 1 unique date, got {len(unique_dates2)}"
assert all(t.start_dt.date() == target for t in tasks2), "FAIL: some tasks on wrong date"
print("TEST 2: PASS (unique_dates=1)")

print()
print("SINGLE_SHIFT: PASS")
print("unique_dates: 1")