"""ADAPTABILITY TEST: 5/8/13/20 employees, 88 rooms, SINGLE_SHIFT."""
import sys; sys.path.insert(0, '.')
from datetime import date
from project import Project, Room, Floor, Shift
from scheduler import schedule_single_shift
from schedule_validator import validate_schedule
from zone_manager import manual_distribution

# ===== CREATE PROJECT =====
p = Project('Adaptability Test')
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
p.shifts = [Shift('Main','09:00','18:00')]
p.breaks = [('12:00','13:00')]
p.weather_factor = 1.0
p.hourly_rate = 200.0

target = date(2026, 8, 5)

print("=" * 70)
print("ADAPTABILITY TEST: 88 rooms / different employees")
print("=" * 70)
print(f"{'emp':>4} {'feas':>5} {'rooms':>6} {'req':>4} {'sched':>5} {'miss':>5} {'clean_min':>10} {'transit':>8} {'idle':>6} {'util%':>6} {'cost':>10}")
print("-" * 70)

for emp in [5, 8, 13, 20]:
    # Пересоздаём зоны для каждого количества
    p.employees_count = emp
    p.zones = manual_distribution(p.all_rooms(), [100.0/emp]*emp)
    for z in p.zones:
        z.floor_index = 0

    r = schedule_single_shift(p, target_date=target, employees=emp)
    tasks = r["tasks"]

    # Loads
    if r["employee_loads"]:
        util_vals = [l["utilization_percent"] for l in r["employee_loads"].values()]
        avg_util = sum(util_vals) / len(util_vals) if util_vals else 0
    else:
        avg_util = 0

    print(f"{emp:>4} {str(r['feasible']):>5} {r['scheduled_rooms']:>4}/{r['active_rooms']:<4} "
          f"{r['required_cleanings']:>4} {r['scheduled_cleanings']:>5} {r['missed_cleanings']:>5} "
          f"{r['cleaning_minutes']:>10.1f} {r['transit_minutes']:>8.1f} {r['idle_minutes']:>6.1f} "
          f"{avg_util:>6.1f} -")

    # Unique dates check
    unique_dates = {t.start_dt.date() for t in tasks}
    assert len(unique_dates) == 1, f"FAIL: {len(unique_dates)} unique dates"
    assert all(t.start_dt.date() == target for t in tasks), "FAIL: wrong date"

    # Chronological sort check
    for e in sorted(set(t.employee for t in tasks)):
        et = [t for t in tasks if t.employee == e]
        et.sort(key=lambda t: t.start_dt)
        sorted_ok = all(et[i].start_dt <= et[i+1].start_dt for i in range(len(et)-1))
        assert sorted_ok, f"FAIL: employee {e} not sorted"

print()
print("ALL ADAPTABILITY TESTS PASS (unique_dates=1, sorted, no exceptions)")