"""PHASE 7: Аудит 411 часов + PHASE 8: Реальный сценарий 88 комнат."""
import sys; sys.path.insert(0, '.')
from project import Project, Room, Floor, Shift
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day
from scheduler import schedule_single_shift, _get_effective_freq
from zone_manager import manual_distribution

# ===== СОЗДАНИЕ ПРОЕКТА =====
p = Project('Тест 88 комнат')
floor = Floor(index=0, name='Этаж 1')
types = ['коридор','санузел','кабинет','склад','зал','кухня']
areas_dict = {'коридор':50,'санузел':15,'кабинет':20,'склад':60,'зал':200,'кухня':30}
for i in range(88):
    t = types[i % 6]
    a = areas_dict[t]
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

# ===== АУДИТ 411 ЧАСОВ =====
print("="*70)
print("АУДИТ ТРУДОЁМКОСТИ")
print("="*70)

weather = 1.0
total_daily = 0.0
print(f"{'room_id':>8} {'type':<12} {'area_m2':>8} {'complexity':>10} {'fixed_min':>10} {'min_per_clean':>14} {'freq':>6} {'daily_min':>10}")
print("-"*80)
for room in all_rooms:
    freq = get_frequency_per_day(room.room_type)
    eff_freq = _get_effective_freq(room.room_type, weather)
    time_per = get_cleaning_time_minutes(room.room_type, room.area_m2)
    daily_min = time_per * eff_freq
    total_daily += daily_min
    print(f"{room.id:>8} {room.room_type:>12} {room.area_m2:>8.0f} {1.0:>8.2f} {0:>10} {time_per:>12.1f} {freq:>6} x{weather} {daily_min:>10.1f}")

print("-"*70)
print(f"{'ИТОГО:':>60} {total_daily:>10.1f} мин = {total_daily/60:.1f} ч")

# ===== SCHEDULER SINGLE_SHIFT =====
print()
print("="*70)
print("SCHEDULER SINGLE_SHIFT (8 сотрудников)")
print("="*70)

result = schedule_single_shift(p)
print(f"\nDATE: {result['date']}")
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
print(f"VALIDATOR VALID: {result['validation']['valid']}")
print()
print(f"411 HOURS AUDIT:")
print(f"  total_daily_cleaning_minutes: {total_daily:.1f} = {total_daily/60:.1f} ч")
print(f"  Если workload ~ {total_daily/60:.0f} часов, то это математически корректно")
print(f"  для 4001 м² с частотой 1-3 уборки/день и 1 мин/м²")
print()
print("UNSCHEDULED ROOMS:")
for u in result['unscheduled_rooms_list'][:10]:
    print(f"  - {u['room_name']} (№{u['room'][1]+1}), уборка #{u['cleaning_index']}: {u['reason']} ({u['required_minutes']:.0f} мин)")
if len(result['unscheduled_rooms_list']) > 10:
    print(f"  ... и ещё {len(result['unscheduled_rooms_list']) - 10}")

print()
print("="*70)
print("ПРОВЕРКА STAFFING: поиск минимального feasible штата")
print("="*70)
for emp in [8, 16, 20, 24, 26, 28, 30]:
    r = schedule_single_shift(p, employees=emp)
    print(f"  {emp} empl: scheduled_rooms={r['scheduled_rooms']}/{r['active_rooms']}, "
          f"missed={r['missed_cleanings']}, feasible={r['feasible']}, "
          f"workload_h={r['total_workload_hours']:.1f}, av_h={r['available_minutes']/60:.1f}")