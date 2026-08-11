"""
Проверка: расписание не должно содержать коллизий —
один сотрудник не может убирать две комнаты в одно и то же время.
Также проверяется, что каждая активная комната получила уборку.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, timedelta
from project import Project, Room, Floor, Zone, Shift
from scheduler import plan_cleaning_schedule


def make_project():
    p = Project("test")
    p.employees_count = 2
    p.employee_names = ["Сотрудник 1", "Сотрудник 2"]
    p.start_date = date(2026, 1, 1)
    p.end_date = date(2026, 1, 1)
    p.shifts = [Shift("Основная", "08:00", "22:00")]
    p.breaks = [("12:00", "13:00")]

    # Сетка 3x2: 6 комнат, каждая 10x10 = 100 м²
    floor = p.floors[0]
    rooms = []
    w, h = 10.0, 10.0
    for i in range(6):
        x = (i % 3) * w
        y = (i // 3) * h
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        rooms.append(Room(i, pts, area_m2=w * h, traffic=30,
                          room_type="кабинет", name=f"Комната {i+1}"))
    floor.rooms = rooms

    # Зоны: 3 комнаты на сотрудника
    p.zones = [
        Zone(0, "Зона 1", [0, 1, 2], employee_index=0),
        Zone(1, "Зона 2", [3, 4, 5], employee_index=1),
    ]
    return p


def check_no_collisions(tasks):
    """Для каждого сотрудника интервалы уборок не должны пересекаться."""
    by_emp = {}
    for t in tasks:
        by_emp.setdefault(t.employee, []).append(t)
    errors = []
    for emp, emp_tasks in sorted(by_emp.items()):
        emp_tasks.sort(key=lambda t: t.start_dt)
        prev_end = None
        for t in emp_tasks:
            if prev_end is not None and t.start_dt < prev_end:
                errors.append(
                    f"Коллизия у сотрудника {emp+1}: задача с {t.start_dt:%H:%M} "
                    f"пересекается с предыдущей (конец {prev_end:%H:%M})"
                )
            prev_end = max(prev_end, t.end_dt) if prev_end else t.end_dt
    return errors


def main():
    p = make_project()
    tasks = plan_cleaning_schedule(p)
    print(f"Всего задач: {len(tasks)}")
    for emp in range(p.employees_count):
        emp_tasks = [t for t in tasks if t.employee == emp]
        emp_tasks.sort(key=lambda t: t.start_dt)
        print(f"\nСотрудник {emp+1}: {len(emp_tasks)} задач")
        for t in emp_tasks:
            dur = (t.end_dt - t.start_dt).total_seconds() // 60
            print(f"  {t.start_dt:%H:%M}-{t.end_dt:%H:%M} ({dur:.0f} мин) комната {t.room_id+1}")

    # Коллизии
    errors = check_no_collisions(tasks)
    if errors:
        print("\n!!! НАЙДЕНЫ КОЛЛИЗИИ:")
        for e in errors:
            print(" ", e)
        sys.exit(1)

    # Каждая активная комната должна быть обслужена
    scheduled_rooms = {t.room_id for t in tasks}
    expected_rooms = {0, 1, 2, 3, 4, 5}
    missing = expected_rooms - scheduled_rooms
    if missing:
        print(f"\n!!! НЕ ОБСЛУЖЕНЫ КОМНАТЫ: {[m+1 for m in sorted(missing)]}")
        sys.exit(1)

    print("\nOK: коллизий нет, все комнаты обслужены")


if __name__ == "__main__":
    main()