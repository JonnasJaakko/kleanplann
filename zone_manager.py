# zone_manager.py
"""
Распределение комнат между сотрудниками.

Цели:
  * сбалансированное распределение: у всех сотрудников примерно равные
    суммарная площадь и число комнат (никто не остаётся без работы,
    никто не перегружен);
  * комнаты одного сотрудника расположены рядом (если выбран приоритет
    «близость»);
  * поддержка приоритета: близость / площадь / количество / сбалансированно.
"""
import math
from typing import List
from project import Room, Zone

ZONE_COLORS = [
    (230,25,75,100), (60,180,75,100), (255,225,25,100),
    (0,130,200,100), (245,130,48,100), (145,30,180,100),
    (70,240,240,100), (240,50,230,100), (210,245,60,100),
    (250,190,190,100), (0,128,128,100), (230,190,255,100)
]

# Приоритеты распределения
PRIORITY_BALANCED = "balanced"      # сбалансированно (по умолчанию)
PRIORITY_PROXIMITY = "proximity"    # близость комнат
PRIORITY_AREA = "area"              # площадь
PRIORITY_COUNT = "count"            # количество комнат


def _room_center(room: Room):
    xs = [p[0] for p in room.points]
    ys = [p[1] for p in room.points]
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _dist(c1, c2):
    return math.hypot(c1[0] - c2[0], c1[1] - c2[1])


def _zone_centroid(centers):
    if not centers:
        return None
    return (sum(c[0] for c in centers) / len(centers),
            sum(c[1] for c in centers) / len(centers))


def manual_distribution(rooms: List[Room], percentages: List[float],
                        priority: str = PRIORITY_BALANCED) -> List[Zone]:
    """
    Сбалансированно распределяет комнаты между сотрудниками.

    percentages — проценты нагрузки (обычно равные 100/n).
    priority — приоритет: "balanced" | "proximity" | "area" | "count".

    Алгоритм:
      1. якорь: каждому сотруднику по одной самой большой комнате;
      2. донабор: на каждом шаге выбирается сотрудник с наибольшим
         «недобором» (по площади и количеству), ему отдаётся комната
         по выбранному приоритету;
      3. остаток распределяется так же.

    Гарантирует, что все сотрудники задействованы (если комнат не меньше
    числа сотрудников).
    """
    if not rooms or not percentages:
        return []
    employees = len(percentages)
    total_area = sum(r.area_m2 for r in rooms)
    if total_area <= 0:
        return []

    target_area = total_area / employees
    target_count = len(rooms) / employees
    centers = {r.id: _room_center(r) for r in rooms}
    # масштаб для нормализации расстояний
    xs = [c[0] for c in centers.values()]
    ys = [c[1] for c in centers.values()]
    _extent_scale = max(1.0, (max(xs) - min(xs)) + (max(ys) - min(ys)))

    # 1. якорь: по одной самой большой комнате каждому
    pool = sorted(rooms, key=lambda r: r.area_m2, reverse=True)
    zones: List[List[Room]] = [[] for _ in range(employees)]
    for i in range(employees):
        if i < len(pool):
            zones[i].append(pool[i])
    remaining = pool[employees:] if len(pool) > employees else []

    # 2-3. донабор и остаток
    while remaining:
        # выбираем сотрудника с наибольшим недобором
        best_emp, best_short = 0, -1e18
        for i in range(employees):
            cur_area = sum(r.area_m2 for r in zones[i])
            cur_count = len(zones[i])
            short = ((target_area - cur_area) / target_area
                     + (target_count - cur_count) / max(1, target_count))
            if short > best_short:
                best_short, best_emp = short, i

        cur_area = sum(r.area_m2 for r in zones[best_emp])
        centroid = _zone_centroid([centers[r.id] for r in zones[best_emp]])

        # выбираем комнату по приоритету
        if priority == PRIORITY_PROXIMITY:
            room = min(remaining, key=lambda r: _dist(centroid, centers[r.id]) if centroid else 0.0)
        elif priority == PRIORITY_AREA:
            room = max(remaining, key=lambda r: r.area_m2)
        elif priority == PRIORITY_COUNT:
            room = min(remaining, key=lambda r: _dist(centroid, centers[r.id]) if centroid else 0.0)
        else:  # balanced — совмещает площадь, количество и близость
            def _score(r):
                area_score = abs((cur_area + r.area_m2) - target_area) / max(1.0, target_area)
                count_score = abs((len(zones[best_emp]) + 1) - target_count) / max(1.0, target_count)
                dist_score = (_dist(centroid, centers[r.id]) if centroid else 0.0) / max(1.0, _extent_scale)
                return area_score * 0.5 + count_score * 0.3 + dist_score * 0.2
            room = min(remaining, key=_score)

        zones[best_emp].append(room)
        remaining.remove(room)

    # формируем зоны
    result: List[Zone] = []
    for emp_idx, emp_rooms in enumerate(zones):
        if not emp_rooms:
            continue
        color = ZONE_COLORS[emp_idx % len(ZONE_COLORS)]
        result.append(Zone(emp_idx, f"Сотрудник {emp_idx+1}",
                           [r.id for r in emp_rooms], color=color,
                           employee_index=emp_idx))
    return result