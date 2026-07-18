# """
# Построение замкнутых областей (комнат) из набора отрезков стен с использованием
# алгоритма обхода граней (face tracing) планарного графа.
# """
# from typing import List, Tuple
# from collections import defaultdict
# import math

# def _polygon_area(points):
#     n = len(points)
#     area = 0.0
#     for i in range(n):
#         x1, y1 = points[i]
#         x2, y2 = points[(i + 1) % n]
#         area += x1 * y2 - x2 * y1
#     return abs(area) / 2.0

# def _angle(p1, p2):
#     """Угол вектора p2-p1 в радианах."""
#     return math.atan2(p2[1] - p1[1], p2[0] - p1[0])

# def build_rooms_from_walls(walls: List[Tuple[float, float, float, float]]) -> List[List[Tuple[float, float]]]:
#     """
#     Находит все грани планарного графа, образованного стенами.
#     Каждая грань — замкнутый полигон. Внешняя грань (наибольшая площадь) отбрасывается.
#     """
#     if not walls:
#         return []

#     # 1. Строим список уникальных точек и рёбер (полу-рёбер)
#     points = []
#     point_to_idx = {}

#     def get_idx(pt):
#         key = (round(pt[0], 6), round(pt[1], 6))
#         if key not in point_to_idx:
#             point_to_idx[key] = len(points)
#             points.append(pt)
#         return point_to_idx[key]

#     half_edges = []          # (start_idx, end_idx, angle)
#     edge_map = {}            # (u,v) -> индекс half_edges

#     for (x1, y1, x2, y2) in walls:
#         u = get_idx((x1, y1))
#         v = get_idx((x2, y2))
#         if u == v:
#             continue
#         # прямое полу-ребро
#         angle_uv = _angle(points[u], points[v])
#         half_edges.append((u, v, angle_uv))
#         idx_uv = len(half_edges) - 1
#         # обратное
#         angle_vu = _angle(points[v], points[u])
#         half_edges.append((v, u, angle_vu))
#         idx_vu = len(half_edges) - 1
#         edge_map[(u, v)] = idx_uv
#         edge_map[(v, u)] = idx_vu

#     if not half_edges:
#         return []

#     # Для каждой вершины сортируем исходящие полу-рёбра по углу
#     adj = {i: [] for i in range(len(points))}
#     for idx, (u, v, angle) in enumerate(half_edges):
#         adj[u].append((angle, idx, v))

#     for u in adj:
#         adj[u].sort(key=lambda x: x[0])

#     # 2. Поиск граней
#     visited = [False] * len(half_edges)
#     faces = []

#     for start_he in range(len(half_edges)):
#         if visited[start_he]:
#             continue
#         u, v, _ = half_edges[start_he]
#         face_pts = [points[u]]
#         cur_he = start_he
#         while True:
#             visited[cur_he] = True
#             u, v, _ = half_edges[cur_he]
#             face_pts.append(points[v])
#             # обратное ребро
#             rev_he = edge_map.get((v, u))
#             if rev_he is None:
#                 break
#             # Находим позицию rev_he в списке adj[v]
#             angles_v = adj[v]
#             pos = next(i for i, (_, idx, _) in enumerate(angles_v) if idx == rev_he)
#             # Следующее ребро — предыдущее в циклическом порядке (левее)
#             next_pos = (pos - 1) % len(angles_v)
#             _, next_he, _ = angles_v[next_pos]
#             if next_he == start_he:
#                 break
#             cur_he = next_he

#         if len(face_pts) >= 3 and face_pts[0] == face_pts[-1]:
#             face_pts = face_pts[:-1]
#         if len(face_pts) >= 3:
#             faces.append(face_pts)

#     if not faces:
#         return []

#     # 3. Отбрасываем внешнюю грань (с максимальной площадью)
#     areas = [_polygon_area(f) for f in faces]
#     max_area = max(areas)
#     # Оставляем все грани, кроме внешней
#     rooms = [f for f, a in zip(faces, areas) if a < max_area * 0.999]
#     if not rooms:
#         # если всего одна грань, возвращаем её
#         rooms = [faces[0]]

#     return rooms


# def nearest_point_on_segment(point, seg_start, seg_end):
#     """Ближайшая точка на отрезке к заданной точке."""
#     x0, y0 = point
#     x1, y1 = seg_start
#     x2, y2 = seg_end
#     dx, dy = x2 - x1, y2 - y1
#     if dx == 0 and dy == 0:
#         return seg_start
#     t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
#     t = max(0.0, min(1.0, t))
#     return (x1 + t * dx, y1 + t * dy)


