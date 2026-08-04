# zone_manager.py
from typing import List
from project import Room, Zone

ZONE_COLORS = [
    (230,25,75,100), (60,180,75,100), (255,225,25,100),
    (0,130,200,100), (245,130,48,100), (145,30,180,100),
    (70,240,240,100), (240,50,230,100), (210,245,60,100),
    (250,190,190,100), (0,128,128,100), (230,190,255,100)
]

def manual_distribution(rooms: List[Room], percentages: List[float]) -> List[Zone]:
    if not rooms or not percentages: return []
    total_area = sum(r.area_m2 for r in rooms)
    if total_area == 0: return []
    sorted_rooms = sorted(rooms, key=lambda r: r.area_m2, reverse=True)
    zones = []; room_pool = sorted_rooms.copy()
    for i, perc in enumerate(percentages):
        target_area = (perc / 100.0) * total_area
        zone_room_ids = []; cum_area = 0.0
        for room in list(room_pool):
            if cum_area + room.area_m2 <= target_area + 1e-6:
                zone_room_ids.append(room.id); cum_area += room.area_m2; room_pool.remove(room)
        while cum_area < target_area and room_pool:
            room = room_pool.pop(0); zone_room_ids.append(room.id); cum_area += room.area_m2
        if zone_room_ids:
            zones.append(Zone(i, f"Сотрудник {i+1}", zone_room_ids,
                              color=ZONE_COLORS[i % len(ZONE_COLORS)], employee_index=i))
    if room_pool and zones:
        for room in room_pool: zones[-1].room_ids.append(room.id)
    return zones