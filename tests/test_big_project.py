"""
Стресс-тест: много комнат, мало сотрудников.
Проверяет, что алгоритм корректно увеличивает сотрудников 
и не уходит в бесконечный цикл.
"""
import sys, os, math, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from project import Project, Room, Floor, Zone, Shift, CleaningTask
from scheduler import plan_cleaning_schedule
from sanitarnorm import COMPLEXITY_FACTOR

random.seed(42)

def make_big_project(num_rooms=60, initial_employees=3):
    p = Project("big_test")
    p.employees_count = initial_employees
    p.employee_names = [f"Сотрудник {i+1}" for i in range(initial_employees)]
    p.start_date = date(2026, 1, 1)
    p.end_date = date(2026, 1, 1)
    p.shifts = [Shift("Основная", "08:00", "22:00")]
    p.breaks = [("12:00", "13:00")]
    p.weather_factor = 1.0
    
    floor = p.floors[0]
    rooms = []
    types = list(COMPLEXITY_FACTOR.keys())
    grid_cols = 8
    cell_w, cell_h = 12.0, 12.0  # ~144 м² каждая комната
    for i in range(num_rooms):
        col = i % grid_cols
        row = i // grid_cols
        x = col * cell_w + random.uniform(-0.2, 0.2)
        y = row * cell_h + random.uniform(-0.2, 0.2)
        pts = [(x,y), (x+cell_w,y), (x+cell_w,y+cell_h), (x,y+cell_h)]
        room_type = random.choice(types)
        rooms.append(Room(i, pts, area_m2=cell_w*cell_h, traffic=30,
                          room_type=room_type, name=f"Комната {i+1}"))
    floor.rooms = rooms
    
    # Начальное распределение зон: все комнаты одному сотруднику
    p.zones = [Zone(0, "Зона 1", list(range(num_rooms)), employee_index=0)]
    if initial_employees > 1:
        split = num_rooms // initial_employees
        for i in range(1, initial_employees):
            start = i * split
            end = num_rooms if i == initial_employees - 1 else (i+1) * split
            p.zones.append(Zone(i, f"Зона {i+1}", list(range(start, end)), employee_index=i))
    
    return p

def check_no_collisions(tasks):
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
    for num_rooms in [30, 60, 120]:
        for init_emp in [2, 3, 5]:
            print(f"\n{'='*60}")
            print(f"Тест: {num_rooms} комнат, начально {init_emp} сотрудников")
            print('='*60)
            try:
                p = make_big_project(num_rooms, init_emp)
                tasks = plan_cleaning_schedule(p)
                print(f"  Итоговое число сотрудников: {p.employees_count}")
                print(f"  Всего задач: {len(tasks)}")
                scheduled_rooms = {t.room_id for t in tasks}
                print(f"  Обслужено комнат: {len(scheduled_rooms)} из {num_rooms}")
                
                errors = check_no_collisions(tasks)
                if errors:
                    print(f"  !!! КОЛЛИЗИИ: {len(errors)}")
                    for e in errors[:5]:
                        print(f"    {e}")
                    return False
                else:
                    print(f"  OK: коллизий нет")
            except Exception as e:
                print(f"  !!! ИСКЛЮЧЕНИЕ: {e}")
                import traceback
                traceback.print_exc()
                return False
    print(f"\n{'='*60}")
    print("Все тесты пройдены успешно!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)