# def split_walls_at_intersections(walls: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
#     """Разбивает отрезки стен в точках пересечения с другими отрезками."""
#     if not walls:
#         return []
#     segments = [((x1, y1), (x2, y2)) for (x1, y1, x2, y2) in walls]
#     intersections = {}
#     n = len(segments)
#     for i in range(n):
#         p1, p2 = segments[i]
#         for j in range(i + 1, n):
#             p3, p4 = segments[j]
#             x1, y1 = p1
#             x2, y2 = p2
#             x3, y3 = p3
#             x4, y4 = p4
#             denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
#             if abs(denom) < 1e-9:
#                 continue
#             t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
#             u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
#             if 0 < t < 1 and 0 < u < 1:
#                 x = x1 + t * (x2 - x1)
#                 y = y1 + t * (y2 - y1)
#                 pt = (x, y)
#                 intersections.setdefault(i, []).append((t, pt))
#                 intersections.setdefault(j, []).append((u, pt))

#     new_walls = []
#     for i, seg in enumerate(segments):
#         (x1, y1), (x2, y2) = seg
#         pts_on = [(0.0, (x1, y1)), (1.0, (x2, y2))]
#         if i in intersections:
#             pts_on.extend(intersections[i])
#         pts_on.sort(key=lambda x: x[0])
#         # удаляем дубликаты
#         unique = []
#         for t, pt in pts_on:
#             if not unique or (math.hypot(pt[0] - unique[-1][1][0], pt[1] - unique[-1][1][1]) > 1e-6):
#                 unique.append((t, pt))
#         for k in range(len(unique) - 1):
#             pt1 = unique[k][1]
#             pt2 = unique[k + 1][1]
#             if math.hypot(pt1[0] - pt2[0], pt1[1] - pt2[1]) > 1e-6:
#                 new_walls.append((pt1[0], pt1[1], pt2[0], pt2[1]))
#     return new_walls

"""
Построение замкнутых областей (комнат) из набора отрезков стен.
Использует Shapely: union, snap, polygonize.
"""
from typing import List, Tuple
import math
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union, polygonize, snap

def _polygon_area(points):
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def build_rooms_from_walls(walls: List[Tuple[float, float, float, float]],
                           snap_tolerance: float = 0.05,
                           min_area: float = 0.5) -> List[List[Tuple[float, float]]]:
    """
    Принимает список стен (x1,y1,x2,y2) в метрах.
    Возвращает список полигонов комнат (списки точек).
    """
    if not walls:
        return []

    # Создаём LineString для каждого отрезка
    lines = [LineString([(x1, y1), (x2, y2)]) for x1, y1, x2, y2 in walls]

    # Объединяем все линии в одну геометрию
    merged = unary_union(lines)

    # Замыкаем разрывы: притягиваем вершины в пределах snap_tolerance
    snapped = snap(merged, merged, snap_tolerance)

    # polygonize ожидает итерацию LineString, поэтому извлекаем их
    if snapped.geom_type == 'LineString':
        line_collection = [snapped]
    elif snapped.geom_type == 'MultiLineString':
        line_collection = list(snapped.geoms)
    else:
        line_collection = []
        # Если после union получилась коллекция разных геометрий, достаём только линии
        from shapely.geometry import GeometryCollection
        if isinstance(snapped, GeometryCollection):
            for geom in snapped.geoms:
                if geom.geom_type == 'LineString':
                    line_collection.append(geom)
                elif geom.geom_type == 'MultiLineString':
                    line_collection.extend(geom.geoms)

    # Строим полигоны
    polygons = list(polygonize(line_collection))

    # Фильтруем по площади и валидности
    rooms = []
    for poly in polygons:
        if poly.is_valid and poly.area >= min_area:
            # Упрощаем контур, чтобы убрать мелкие артефакты
            poly = poly.simplify(0.01, preserve_topology=True)
            if poly.is_empty or poly.exterior is None:
                continue
            pts = list(poly.exterior.coords)
            if len(pts) < 3:
                continue
            # Удаляем последнюю точку, если она совпадает с первой
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            rooms.append(pts)

    # Удаляем внешний контур (самый большой), если он содержит все остальные
    if len(rooms) > 1:
        rooms.sort(key=lambda p: Polygon(p).area, reverse=True)
        outer = Polygon(rooms[0])
        if all(outer.contains(Polygon(p)) for p in rooms[1:]):
            rooms = rooms[1:]

    return rooms


def nearest_point_on_segment(point, seg_start, seg_end):
    """Ближайшая точка на отрезке к заданной точке."""
    x0, y0 = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return seg_start
    t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return (x1 + t * dx, y1 + t * dy)


def split_walls_at_intersections(walls):
    """Больше не требуется, оставлена для совместимости с вызовами в app.py"""
    return walls