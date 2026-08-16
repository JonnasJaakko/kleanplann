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

def manual_distribution(rooms: List[Room], percentages: List[float],
                        priority: str = PRIORITY_BALANCED) -> List[Zone]:
    """
    Интеллектуальное зонирование с жесткой поддержкой 4-х режимов приоритетов из GUI.
    """
    if not rooms or not percentages: return []
    employees = len(percentages)
    
    priority = (priority or "").lower().strip()
    
    room_time_weights = {}
    for r in rooms:
        freq = sanitarnorm.get_frequency_per_day(r.room_type)
        time_per_clean = sanitarnorm.get_cleaning_time_minutes(r.room_type, r.area_m2)
        room_time_weights[r.id] = (time_per_clean + 2.5) * freq

    total_time_load = sum(room_time_weights.values())
    total_area = sum(r.area_m2 for r in rooms)
    
    if total_time_load <= 0: return []

    target_time_load = total_time_load / employees
    target_area = total_area / employees
    target_count = len(rooms) / employees
    
    centers = {r.id: _room_center(r) for r in rooms}
    xs = [c[0] for c in centers.values() if c]
    ys = [c[1] for c in centers.values() if c]
    _extent_scale = max(1.0, (max(xs) - min(xs)) + (max(ys) - min(ys))) if xs else 1.0

    if priority == PRIORITY_AREA:
        pool = sorted(rooms, key=lambda r: r.area_m2, reverse=True)
    elif priority == PRIORITY_COUNT:
        pool = sorted(rooms, key=lambda r: r.id)
    else:
        pool = sorted(rooms, key=lambda r: room_time_weights[r.id], reverse=True)
        
    zones: List[List[Room]] = [[] for _ in range(employees)]
    emp_toilet_counts = {i: 0 for i in range(employees)}
    
    for i in range(employees):
        if i < len(pool):
            zones[i].append(pool[i])
            if "санузел" in (pool[i].room_type or "").lower():
                emp_toilet_counts[i] += 1
                
    remaining = pool[employees:] if len(pool) > employees else []

    while remaining:
        best_emp, best_short = 0, -1e18
        for i in range(employees):
            cur_time = sum(room_time_weights[r.id] for r in zones[i])
            cur_area = sum(r.area_m2 for r in zones[i])
            cur_count = len(zones[i])
            
            if priority == PRIORITY_AREA:
                short = (target_area - cur_area) / target_area
            elif priority == PRIORITY_COUNT:
                short = (target_count - cur_count) / max(1, target_count)
            elif priority == PRIORITY_PROXIMITY:
                short = (target_count - cur_count) / max(1, target_count)
            else:  # BALANCED
                short = ((target_time_load - cur_time) / target_time_load
                         + (target_count - cur_count) / max(1, target_count)
                         + (target_area - cur_area) / target_area)
                         
            if short > best_short:
                best_short, best_emp = short, i

        cur_time = sum(room_time_weights[r.id] for r in zones[best_emp])
        cur_area = sum(r.area_m2 for r in zones[best_emp])
        centroid = _zone_centroid([centers[r.id] for r in zones[best_emp]])

        chosen_room = None
        valid_rooms = []
        for r in remaining:
            is_toilet = "санузел" in (r.room_type or "").lower()
            if is_toilet and emp_toilet_counts[best_emp] >= 2:
                if any("санузел" not in (rm.room_type or "").lower() for rm in remaining):
                    continue
            valid_rooms.append(r)
            
        if not valid_rooms: valid_rooms = remaining

        if priority == PRIORITY_PROXIMITY:
            chosen_room = min(valid_rooms, key=lambda r: _dist(centroid, centers[r.id]) if centroid else 0.0)
        elif priority == PRIORITY_AREA:
            chosen_room = max(valid_rooms, key=lambda r: r.area_m2)
        elif priority == PRIORITY_COUNT:
            chosen_room = valid_rooms[0]
        else:  # BALANCED
            def _score(r):
                t_score = abs((cur_time + room_time_weights[r.id]) - target_time_load) / max(1.0, target_time_load)
                a_score = abs((cur_area + r.area_m2) - target_area) / max(1.0, target_area)
                c_score = abs((len(zones[best_emp]) + 1) - target_count) / max(1.0, target_count)
                d_score = (_dist(centroid, centers[r.id]) if centroid else 0.0) / max(1.0, _extent_scale)
                return t_score * 0.3 + a_score * 0.3 + c_score * 0.2 + d_score * 0.2
            chosen_room = min(valid_rooms, key=_score)

        if "санузел" in (chosen_room.room_type or "").lower():
            emp_toilet_counts[best_emp] += 1

        zones[best_emp].append(chosen_room)
        remaining.remove(chosen_room)

    result: List[Zone] = []
    for emp_idx, emp_rooms in enumerate(zones):
        if not emp_rooms: continue
        color = ZONE_COLORS[emp_idx % len(ZONE_COLORS)]
        result.append(Zone(emp_idx, f"Сотрудник {emp_idx+1}",
                           [r.id for r in emp_rooms], color=color,
                           employee_index=emp_idx))
    return result
