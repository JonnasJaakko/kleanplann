# zone_manager.py
import math
from typing import List
from project import Room, Zone
import sanitarnorm

ZONE_COLORS = [
    (230,25,75,100), (60,180,75,100), (255,225,25,100),
    (0,130,200,100), (245,130,48,100), (145,30,180,100),
    (70,240,240,100), (240,50,230,100), (210,245,60,100),
    (250,190,190,100), (0,128,128,100), (230,190,255,100)
]

PRIORITY_BALANCED = "balanced"
PRIORITY_PROXIMITY = "proximity"
PRIORITY_AREA = "area"
PRIORITY_COUNT = "count"
PRIORITY_TIME = "time"

def _room_center(room: Room):
    """Правильно вычисляет геометрический центроид комнаты на основе пар координат [x, y]."""
    if not room.points: 
        return (0.0, 0.0)
        
    try:
        xs = []
        ys = []
        for p in room.points:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                xs.append(p[0])
                ys.append(p[1])
            elif isinstance(p, (int, float)):
                xs.append(p)
                
        if not xs:
            return (0.0, 0.0)
            
        if len(xs) == len(room.points) and all(isinstance(p, (int, float)) for p in room.points):
            flat_xs = room.points[0::2]
            flat_ys = room.points[1::2]
            if flat_xs and flat_ys:
                return (sum(flat_xs) / len(flat_xs), sum(flat_ys) / len(flat_ys))
                
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    except Exception:
        return (0.0, 0.0)

def _dist(c1, c2):
    if not c1 or not c2: return 0.0
    return math.hypot(c1[0] - c2[0], c1[1] - c2[1])

def _zone_centroid(centers):
    if not centers: return None
    xs = [c[0] for c in centers if c]
    ys = [c[1] for c in centers if c]
    if not xs: return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))

def _nearest_centroid(rooms, centers):
    return _zone_centroid([centers[r.id] for r in rooms]) if rooms else None


def _room_time_weight(room: Room, weather_factor: float = 1.0, cleaning_type: str = "поддерживающая") -> float:
    """Дневная нагрузка помещения в минутах с учётом текущих условий проекта."""
    freq = sanitarnorm.get_effective_frequency(room.room_type, weather_factor)
    duration = sanitarnorm.get_cleaning_time_minutes(
        room.room_type, room.area_m2, weather_factor, cleaning_type
    )
    # Небольшой резерв на переход к следующей задаче; он нужен именно
    # распределителю, чтобы не создавать зоны, которые математически
    # равны по уборке, но сильно различаются по количеству переходов.
    return max(1.0, (duration + 1.0) * freq)


def _proximity_distribution(rooms: List[Room], employees: int, weather_factor: float = 1.0, cleaning_type: str = "поддерживающая") -> List[List[Room]]:
    """Компактные зоны с жёстким ограничением по трудоёмкости.

    В старой версии «близость» выбирала пространственно удачные seeds, после чего
    некоторые зоны получали в 1.5–2 раза больше/меньше реальной работы. Здесь
    сначала строится трудовой баланс LPT, затем выполняются локальные обмены
    помещений, пока они улучшают компактность и сохраняют допустимый диапазон
    нагрузки.
    """
    if not rooms:
        return [[] for _ in range(employees)]
    employees = max(1, min(int(employees), len(rooms)))
    centers = {r.id: _room_center(r) for r in rooms}
    weights = {r.id: _room_time_weight(r, weather_factor, cleaning_type) for r in rooms}
    extent = max(
        1.0,
        math.hypot(
            max(c[0] for c in centers.values()) - min(c[0] for c in centers.values()),
            max(c[1] for c in centers.values()) - min(c[1] for c in centers.values()),
        ),
    )
    target = sum(weights.values()) / employees

    # 1) Сначала гарантируем трудовой баланс.
    zones = [[] for _ in range(employees)]
    loads = [0.0] * employees
    for room in sorted(rooms, key=lambda r: weights[r.id], reverse=True):
        i = min(range(employees), key=lambda idx: (loads[idx], len(zones[idx]), idx))
        zones[i].append(room)
        loads[i] += weights[room.id]

    def centroid(zone):
        if not zone:
            return (0.0, 0.0)
        xs = [centers[r.id][0] for r in zone]
        ys = [centers[r.id][1] for r in zone]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def compactness(zone):
        if len(zone) <= 1:
            return 0.0
        c = centroid(zone)
        return sum(_dist(c, centers[r.id]) for r in zone) / extent

    def objective(current_zones):
        compact = sum(compactness(z) for z in current_zones)
        count_penalty = sum(abs(len(z) - len(rooms) / employees) for z in current_zones) * 0.015
        return compact + count_penalty

    # 2) Улучшаем географическую компактность обменами, не разрушая баланс.
    lower = target * 0.82
    upper = target * 1.18
    current_obj = objective(zones)
    for _ in range(8):
        best = None
        for a in range(employees):
            if not zones[a]:
                continue
            for b in range(a + 1, employees):
                if not zones[b]:
                    continue
                for ra in zones[a]:
                    for rb in zones[b]:
                        new_a = loads[a] - weights[ra.id] + weights[rb.id]
                        new_b = loads[b] - weights[rb.id] + weights[ra.id]
                        if new_a < lower or new_a > upper or new_b < lower or new_b > upper:
                            continue
                        zones[a].remove(ra); zones[a].append(rb)
                        zones[b].remove(rb); zones[b].append(ra)
                        trial = objective(zones)
                        zones[a].remove(rb); zones[a].append(ra)
                        zones[b].remove(ra); zones[b].append(rb)
                        if trial + 1e-9 < current_obj and (best is None or trial < best[0]):
                            best = (trial, a, b, ra, rb, new_a, new_b)
        if best is None:
            break
        _, a, b, ra, rb, new_a, new_b = best
        zones[a].remove(ra); zones[a].append(rb)
        zones[b].remove(rb); zones[b].append(ra)
        loads[a], loads[b] = new_a, new_b
        current_obj = best[0]

    # 3) Ещё один балансирующий проход: если swap улучшает загрузку и не делает
    # географию заметно хуже, предпочтение получает более ровная нагрузка.
    for _ in range(3):
        changed = False
        heavy = sorted(range(employees), key=lambda i: loads[i], reverse=True)
        light = sorted(range(employees), key=lambda i: loads[i])
        for a in heavy:
            if loads[a] <= target * 1.10:
                continue
            for b in light:
                if a == b or loads[b] >= target * 0.92:
                    continue
                candidate = min(zones[a], key=lambda r: weights[r.id])
                new_a = loads[a] - weights[candidate.id]
                new_b = loads[b] + weights[candidate.id]
                if new_a < lower or new_b > upper:
                    continue
                before = objective(zones)
                zones[a].remove(candidate); zones[b].append(candidate)
                after = objective(zones)
                # Разрешаем небольшой рост пути, если он существенно выравнивает load.
                if after <= before + 0.08:
                    loads[a], loads[b] = new_a, new_b
                    changed = True
                    break
                zones[b].remove(candidate); zones[a].append(candidate)
            if changed:
                break
        if not changed:
            break
    return zones


