from datetime import date

import pytest

from project import Project, Floor, Room, Shift
from scheduler import plan_cleaning_period, schedule_single_shift
from schedule_validator import validate_schedule
from report_generator import generate_report, ReportGenerationError
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day


def make_project(days=1, employees=2):
    p = Project("Regression")
    p.start_date = date(2026, 8, 25)
    p.end_date = p.start_date.fromordinal(p.start_date.toordinal() + days - 1)
    p.employees_count = employees
    p.employee_names = [f"Сотрудник {i+1}" for i in range(employees)]
    p.shifts = [Shift("Основная", "09:00", "18:00")]
    p.breaks = [("12:00", "13:00")]
    return p


def add_room(floor, rid, room_type, area=20, x=0, y=0):
    r = Room(rid, [(x, y), (x + 10, y), (x + 10, y + 10), (x, y + 10)], area_m2=area)
    r.room_type = room_type
    r.name = f"{room_type} {rid+1}"
    floor.rooms.append(r)
    return r


def test_scheduler_is_explicitly_one_day_even_if_project_has_date_range():
    p = make_project(days=7, employees=4)
    f = Floor(0, "Этаж 1")
    add_room(f, 0, "склад", 20)
    p.floors = [f]
    plan_cleaning_period(p, employees=4)
    v = validate_schedule(p)
    assert v["valid"]
    assert v["period_days"] == 1
    assert v["expected_tasks"] == 1
    assert len(p.cleaning_tasks) == 1
    assert {t.start_dt.date() for t in p.cleaning_tasks} == {p.start_date}


def test_validator_does_not_require_future_days_for_one_day_schedule():
    p = make_project(days=7, employees=2)
    f = Floor(0, "Этаж 1")
    add_room(f, 0, "склад", 20)
    p.floors = [f]
    schedule_single_shift(p, target_date=p.start_date, employees=2)
    v = validate_schedule(p)
    assert v["valid"]
    assert v["missing_cleanings"] == 0

def test_interfloor_transit_is_respected():
    p = make_project(days=1, employees=1)
    f1, f2 = Floor(0, "Этаж 1"), Floor(1, "Этаж 2")
    add_room(f1, 0, "склад", 20)
    add_room(f2, 0, "склад", 20)
    p.floors = [f1, f2]
    schedule_single_shift(p, target_date=p.start_date, employees=1)
    v = validate_schedule(p)
    assert v["transit_violations"] == 0
    assert v["valid"]


def test_validator_accepts_one_day_schedule_when_project_range_is_longer():
    p = make_project(days=2, employees=2)
    f = Floor(0, "Этаж 1")
    add_room(f, 0, "склад", 20)
    p.floors = [f]
    schedule_single_shift(p, target_date=p.start_date, employees=2)
    v = validate_schedule(p)
    assert v["valid"]
    assert v["period_days"] == 1



def test_invalid_production_report_is_blocked(tmp_path):
    p = make_project(days=1, employees=1)
    f = Floor(0, "Этаж 1")
    add_room(f, 0, "склад", 20)
    add_room(f, 1, "склад", 20, x=20)
    p.floors = [f]
    schedule_single_shift(p, target_date=p.start_date, employees=1)
    p.cleaning_tasks = p.cleaning_tasks[:-1]
    assert not validate_schedule(p)["valid"]
    with pytest.raises(ReportGenerationError):
        generate_report(p, tmp_path / "invalid.docx")


def test_missing_source_plan_is_blocked(tmp_path):
    p = make_project(days=1, employees=1)
    f = Floor(0, "Этаж 1")
    f.image_path = "does-not-exist.png"
    add_room(f, 0, "склад", 20)
    p.floors = [f]
    schedule_single_shift(p, target_date=p.start_date, employees=1)
    assert validate_schedule(p)["valid"]
    with pytest.raises(ReportGenerationError):
        generate_report(p, tmp_path / "missing-plan.docx")


def test_norms_are_stable():
    assert get_cleaning_time_minutes("кабинет", 20) > 0
    assert get_cleaning_time_minutes("зал", 100, 1.0, "генеральная") == 2 * get_cleaning_time_minutes("зал", 100, 1.0, "поддерживающая")
    assert get_frequency_per_day("санузел") >= 1


def test_adaptive_staffing_small_object_is_one():
    p = make_project(days=7, employees=1)
    f = Floor(0, "Этаж 1")
    add_room(f, 0, "склад", 20)
    p.floors = [f]
    from cost_calculator import estimate_required_employees
    result = estimate_required_employees(p)
    assert result["employees"] == 1
    assert result["feasible"]


