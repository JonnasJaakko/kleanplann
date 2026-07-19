"""
Построение комнат из стен: алгоритм обхода граней (face tracing) + разбиение в пересечениях.
"""
from typing import List, Tuple
from collections import defaultdict
import math

def _polygon_area(points):
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i+1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def _angle(p1, p2):
    return math.atan2(p2[1] - p1[1], p2[0] - p1[0])

def build_rooms_from_walls(walls: List[Tuple[float, float, float, float]]) -> List[List[Tuple[float, float]]]:
    if not walls:
        return []

    # 1. Строим граф (вершины, полу-рёбра)
    points = []
    point_to_idx = {}
    def get_idx(pt):
        key = (round(pt[0], 6), round(pt[1], 6))
        if key not in point_to_idx:
            point_to_idx[key] = len(points)
            points.append(pt)
        return point_to_idx[key]

    half_edges = []  # (start, end, angle)
    edge_map = {}
    for x1, y1, x2, y2 in walls:
        u = get_idx((x1, y1))
        v = get_idx((x2, y2))
        if u == v:
            continue
        ang_uv = _angle(points[u], points[v])
        half_edges.append((u, v, ang_uv))
        idx_uv = len(half_edges) - 1
        ang_vu = _angle(points[v], points[u])
        half_edges.append((v, u, ang_vu))
        idx_vu = len(half_edges) - 1
        edge_map[(u, v)] = idx_uv
        edge_map[(v, u)] = idx_vu

    if not half_edges:
        return []

    adj = defaultdict(list)
    for idx, (u, v, ang) in enumerate(half_edges):
        adj[u].append((ang, idx, v))

    for u in adj:
        adj[u].sort(key=lambda x: x[0])

    # 2. Поиск граней
    visited = [False] * len(half_edges)
    faces = []
    for start_he in range(len(half_edges)):
        if visited[start_he]:
            continue
        u, v, _ = half_edges[start_he]
        face_pts = [points[u]]
        cur_he = start_he
        while True:
            visited[cur_he] = True
            u, v, _ = half_edges[cur_he]
            face_pts.append(points[v])
            rev_he = edge_map.get((v, u))
            if rev_he is None:
                break
            pos = next(i for i, (_, idx, _) in enumerate(adj[v]) if idx == rev_he)
            next_pos = (pos - 1) % len(adj[v])
            _, next_he, _ = adj[v][next_pos]
            if next_he == start_he:
                break
            cur_he = next_he

        if len(face_pts) >= 3 and face_pts[0] == face_pts[-1]:
            face_pts = face_pts[:-1]
        if len(face_pts) >= 3:
            faces.append(face_pts)

    if not faces:
        return []

    # 3. Удаляем внешнюю грань (самую большую)
    areas = [_polygon_area(f) for f in faces]
    max_area = max(areas)
    rooms = [f for f, a in zip(faces, areas) if a < max_area * 0.999]
    return rooms if rooms else [faces[0]]


def nearest_point_on_segment(point, seg_start, seg_end):
    x0, y0 = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return seg_start
    t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return (x1 + t * dx, y1 + t * dy)


def split_walls_at_intersections(walls: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
    if not walls:
        return []
    segments = [((x1, y1), (x2, y2)) for (x1, y1, x2, y2) in walls]
    intersections = {}
    n = len(segments)
    for i in range(n):
        p1, p2 = segments[i]
        for j in range(i+1, n):
            p3, p4 = segments[j]
            x1, y1 = p1; x2, y2 = p2; x3, y3 = p3; x4, y4 = p4
            denom = (x1 - x2)*(y3 - y4) - (y1 - y2)*(x3 - x4)
            if abs(denom) < 1e-9:
                continue
            t = ((x1 - x3)*(y3 - y4) - (y1 - y3)*(x3 - x4)) / denom
            u = -((x1 - x2)*(y1 - y3) - (y1 - y2)*(x1 - x3)) / denom
            if 0 < t < 1 and 0 < u < 1:
                x = x1 + t*(x2 - x1)
                y = y1 + t*(y2 - y1)
                pt = (x, y)
                intersections.setdefault(i, []).append((t, pt))
                intersections.setdefault(j, []).append((u, pt))

    new_walls = []
    for i, seg in enumerate(segments):
        (x1, y1), (x2, y2) = seg
        pts_on = [(0.0, (x1, y1)), (1.0, (x2, y2))]
        if i in intersections:
            pts_on.extend(intersections[i])
        pts_on.sort(key=lambda x: x[0])
        unique = []
        for t, pt in pts_on:
            if not unique or (math.hypot(pt[0]-unique[-1][1][0], pt[1]-unique[-1][1][1]) > 1e-6):
                unique.append((t, pt))
        for k in range(len(unique)-1):
            pt1 = unique[k][1]
            pt2 = unique[k+1][1]
            if math.hypot(pt1[0]-pt2[0], pt1[1]-pt2[1]) > 1e-6:
                new_walls.append((pt1[0], pt1[1], pt2[0], pt2[1]))
    return new_walls