def _balanced_distribution(rooms: List[Room], employees: int, mode: str, weather_factor: float = 1.0, cleaning_type: str = "поддерживающая") -> List[List[Room]]:
    if not rooms:
        return [[] for _ in range(employees)]
    weights = {r.id: _room_time_weight(r, weather_factor, cleaning_type) for r in rooms}
    areas = [0.0] * employees
    loads = [0.0] * employees
    zones = [[] for _ in range(employees)]

    if mode == PRIORITY_COUNT:
        ordered = sorted(rooms, key=lambda r: r.id)
        for idx, room in enumerate(ordered):
            i = idx % employees
            zones[i].append(room); loads[i] += weights[room.id]; areas[i] += room.area_m2
        return zones

    if mode == PRIORITY_AREA:
        ordered = sorted(rooms, key=lambda r: r.area_m2, reverse=True)
        for room in ordered:
            i = min(range(employees), key=lambda idx: (areas[idx], loads[idx], idx))
            zones[i].append(room); loads[i] += weights[room.id]; areas[i] += room.area_m2
        return zones

    # Balanced: longest-processing-time first on actual daily labour minutes.
    # Это гарантирует, что при разумном количестве комнат не останется зоны с 1 комнатой
    # только потому, что другая зона оказалась геометрически удачнее.
    ordered = sorted(rooms, key=lambda r: weights[r.id], reverse=True)
    for room in ordered:
        i = min(range(employees), key=lambda idx: (loads[idx], areas[idx], len(zones[idx]), idx))
        zones[i].append(room)
        loads[i] += weights[room.id]
        areas[i] += room.area_m2
    return zones


def manual_distribution(rooms: List[Room], percentages: List[float],
                        priority: str = PRIORITY_BALANCED, weather_factor: float = 1.0,
                        cleaning_type: str = "поддерживающая") -> List[Zone]:
    """Распределяет помещения между сотрудниками по выбранному режиму.

    Функция не допускает экстремальных зон: при наличии достаточного количества
    помещений нагрузка сначала балансируется по реальному времени уборки, затем
    учитываются площадь/количество/близость.
    """
    if not rooms or not percentages:
        return []
    employees = max(1, len(percentages))
    priority = (priority or PRIORITY_BALANCED).lower().strip()
    if priority == PRIORITY_PROXIMITY:
        zones = _proximity_distribution(rooms, employees, weather_factor, cleaning_type)
    elif priority in (PRIORITY_AREA, PRIORITY_COUNT):
        zones = _balanced_distribution(rooms, employees, priority, weather_factor, cleaning_type)
    else:
        zones = _balanced_distribution(rooms, employees, PRIORITY_BALANCED, weather_factor, cleaning_type)

    result = []
    for emp_idx, emp_rooms in enumerate(zones):
        if not emp_rooms:
            continue
        color = ZONE_COLORS[emp_idx % len(ZONE_COLORS)]
        result.append(Zone(
            emp_idx,
            f"Сотрудник {emp_idx + 1}",
            [r.id for r in emp_rooms],
            color=color,
            employee_index=emp_idx,
        ))
    return result


def distribute_project_zones(project, employees: int, priority: str = None) -> List[Zone]:
    """Строит зоны по всему проекту с учётом фактических нормативов проекта.

    Функция является единым API для GUI и staffing-модели: одинаковый проект
    должен получать одинаковое начальное разбиение зон.
    """
    employees = max(1, int(employees))
    priority = (priority or getattr(project, "priority_mode", PRIORITY_BALANCED) or PRIORITY_BALANCED).lower()
    weather_factor = float(getattr(project, "weather_factor", 1.0) or 1.0)
    cleaning_type = getattr(project, "cleaning_type", "поддерживающая")
    zones = []
    zone_id = 0
    shares = [100.0 / employees] * employees
    for floor_index, floor in enumerate(getattr(project, "floors", []) or []):
        rooms = [r for r in floor.rooms if not getattr(r, "disabled", False)]
        if not rooms:
            continue
        local = manual_distribution(
            rooms, shares, priority=priority,
            weather_factor=weather_factor, cleaning_type=cleaning_type,
        )
        for zone in local:
            zone.id = zone_id
            zone_id += 1
            zone.floor_index = floor_index
            zone.name = f"Сотрудник {zone.employee_index + 1} — этаж {floor_index + 1}"
            zones.append(zone)
    return zones