def test_adaptive_staffing_stable_snow_stays_reasonable():
    from datetime import date
    from project import Shift
    from cost_calculator import estimate_required_employees
    import glob
    files = glob.glob("projects/*.json")
    path = next(path for path in files if Project.load_from_file(path).name == "СтабильныйБилдд")
    p = Project.load_from_file(path)
    p.priority_mode = "proximity"
    p.weather_factor = 1.5
    p.salary_type = "hour"
    p.salary_value = 1000.0
    p.overtime_type = "per_hour"
    p.overtime_value = 150.0
    p.shifts = [Shift("Основная", "09:00", "19:00")]
    p.breaks = [("12:00", "13:00")]
    p.start_date = date(2026, 8, 5)
    p.end_date = p.start_date
    result = estimate_required_employees(p)
    assert 6 <= result["employees"] <= 10
    assert result["minimum_feasible"] is not None


def test_no_large_artificial_gap_from_release_target():
    from project import Shift
    from scheduler import schedule_single_shift
    p = make_project(days=1, employees=2)
    p.shifts = [Shift("Основная", "09:00", "19:00")]
    p.breaks = [("12:00", "13:00")]
    f = Floor(0, "Этаж 1")
    for rid, x in enumerate((0, 20, 40, 60, 80, 100)):
        add_room(f, rid, "склад", 20, x=x)
    p.floors = [f]
    schedule_single_shift(p, target_date=p.start_date, employees=2)
    for emp in range(2):
        tasks = sorted([t for t in p.cleaning_tasks if t.employee == emp], key=lambda t: t.start_dt)
        for a, b in zip(tasks, tasks[1:]):
            gap = (b.start_dt - a.end_dt).total_seconds() / 60
            # Ланч исключается из искусственного ожидания.
            if a.end_dt.hour * 60 + a.end_dt.minute <= 720 and b.start_dt.hour * 60 + b.start_dt.minute >= 780:
                gap -= 60
            assert gap <= 120


def test_time_priority_balances_load_and_common_finish():
    from scheduler import schedule_single_shift
    from zone_manager import distribute_project_zones
    p = make_project(days=1, employees=3)
    p.priority_mode = "time"
    p.shifts = [Shift("Основная", "09:00", "19:00")]
    p.breaks = [("12:00", "13:00")]
    f = Floor(0, "Этаж 1")
    for rid, x in enumerate((0, 20, 40, 60, 80, 100, 120, 140, 160)):
        add_room(f, rid, "склад", 20, x=x)
    p.floors = [f]
    p.zones = distribute_project_zones(p, 3, "time")
    result = schedule_single_shift(p, target_date=p.start_date, employees=3)
    assert result["validation"]["valid"]
    assert result["end_time_spread_minutes"] == 0
    assert result["working_load_spread_minutes"] <= 15


def test_fixed_room_time_and_employee_survive_recalculation():
    from scheduler import schedule_single_shift
    from zone_manager import distribute_project_zones
    p = make_project(days=1, employees=2)
    p.priority_mode = "time"
    p.shifts = [Shift("Основная", "09:00", "19:00")]
    p.breaks = [("12:00", "13:00")]
    f = Floor(0, "Этаж 1")
    for rid, x in enumerate((0, 20, 40, 60, 80, 100)):
        add_room(f, rid, "склад", 20, x=x)
    p.floors = [f]
    p.zones = distribute_project_zones(p, 2, "time")
    p.manual_assignments = {"0:0": 1}
    p.schedule_locks = {"0:0:0": {"employee": 1, "start": "11:00", "fixed": True}}

    first = schedule_single_shift(p, target_date=p.start_date, employees=2)
    assert first["validation"]["valid"]
    locked = next(t for t in p.cleaning_tasks if t.room_id == 0)
    assert locked.employee == 1
    assert locked.start_dt.strftime("%H:%M") == "11:00"
    assert getattr(locked, "fixed", False)

    # Пересчёт не должен сдвинуть зафиксированную задачу.
    second = schedule_single_shift(p, target_date=p.start_date, employees=2)
    assert second["validation"]["valid"]
    locked_again = next(t for t in p.cleaning_tasks if t.room_id == 0)
    assert locked_again.employee == 1
    assert locked_again.start_dt.strftime("%H:%M") == "11:00"


def test_schedule_locks_round_trip(tmp_path):
    p = make_project(days=1, employees=2)
    p.schedule_locks = {"0:2:1": {"employee": 1, "start": "15:20", "fixed": True}}
    path = tmp_path / "locked.json"
    p.save_to_file(path)
    restored = Project.load_from_file(path)
    assert restored.schedule_locks == p.schedule_locks
