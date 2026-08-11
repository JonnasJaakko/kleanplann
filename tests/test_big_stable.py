"""Проверка генерации расписания на реальном big_stable.json (без огромных команд)."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project import Project
from scheduler import plan_cleaning_schedule

def main():
    p = Project.load_from_file('projects/big_stable.json')
    print(f"сотрудников: {p.employees_count}, комнат: {len(p.all_rooms())}, "
          f"смены: {[(s.start_time, s.end_time) for s in p.shifts]}, "
          f"перерывы: {p.breaks}")

    start = time.time()
    try:
        tasks = plan_cleaning_schedule(p)
    except RecursionError:
        print("ОШИБКА: RecursionError (возможен бесконечный цикл)")
        return
    dt = time.time() - start
    print(f"задач: {len(tasks)}, время: {dt:.2f} с")
    print(f"сотрудников после: {p.employees_count}, зон: {len(p.zones)}")
    if tasks:
        by_emp = {}
        for t in tasks:
            by_emp.setdefault(t.employee, []).append(t)
        for e, ts in sorted(by_emp.items()):
            print(f"  сотрудник {e+1}: {len(ts)} задач")

if __name__ == "__main__":
    main()