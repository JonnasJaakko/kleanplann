# scheduler.py
"""
Практический планировщик уборки.

Главные принципы:
1. Нормативное время и кратность берутся только из sanitarnorm.
2. Каждая требуемая уборка создаётся как отдельная задача.
3. Обед является жёстким недоступным интервалом.
4. Внутри смены задачи стараются распределяться равномерно между всеми
   указанными сотрудниками, но без искусственного создания работы.
5. Если задача физически не помещается в обычную смену, она переносится
   после окончания смены и получает is_overtime=True. Такая задача
   считается допустимой переработкой и в отчёте должна быть подсвечена
   светло-красным.
6. Зоны ответственности являются предпочтительным сотрудником, но
   scheduler может передать отдельную уборку другому сотруднику, если
   это необходимо для выполнимости/баланса.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import math

import sanitarnorm
from project import CleaningTask


# Backward-compatible lightweight job used by older tests/integrations.
class CleaningJob:
    def __init__(self, room_id, floor_index, duration, occurrence,
                 frequency, priority=False, employee=0, is_overtime=False):
        self.room_id = room_id
        self.floor_index = floor_index
        self.duration = duration
        self.occurrence = occurrence
        self.frequency = frequency
        self.priority = priority
        self.employee = employee
        self.is_overtime = is_overtime


def _compute_ideal_start(job, shift_start, shift_end):
    """Совместимый helper: равномерная точка внутри календарной смены."""
    span = shift_end - shift_start
    if getattr(job, "frequency", 1) <= 1:
        return shift_start
    return shift_start + span * getattr(job, "occurrence", 0) // getattr(job, "frequency", 1)


# Минимальный технологический интервал между двумя уборками одного помещения.
# Это не норматив кратности: кратность задаётся sanitarnorm.
COOLDOWN_BY_TYPE = {
    "санузел": 180,
    "кухня": 180,
    "коридор": 120,
    "default": 90,
}

TRANSIT_SAME_FLOOR = 1
TRANSIT_TO_OTHER_FLOOR = 5
TRANSIT_TOILET = 3


def _type_key(room_type: str) -> str:
    s = (room_type or "").lower()
    for key in ("санузел", "кухня", "коридор"):
        if key in s:
            return key
    return "default"


def _get_cooldown(room_type: str) -> int:
    return COOLDOWN_BY_TYPE[_type_key(room_type)]


def _get_transit_minutes(prev_room_type: str, prev_floor: int,
                         next_floor: int) -> int:
    if prev_room_type and _type_key(prev_room_type) == "санузел":
        return TRANSIT_TOILET
    if prev_floor != next_floor:
        return TRANSIT_TO_OTHER_FLOOR
    return TRANSIT_SAME_FLOOR


def _to_minutes(value: str) -> int:
    h, m = map(int, value.split(":"))
    return h * 60 + m


def _time_to_datetime(base_date, minute_from_midnight: int) -> datetime:
    return datetime.combine(
        base_date,
        datetime.min.time()
    ) + timedelta(minutes=minute_from_midnight)


def _regular_windows(project) -> Tuple[int, int, List[Tuple[int, int]]]:
    """Возвращает начало/конец смены и доступные интервалы без обеда."""
    if not project.shifts:
        start, end = 9 * 60, 19 * 60
    else:
        shift = project.shifts[0]
        start = _to_minutes(shift.start_time)
        end = _to_minutes(shift.end_time)

    if end <= start:
        raise ValueError("Конец смены должен быть позже начала смены")

    windows = [(start, end)]

    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs, be = _to_minutes(b_start), _to_minutes(b_end)
        except Exception:
            continue
        if be <= bs:
            continue

        new_windows = []
        for ws, we in windows:
            if be <= ws or bs >= we:
                new_windows.append((ws, we))
                continue
            if ws < bs:
                new_windows.append((ws, min(bs, we)))
            if be < we:
                new_windows.append((max(be, ws), we))
        windows = [(a, b) for a, b in new_windows if b > a]

    return start, end, windows


def _build_targets(frequency: int, windows: List[Tuple[int, int]]) -> List[int]:
    """
    Равномерно раскладывает повторные уборки по рабочему времени,
    а не по календарному времени смены.

    Например 09:00–19:00 + обед 12:00–13:00:
      freq=2 -> примерно 09:00 и 16:00
      freq=3 -> примерно 09:00, 13:30, 18:00
    """
    if frequency <= 1:
        return [windows[0][0]]

    total = sum(b - a for a, b in windows)
    if total <= 0:
        return [windows[0][0]] * frequency

    # Равномерные точки по рабочей шкале.
    points = []
    for i in range(frequency):
        offset = round(total * i / frequency)
        remaining = offset
        chosen = windows[-1][0]
        for ws, we in windows:
            length = we - ws
            if remaining <= length:
                chosen = ws + remaining
                break
            remaining -= length
        points.append(int(chosen))
    return points


def _zone_map(project) -> Dict[Tuple[int, int], int]:
    """room key -> preferred employee."""
    mapping = {}
    for zone in getattr(project, "zones", []) or []:
        fi = getattr(zone, "floor_index", 0)
        emp = getattr(zone, "employee_index", 0)
        for rid in getattr(zone, "room_ids", []) or []:
            mapping[(fi, rid)] = emp
    return mapping


def _room_map(project):
    return {
        (fi, room.id): room
        for fi, floor in enumerate(project.floors)
        for room in floor.rooms
    }


def _safe_employee_count(project, employees: Optional[int]) -> int:
    requested = employees if employees is not None else project.employees_count
    requested = max(1, int(requested))

    max_zone_emp = -1
    for z in getattr(project, "zones", []) or []:
        max_zone_emp = max(max_zone_emp, getattr(z, "employee_index", 0))

    return max(requested, max_zone_emp + 1)


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


def _room_is_available(start: int, room_history: List[Tuple[int, int]],
                       cooldown: int) -> bool:
    for prev_start, prev_end in room_history:
        if start < prev_end + cooldown:
            return False
    return True


def _find_regular_slot(
    desired: int,
    duration: int,
    occupied: List[Tuple[int, int]],
    room_history: List[Tuple[int, int]],
    cooldown: int,
    windows: List[Tuple[int, int]],
) -> Optional[Tuple[int, int]]:
    """
    Ищет ближайшее допустимое место в обычной смене.
    Предпочитает минимальное отклонение от целевого времени.
    """
    candidates = []

    # Формируем свободные куски каждого рабочего окна.
    for ws, we in windows:
        blocked = sorted(
            [(a, b) for a, b in occupied if _overlap(a, b, ws, we)],
            key=lambda x: x[0]
        )

        cursor = ws
        gaps = []
        for a, b in blocked:
            if a > cursor:
                gaps.append((cursor, min(a, we)))
            cursor = max(cursor, b)
        if cursor < we:
            gaps.append((cursor, we))

        for gs, ge in gaps:
            if ge - gs < duration:
                continue

            # Несколько естественных кандидатов: target, начало и конец gap.
            starts = {
                max(gs, min(desired, ge - duration)),
                gs,
                ge - duration,
            }
            for start in starts:
                end = start + duration
                if end > ge:
                    continue
                if _room_is_available(start, room_history, cooldown):
                    candidates.append((abs(start - desired), start, end))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    _, start, end = candidates[0]
    return start, end


def _find_overtime_slot(
    desired: int,
    duration: int,
    occupied: List[Tuple[int, int]],
    room_history: List[Tuple[int, int]],
    cooldown: int,
    shift_end: int,
) -> Tuple[int, int]:
    """
    Overtime никогда не начинается внутри обеда и не обрывается.
    По умолчанию задача ставится после последней задачи сотрудника,
    но не раньше конца смены.
    """
    start = max(shift_end, desired)
    if occupied:
        start = max(start, max(b for _, b in occupied))

    # Важное практическое правило: если уборка уже не помещается
    # в обычную смену, она выполняется сразу после последней работы
    # сотрудника в смене. Периодичность между повторными уборками
    # здесь намеренно НЕ применяется: это уже переработка.
    return start, start + duration


def _score_candidate(
    employee: int,
    preferred_employee: int,
    start: int,
    end: int,
    desired: int,
    load_minutes: Dict[int, float],
    target_load: float,
    zone_penalty: float = 10.0,
) -> float:
    new_load = load_minutes[employee] + (end - start)
    current_load = load_minutes[employee]
    balance = abs(new_load - target_load)
    lateness = abs(start - desired)
    preference = 0.0 if employee == preferred_employee else zone_penalty
    # Сначала не допускаем накопления задач у одного сотрудника.
    # Затем учитываем целевую загрузку и близость к нормативному времени.
    return current_load * 0.75 + balance * 0.25 + lateness * 0.45 + preference


def _attach_task_metadata(task: CleaningTask, *, is_overtime: bool,
                          transit_after: int, priority: bool = False):
    # Оставляем совместимость со старой моделью CleaningTask.
    task.is_overtime = bool(is_overtime)
    task.transit_after_minutes = int(max(0, transit_after))
    task.priority = bool(priority)


def schedule_single_shift(project, employees: Optional[int] = None,
                          allow_partial_schedule: bool = True):
    """
    Генерирует расписание на один день.

    Все требуемые уборки создаются обязательно. Если регулярное окно
    закончилось, уборка переносится в сверхурочное время.
    """
    shift_start, shift_end, windows = _regular_windows(project)
    base_date = project.start_date
    if isinstance(base_date, str):
        # Совместимость со старыми сохранениями.
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                base_date = datetime.strptime(base_date, fmt).date()
                break
            except ValueError:
                continue
        else:
            base_date = datetime.today().date()

    emp_count = _safe_employee_count(project, employees)
    room_by_key = _room_map(project)
    preferred = _zone_map(project)

    # Если комната не распределена по зоне, назначаем её на наименее
    # загруженного сотрудника позже. Она не должна исчезнуть из расписания.
    active_rooms = [
        (key, room)
        for key, room in room_by_key.items()
        if not getattr(room, "disabled", False)
    ]

    # Создаём все повторные уборки.
    jobs = []
    for (fi, rid), room in active_rooms:
        frequency = max(
            1,
            int(round(
                sanitarnorm.get_frequency_per_day(room.room_type)
                * getattr(project, "weather_factor", 1.0)
            ))
        )
        duration = max(
            1,
            int(math.ceil(
                sanitarnorm.get_cleaning_time_minutes(
                    room.room_type,
                    room.area_m2,
                    getattr(project, "weather_factor", 1.0),
                    getattr(project, "cleaning_type", "поддерживающая"),
                )
            ))
        )

        targets = _build_targets(frequency, windows)
        for occurrence, target in enumerate(targets):
            jobs.append({
                "floor_index": fi,
                "room_id": rid,
                "room": room,
                "duration": duration,
                "frequency": frequency,
                "occurrence": occurrence,
                "target": target,
                "priority": bool(getattr(room, "priority", False)),
                "preferred_employee": preferred.get((fi, rid)),
            })

    # Сначала обязательные/частые/длинные работы. Внутри одного уровня
    # сохраняем временную шкалу, чтобы повторные уборки не "съезжали".
    jobs.sort(
        key=lambda j: (
            not j["priority"],
            j["target"],
            -j["duration"],
            -j["frequency"],
        )
    )

    occupied = {i: [] for i in range(emp_count)}
    load_minutes = {i: 0.0 for i in range(emp_count)}
    room_history: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    employee_last_room = {i: None for i in range(emp_count)}
    employee_last_floor = {i: 0 for i in range(emp_count)}
    employee_transit = {i: 0 for i in range(emp_count)}

    total_cleaning = sum(j["duration"] for j in jobs)
    target_load = total_cleaning / emp_count if emp_count else total_cleaning

    final_tasks: List[CleaningTask] = []
    overtime_tasks = []

    for job in jobs:
        key = (job["floor_index"], job["room_id"])
        history = room_history.setdefault(key, [])
        cooldown = _get_cooldown(job["room"].room_type)
        preferred_emp = job["preferred_employee"]
        if preferred_emp is None or not (0 <= preferred_emp < emp_count):
            # Нераспределённая комната: на этом этапе предпочтения нет.
            preferred_emp = min(load_minutes, key=load_minutes.get)

        candidates = []

        # Зона ответственности является жёстким ограничением: уборка
        # помещения не передаётся другому сотруднику. Если помещение не
        # закреплено ни за одной зоной, тогда допускается балансировка.
        employee_candidates = [preferred_emp] if job["preferred_employee"] is not None else list(range(emp_count))

        # В первую очередь пытаемся поставить работу в обычную смену.
        for emp in employee_candidates:
            prev_room = employee_last_room[emp]
            prev_floor = employee_last_floor[emp]
            transit = 0
            if prev_room is not None:
                transit = _get_transit_minutes(
                    prev_room.room_type,
                    prev_floor,
                    job["floor_index"],
                )

            # Учитываем переход как часть занятости сотрудника.
            occupied_with_transit = list(occupied[emp])
            if occupied_with_transit and prev_room is not None:
                last_end = max(b for _, b in occupied_with_transit)
                occupied_with_transit.append((last_end, last_end + transit))

            slot = _find_regular_slot(
                job["target"],
                job["duration"],
                occupied_with_transit,
                history,
                cooldown,
                windows,
            )
            if slot is not None:
                start, end = slot
                score = _score_candidate(
                    emp,
                    preferred_emp,
                    start,
                    end,
                    job["target"],
                    load_minutes,
                    target_load,
                )
                # Приоритетная работа дополнительно стремится к целевому времени.
                if job["priority"]:
                    score -= max(0, 25 - abs(start - job["target"])) * 0.5
                candidates.append((score, emp, start, end, False, transit))

        if candidates:
            candidates.sort(key=lambda x: (x[0], x[2], x[1]))
            _, emp, start, end, is_overtime, transit = candidates[0]
        else:
            # Если в обычной смене места нет, работа обязана попасть
            # в расписание сверх смены.
            overtime_candidates = []
            employee_candidates = [preferred_emp] if job["preferred_employee"] is not None else list(range(emp_count))
            for emp in employee_candidates:
                start, end = _find_overtime_slot(
                    job["target"],
                    job["duration"],
                    occupied[emp],
                    history,
                    cooldown,
                    shift_end,
                )
                overtime_candidates.append(
                    (
                        abs(start - max(shift_end, job["target"]))
                        + abs(
                            (load_minutes[emp] + (end - start))
                            - target_load
                        ),
                        emp,
                        start,
                        end,
                    )
                )

            overtime_candidates.sort(key=lambda x: (x[0], x[2], x[1]))
            _, emp, start, end = overtime_candidates[0]
            is_overtime = True

            prev_room = employee_last_room[emp]
            transit = 0
            if prev_room is not None:
                transit = _get_transit_minutes(
                    prev_room.room_type,
                    employee_last_floor[emp],
                    job["floor_index"],
                )
            overtime_tasks.append((job, emp, start, end))

        # Встраиваем переход перед задачей только если он реально нужен.
        if occupied[emp]:
            previous_end = max(b for _, b in occupied[emp])
            if previous_end < start:
                transit_needed = _get_transit_minutes(
                    employee_last_room[emp].room_type
                    if employee_last_room[emp] is not None else "",
                    employee_last_floor[emp],
                    job["floor_index"],
                )
                # Если переход не помещается в regular slot, переносим задачу.
                if not is_overtime and previous_end + transit_needed > start:
                    # Пытаемся найти ещё один regular slot с учётом перехода.
                    occupied2 = list(occupied[emp]) + [
                        (previous_end, previous_end + transit_needed)
                    ]
                    alt = _find_regular_slot(
                        job["target"], job["duration"], occupied2,
                        history, cooldown, windows
                    )
                    if alt is not None:
                        start, end = alt
                    else:
                        start, end = _find_overtime_slot(
                            job["target"], job["duration"],
                            occupied[emp], history, cooldown, shift_end
                        )
                        is_overtime = True

        # Последний safety-check: задача не должна пересекать обед.
        if not is_overtime:
            if any(_overlap(start, end, bs, be)
                   for bs, be in _break_intervals(project, shift_start, shift_end)):
                start, end = _find_overtime_slot(
                    job["target"], job["duration"],
                    occupied[emp], history, cooldown, shift_end
                )
                is_overtime = True

        t_start = _time_to_datetime(base_date, start)
        t_end = _time_to_datetime(base_date, end)

        task = CleaningTask(
            room_id=job["room_id"],
            floor_index=job["floor_index"],
            start_dt=t_start,
            end_dt=t_end,
            employee=emp,
        )
        _attach_task_metadata(
            task,
            is_overtime=is_overtime,
            transit_after=transit,
            priority=job["priority"],
        )
        final_tasks.append(task)

        occupied[emp].append((start, end + transit))
        history.append((start, end))
        history.sort()
        load_minutes[emp] += job["duration"]
        employee_transit[emp] += transit
        employee_last_room[emp] = job["room"]
        employee_last_floor[emp] = job["floor_index"]

    # Удаляем старые задачи и сохраняем результат в проекте.
    final_tasks.sort(key=lambda t: (t.employee, t.start_dt))
    project.cleaning_tasks = final_tasks

    # Аналитика по сотрудникам.
    employee_analytics = {}
    regular_capacity = sum(b - a for a, b in windows)
    for emp in range(emp_count):
        regular_clean = sum(
            (t.end_dt - t.start_dt).total_seconds() / 60
            for t in final_tasks
            if t.employee == emp and not getattr(t, "is_overtime", False)
        )
        overtime = sum(
            (t.end_dt - t.start_dt).total_seconds() / 60
            for t in final_tasks
            if t.employee == emp and getattr(t, "is_overtime", False)
        )
        employee_analytics[emp] = {
            "cleaning_minutes": round(regular_clean, 1),
            "overtime_minutes": round(overtime, 1),
            "transit_minutes": int(employee_transit[emp]),
            "capacity_minutes": regular_capacity,
            "utilization_percent": round(
                min(100.0, regular_clean / regular_capacity * 100)
                if regular_capacity else 0.0,
                1,
            ),
            "idle_minutes": round(
                max(0.0, regular_capacity - regular_clean),
                1,
            ),
        }

    unscheduled = []  # В новой модели нормальные задачи не теряются.
    scheduled_keys = {(t.floor_index, t.room_id) for t in final_tasks}

    # allow_partial_schedule оставлен для обратной совместимости.
    result = {
        "tasks": final_tasks,
        "unscheduled_rooms": 0,
        "missed_cleanings": 0,
        "unscheduled_rooms_list": unscheduled,
        "feasible": True,
        "employee_analytics": employee_analytics,
        "overtime_tasks": len(overtime_tasks),
        "overtime_minutes": sum(
            j["duration"] for j, _, _, _ in overtime_tasks
        ),
        "regular_capacity_minutes": regular_capacity * emp_count,
        "total_cleaning_minutes": total_cleaning,
        "scheduled_room_keys": sorted(scheduled_keys),
    }
    return result


def _break_intervals(project, shift_start: int, shift_end: int):
    result = []
    for b_start, b_end in getattr(project, "breaks", []) or []:
        try:
            bs, be = _to_minutes(b_start), _to_minutes(b_end)
        except Exception:
            continue
        bs = max(bs, shift_start)
        be = min(be, shift_end)
        if be > bs:
            result.append((bs, be))
    return result


def plan_cleaning_schedule(project):
    return schedule_single_shift(project, project.employees_count)


def compute_recommended_employees(project):
    """Рекомендуемое число сотрудников (быстрая нижняя оценка)."""
    from cost_calculator import estimate_required_employees
    return estimate_required_employees(project)["employees"]
