from typing import List
from project import Room, Zone

ZONE_COLORS = [
    (230,25,75,100), (60,180,75,100), (255,225,25,100),
    (0,130,200,100), (245,130,48,100), (145,30,180,100),
    (70,240,240,100), (240,50,230,100), (210,245,60,100),
    (250,190,190,100), (0,128,128,100), (230,190,255,100)
]

def _area_weight(room: Room) -> float:
    return room.area_m2

def _traffic_weight(room: Room) -> float:
    return room.traffic

def _complexity_weight(room: Room) -> float:
    # сложность обратно пропорциональна площади (меньше – сложнее)
    return 1.0 / (room.area_m2 + 1.0)

def _get_weight_func(mode: str):
    if mode == 'traffic':
        return _traffic_weight
    elif mode == 'complexity':
        return _complexity_weight
    else:  # area
        return _area_weight

def manual_distribution(rooms: List[Room], percentages: List[float],
                        priority_mode: str = 'area') -> List[Zone]:
    if not rooms or not percentages:
        return []

    weight_func = _get_weight_func(priority_mode)
    total_weight = sum(weight_func(r) for r in rooms)
    if total_weight == 0:
        return []

    # Сортируем комнаты по убыванию веса
    sorted_rooms = sorted(rooms, key=weight_func, reverse=True)
    zones = []
    room_pool = sorted_rooms.copy()

    for i, perc in enumerate(percentages):
        target_weight = (perc / 100.0) * total_weight
        zone_room_ids = []
        cum_weight = 0.0
        for room in list(room_pool):
            w = weight_func(room)
            if cum_weight + w <= target_weight + 1e-6:
                zone_room_ids.append(room.id)
                cum_weight += w
                room_pool.remove(room)
        # если не добрали до целевого веса – добавляем следующую комнату
        while cum_weight < target_weight and room_pool:
            room = room_pool.pop(0)
            zone_room_ids.append(room.id)
            cum_weight += weight_func(room)
        if zone_room_ids:
            zones.append(Zone(i, f"Сотрудник {i+1}", zone_room_ids,
                              color=ZONE_COLORS[i % len(ZONE_COLORS)],
                              employee_index=i))

    # оставшиеся комнаты добавляем в последнюю зону
    if room_pool and zones:
        for room in room_pool:
            zones[-1].room_ids.append(room.id)
    return zones