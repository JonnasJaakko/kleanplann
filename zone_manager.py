"""
Распределение комнат по сотрудникам с использованием кластеризации (k-means)
на основе географических центров комнат и их площади.
"""
from typing import List, Tuple
import math
from project import Room, Zone

ZONE_COLORS = [
    (230,25,75,100), (60,180,75,100), (255,225,25,100),
    (0,130,200,100), (245,130,48,100), (145,30,180,100),
    (70,240,240,100), (240,50,230,100), (210,245,60,100),
    (250,190,190,100), (0,128,128,100), (230,190,255,100)
]

def _get_centers(rooms: List[Room]) -> List[Tuple[float, float]]:
    """Возвращает географические центры комнат."""
    centers = []
    for room in rooms:
        cx = sum(p[0] for p in room.points) / len(room.points)
        cy = sum(p[1] for p in room.points) / len(room.points)
        centers.append((cx, cy))
    return centers

def _kmeans_cluster(rooms: List[Room], k: int) -> List[List[Room]]:
    """
    Простейшая реализация k-means для группировки комнат по близости.
    Учитывает площадь: более крупные комнаты имеют больший вес при вычислении центроида.
    """
    if k <= 0 or not rooms:
        return []
    if k >= len(rooms):
        # Если сотрудников больше, чем комнат, каждому по комнате
        return [[room] for room in rooms] + [[] for _ in range(k - len(rooms))]

    centers = _get_centers(rooms)
    # Инициализируем центроиды: выбираем k случайных центров комнат
    import random
    random.seed(42)  # для воспроизводимости
    indices = list(range(len(rooms)))
    random.shuffle(indices)
    centroids = [centers[i] for i in indices[:k]]

    max_iterations = 100
    clusters = [[] for _ in range(k)]
    for _ in range(max_iterations):
        # Приписываем каждую комнату к ближайшему центроиду
        clusters = [[] for _ in range(k)]
        for i, room in enumerate(rooms):
            best_cluster = 0
            best_dist = float('inf')
            for j, centroid in enumerate(centroids):
                dist = math.hypot(centers[i][0] - centroid[0], centers[i][1] - centroid[1])
                # Учитываем площадь: более крупные комнаты "притягивают" центроид сильнее
                dist /= (room.area_m2 + 1.0)  # +1 чтобы не делить на ноль
                if dist < best_dist:
                    best_dist = dist
                    best_cluster = j
            clusters[best_cluster].append(room)

        # Пересчитываем центроиды как средневзвешенные центры
        new_centroids = []
        for cluster in clusters:
            if not cluster:
                # Если кластер пуст, оставляем старый центроид
                new_centroids.append(centroids[len(new_centroids)])
                continue
            total_weight = sum(r.area_m2 for r in cluster) + len(cluster)  # вес = площадь + 1
            cx = sum(r.area_m2 * (sum(p[0] for p in r.points) / len(r.points)) for r in cluster) / total_weight
            cy = sum(r.area_m2 * (sum(p[1] for p in r.points) / len(r.points)) for r in cluster) / total_weight
            new_centroids.append((cx, cy))

        # Проверяем сходимость
        max_shift = 0.0
        for j in range(k):
            if centroids[j] is None or new_centroids[j] is None:
                continue
            shift = math.hypot(new_centroids[j][0] - centroids[j][0],
                               new_centroids[j][1] - centroids[j][1])
            if shift > max_shift:
                max_shift = shift
        centroids = new_centroids
        if max_shift < 0.01:  # сантиметр
            break

    return clusters

def manual_distribution(rooms: List[Room], percentages: List[float]) -> List[Zone]:
    """
    Распределяет комнаты между сотрудниками.
    Если заданы проценты, используется прежний алгоритм равномерного распределения по площади.
    Если проценты равны (по умолчанию), применяется кластеризация для географической близости.
    """
    if not rooms or not percentages:
        return []

    # Проверяем, равны ли все проценты (с точностью до 1%)
    avg = percentages[0]
    if all(abs(p - avg) < 1.0 for p in percentages):
        # Кластеризация
        k = len(percentages)
        clusters = _kmeans_cluster(rooms, k)
        zones = []
        for i, cluster in enumerate(clusters):
            if cluster:
                zone = Zone(i, f"Сотрудник {i+1}", [r.id for r in cluster],
                            color=ZONE_COLORS[i % len(ZONE_COLORS)], employee_index=i)
                zones.append(zone)
            else:
                # Пустой кластер – создаём зону без комнат
                zones.append(Zone(i, f"Сотрудник {i+1}", [],
                                color=ZONE_COLORS[i % len(ZONE_COLORS)], employee_index=i))
        return zones
    else:
        # Старый алгоритм по процентам
        total_area = sum(r.area_m2 for r in rooms)
        if total_area == 0:
            return []
        sorted_rooms = sorted(rooms, key=lambda r: r.area_m2, reverse=True)
        zones = []
        room_pool = sorted_rooms.copy()
        for i, perc in enumerate(percentages):
            target_area = (perc / 100.0) * total_area
            zone_room_ids = []
            cum_area = 0.0
            for room in list(room_pool):
                if cum_area + room.area_m2 <= target_area + 1e-6:
                    zone_room_ids.append(room.id)
                    cum_area += room.area_m2
                    room_pool.remove(room)
            while cum_area < target_area and room_pool:
                room = room_pool.pop(0)
                zone_room_ids.append(room.id)
                cum_area += room.area_m2
            if zone_room_ids:
                zones.append(Zone(i, f"Сотрудник {i+1}", zone_room_ids,
                                  color=ZONE_COLORS[i % len(ZONE_COLORS)], employee_index=i))
        if room_pool and zones:
            for room in room_pool:
                zones[-1].room_ids.append(room.id)
        return zones