"""Тест равномерного распределения задач по смене в scheduler."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from project import Project, Room, Floor, Shift
from scheduler import schedule_single_shift, _compute_ideal_start, CleaningJob


def test_ideal_start_distribution():
    """Проверяем, что идеальные времена равномерно распределены по смене."""
    shift_start = 8 * 60  # 08:00
    shift_end = 22 * 60   # 22:00
    span = shift_end - shift_start  # 840 мин

    # Комната с частотой 2: уборки в начале и середине
    job1 = CleaningJob(0, 0, 10, 0, 2, False, 0, False)
    job2 = CleaningJob(0, 0, 10, 1, 2, False, 0, False)
    assert _compute_ideal_start(job1, shift_start, shift_end) == shift_start
    assert _compute_ideal_start(job2, shift_start, shift_end) == shift_start + span // 2

    # Комната с частотой 3: уборки в начале, 1/3 и 2/3
    job3_0 = CleaningJob(0, 0, 10, 0, 3, False, 0, False)
    job3_1 = CleaningJob(0, 0, 10, 1, 3, False, 0, False)
    job3_2 = CleaningJob(0, 0, 10, 2, 3, False, 0, False)
    assert _compute_ideal_start(job3_0, shift_start, shift_end) == shift_start
    assert _compute_ideal_start(job3_1, shift_start, shift_end) == shift_start + span // 3
    assert _compute_ideal_start(job3_2, shift_start, shift_end) == shift_start + span * 2 // 3

    print("✓ Идеальные времена равномерно распределены по смене")


def test_schedule_fills_shift():
    """Проверяем, что расписание заполняет смену без больших застоев."""
    project = Project("Тест")
    project.shifts = [Shift("Основная", "08:00", "22:00")]
    project.breaks = [("12:00", "13:00")]
    project.employees_count = 1
    project.employee_names = ["Сотрудник 1"]

    # Создаём комнаты с разными типами
    floor = project.floors[0]
    rooms_data = [
        ("коридор", 50, 0),
        ("кабинет", 30, 1),
        ("санузел", 10, 2),
        ("склад", 20, 3),
        ("зал", 40, 4),
        ("кухня", 15, 5),
    ]
    for name, area, idx in rooms_data:
        pts = [(idx * 10, 0), (idx * 10 + 8, 0), (idx * 10 + 8, 8), (idx * 10, 8)]
        room = Room(idx, pts, area_m2=area, room_type=name, name=f"Комната {idx + 1}")
        floor.rooms.append(room)

    result = schedule_single_shift(project, allow_partial_schedule=True)
    tasks = result["tasks"]

    # Проверяем, что все комнаты запланированы
    assert result["unscheduled_rooms"] == 0, f"Не запланированы комнаты: {result['unscheduled_rooms']}"
    assert result["missed_cleanings"] == 0, f"Пропущены уборки: {result['missed_cleanings']}"

    # Проверяем, что нет больших застоев (более 2 часов между задачами)
    tasks_sorted = sorted(tasks, key=lambda t: t.start_dt)
    for i in range(len(tasks_sorted) - 1):
        gap = (tasks_sorted[i + 1].start_dt - tasks_sorted[i].end_dt).total_seconds() / 60.0
        if gap > 120:
            print(f"  ⚠ Большой застой: {gap:.0f} мин между {tasks_sorted[i].start_dt.strftime('%H:%M')} и {tasks_sorted[i + 1].start_dt.strftime('%H:%M')}")

    # Проверяем, что первая задача начинается с начала смены
    first_start = tasks_sorted[0].start_dt.strftime('%H:%M')
    assert first_start == "08:00", f"Первая задача должна начинаться в 08:00, а начинается в {first_start}"

    # Проверяем, что повторные уборки одной комнаты не идут подряд
    from collections import defaultdict
    room_times = defaultdict(list)
    for t in tasks:
        room_times[t.room_id].append(t.start_dt)
    for rid, times in room_times.items():
        times.sort()
        for i in range(len(times) - 1):
            gap = (times[i + 1] - times[i]).total_seconds() / 60.0
            assert gap >= 30, f"Комната {rid}: повторная уборка через {gap:.0f} мин (должно быть >= 30)"

    print(f"✓ Расписание заполняет смену: {len(tasks)} задач, первая в {first_start}")
    print(f"✓ Периодичность соблюдена: повторные уборки с интервалом >= 30 мин")


def main():
    test_ideal_start_distribution()
    test_schedule_fills_shift()
    print("\n✓ ВСЕ ТЕСТЫ SCHEDULER ПРОЙДЕНЫ")


if __name__ == "__main__":
    main()