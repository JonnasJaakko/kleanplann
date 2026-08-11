"""
Проверка: уборки одной комнаты НЕ должны идти подряд.
Если у комнаты freq > 1, между её уборками соблюдается интервал.
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from project import Project, Room, Floor, Zone, Shift, CleaningTask
from scheduler import plan_cleaning_schedule
from sanitarnorm import COMPLEXITY_FACTOR, DEFAULT_FREQUENCY_PER_DAY

random.seed(7)

def make_project(num_rooms=12, employees=3):
    p = Project("dup_test")
    p.employees_count = employees
    p.employee_names = [f"Сотрудник {i+1}" for i in range(employees)]
    p.start_date = date(2026, 1, 1)
    p.end_date = date(2026, 1, 1)
    p.shifts = [Shift("Основная", "08:00", "22:00")]
    p.breaks = [("12:00", "13:00")]
    p.weather_factor = 1.0

    floor = p.floors[0]
    rooms = []
    types = list(COMPLEXITY_FACTOR.keys())
    # Маленькие площади: 9-25 м²
    grid_cols = 4
    for i in range(num_rooms):
        col = i % grid_cols
        row = i // grid_cols
        x = col * 8.0
        y = row * 8.0
        w, h = 3.0 + random.uniform(0, 2), 3.0 + random.uniform(0, 2)
        pts = [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]
        room_type = types[i % len(types)]
        area = w * h
        rooms.append(Room(i, pts, area_m2=area, traffic=30,
                          room_type=room_type, name=f"Комната {i+1}"))
    floor.rooms = rooms

    # Зоны: примерно поровну
    split = num_rooms // employees
    p.zones = []
    for e in range(employees):
        start = e * split
        end = num_rooms if e == employees - 1 else (e+1) * split
        p.zones.append(Zone(e, f"Зона {e+1}", list(range(start, end)), employee_index=e))
    return p

def find_duplicate_cleaning(tasks):
    """Находит случаи, когда одна комната убирается дважды подряд
    БЕЗ перерыва между ними (настоящее дублирование)."""
    by_emp = {}
    for t in tasks:
        by_emp.setdefault(t.employee, []).append(t)
    errors = []
    for emp, emp_tasks in sorted(by_emp.items()):
        emp_tasks.sort(key=lambda t: t.start_dt)
        for i in range(1, len(emp_tasks)):
            prev = emp_tasks[i-1]
            cur = emp_tasks[i]
            if cur.room_id != prev.room_id:
                continue
            gap = (cur.start_dt - prev.end_dt).total_seconds() / 60
            if gap <= 5:
                errors.append(
                    f"Дублирование у сотрудника {emp+1}: комната {cur.room_id+1} "
                    f"({prev.start_dt:%H:%M}-{prev.end_dt:%H:%M}, "
                    f"{cur.start_dt:%H:%M}-{cur.end_dt:%H:%M}), разрыв {gap:.0f} мин"
                )
    return errors

def main():
    all_ok = True
    for num_rooms in [8, 12, 20, 40]:
        for employees in [2, 3, 4]:
            p = make_project(num_rooms, employees)
            tasks = plan_cleaning_schedule(p)
            errors = find_duplicate_cleaning(tasks)
            scheduled_rooms = {t.room_id for t in tasks}
            pct = f"({len(scheduled_rooms)}/{num_rooms})" if tasks else "(нет задач)"
            print(f"\nКомнат: {num_rooms}, сотрудников: {employees} → задач: {len(tasks)}, обслужено: {pct}")
            if errors:
                print("  !!! ДУБЛИРОВАНИЯ:")
                for e in errors[:5]:
                    print(f"    {e}")
                all_ok = False
            else:
                print("  OK: дублирований нет")
    print("\n" + ("Все тесты пройдены!" if all_ok else "ЕСТЬ ПРОБЛЕМЫ"))
    return all_ok

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)