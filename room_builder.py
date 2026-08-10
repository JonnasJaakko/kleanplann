"""
room_builder — построение комнат (замкнутых областей) из отрезков стен.

Два режима:

  * "thin"  — стена задана ОДНОЙ осевой линией (ручной редактор в app.py).
              Комната = грань планарного графа (shapely.polygonize).

  * "solid" — стена задана ДВУМЯ параллельными линиями (типовой DXF из AutoCAD).
              Комната = свободное пространство ВНУТРИ здания, то есть негатив
              массы стен. Именно этот режим лечит баг «контур вокруг каждой
              стены»: polygonize по сырым линиям DXF считает комнатой саму
              полость стены между её двумя гранями.

Главная точка входа — detect_rooms(). build_rooms_from_walls() сохранена
ради обратной совместимости со старыми вызовами.

Все параметры длины — в тех же единицах, что и координаты стен
(dxf_analyzer передаёт метры; редактор app.py — пиксели сцены).

Схематизация (режим "centerline"):
----------------------------------
Для интерактивного редактора нужен НЕ контур стены, а её ОСЬ.  extract_wall_centerlines()
находит пары параллельных отрезков на расстоянии толщины стены и заменяет их
одним отрезком — серединой между гранями.  Одиночные линии (ненесущие
перегородки, уже осевые) сохраняются как есть.  cleanup_segments() затем
выбрасывает мусор: дубликаты, короткие изолированные обрезки, висячие хвосты
от балок/размерных линий и склеивает коллинеарные отрезки.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

Wall = Tuple[float, float, float, float]
Pts = List[Tuple[float, float]]

# --- предохранители от переполнения памяти -------------------------------
MAX_SEGMENTS = 400_000        # больше — обрезаем ввод
MAX_BRIDGE_ENDS = 20_000      # больше — не пытаемся сшивать дверные проёмы
MAX_ROOM_POINTS = 400         # больше — упрощаем контур комнаты


# ======================= базовые утилиты =======================
def _as_lines(walls: Iterable[Wall]) -> List[LineString]:
    """Отрезки -> LineString, вырожденные выбрасываем."""
    lines = []
    for w in walls:
        try:
            x1, y1, x2, y2 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
        except (TypeError, ValueError, IndexError):
            continue
        if not all(map(math.isfinite, (x1, y1, x2, y2))):
            continue
        if x1 == x2 and y1 == y2:
            continue
        lines.append(LineString([(x1, y1), (x2, y2)]))
        if len(lines) >= MAX_SEGMENTS:
            logger.warning("Превышен лимит отрезков (%d), ввод обрезан", MAX_SEGMENTS)
            break
    return lines


def _extent(lines: Sequence[LineString]) -> float:
    """Диагональ габаритного прямоугольника — масштаб чертежа."""
    if not lines:
        return 0.0
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for ln in lines:
        a, b, c, d = ln.bounds
        minx = min(minx, a); miny = min(miny, b)
        maxx = max(maxx, c); maxy = max(maxy, d)
    return math.hypot(maxx - minx, maxy - miny)


def _dedupe(lines: Sequence[LineString], grid: float) -> List[LineString]:
    """Выбрасывает дубликаты отрезков (в DXF они встречаются пачками)."""
    if grid <= 0:
        return list(lines)
    seen = set()
    out = []
    for ln in lines:
        (x1, y1), (x2, y2) = ln.coords[0], ln.coords[-1]
        a = (round(x1 / grid), round(y1 / grid))
        b = (round(x2 / grid), round(y2 / grid))
        if a == b:
            continue
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def _flatten_lines(geom) -> List[LineString]:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out.extend(_flatten_lines(g))
        return out
    return []


def _node(lines: Sequence[LineString], grid: float) -> List[LineString]:
    """
    Разрезает отрезки во всех пересечениях и приводит координаты к сетке.

    Здесь же лечится главная причина падения по памяти: старый код звал
    snap(merged, merged, tol) — это квадратично по числу вершин и на реальном
    чертеже съедает всю RAM. set_precision делает snap-rounding за один
    проход внутри GEOS.
    """
    if not lines:
        return []
    merged = unary_union(lines)          # union линий = их нодирование
    if grid > 0:
        try:
            merged = shapely.set_precision(merged, grid_size=grid)
        except Exception as e:            # старый GEOS
            logger.debug("set_precision недоступен (%s), продолжаю без него", e)
    return _flatten_lines(merged)


def _polygon_area(points: Pts) -> float:
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _poly_to_pts(poly: Polygon, simplify_tol: float) -> Optional[Pts]:
    """Полигон -> список точек с упрощением и жёстким лимитом на вершины."""
    if poly is None or poly.is_empty:
        return None
    if poly.geom_type != "Polygon":
        parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
        if not parts:
            return None
        poly = max(parts, key=lambda p: p.area)

    tol = max(simplify_tol, 0.0)
    for _ in range(6):
        p = poly.simplify(tol, preserve_topology=True) if tol > 0 else poly
        if p.is_empty or p.geom_type != "Polygon":
            break
        pts = list(p.exterior.coords)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            break
        if len(pts) <= MAX_ROOM_POINTS:
            return [(float(x), float(y)) for x, y in pts]
        tol = tol * 2 if tol > 0 else 0.01

    pts = list(poly.exterior.coords)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return [(float(x), float(y)) for x, y in pts] if len(pts) >= 3 else None


# ======================= анализ толщины стен =======================
def estimate_wall_thickness(lines: Sequence[LineString],
                            max_thickness: Optional[float] = None,
                            sample: int = 4000) -> Tuple[float, float]:
    """
    Оценивает толщину стены по расстоянию между параллельными соседями.

    Возвращает (толщина, доля_отрезков_с_параллельной_парой).
    Доля > ~0.35 означает, что стены нарисованы двумя линиями -> режим "solid".
    """
    if not lines:
        return 0.0, 0.0
    ext = _extent(lines)
    if max_thickness is None:
        max_thickness = max(ext * 0.02, 1e-9)

    step = max(1, len(lines) // sample)
    probes = lines[::step]
    tree = STRtree(lines)

    dists: List[float] = []
    paired = 0
    for s in probes:
        (x1, y1), (x2, y2) = s.coords[0], s.coords[-1]
        if s.length < max_thickness:      # слишком короткий, чтобы судить
            continue
        ang_s = math.atan2(y2 - y1, x2 - x1)
        best = None
        for idx in tree.query(s.buffer(max_thickness)):
            c = lines[idx]
            if c is s or c.length < max_thickness:
                continue
            (a1, b1), (a2, b2) = c.coords[0], c.coords[-1]
            ang_c = math.atan2(b2 - b1, a2 - a1)
            d_ang = abs((ang_s - ang_c + math.pi / 2) % math.pi - math.pi / 2)
            if d_ang > math.radians(8):    # не параллельны
                continue
            d = s.distance(c)
            if 1e-9 < d <= max_thickness and (best is None or d < best):
                best = d
        if best is not None:
            paired += 1
            dists.append(best)

    if not dists:
        return 0.0, 0.0
    dists.sort()
    thickness = dists[len(dists) // 2]     # медиана устойчивее среднего
    frac = paired / max(1, len(probes))
    return thickness, frac


def _parallel_wall_faces(lines: Sequence[LineString], thickness: float) -> List[LineString]:
    """Keep the linework that is supported by a parallel wall face.

    A solid-wall DXF draws a wall using two nearby parallel contours.  Door
    leaves, furniture, dimensions and drafting remnants do not usually have
    that property.  Feeding all of them into the negative-space algorithm is
    what creates dozens of false "rooms".  Long single lines that join a
    supported wall are retained as a conservative fallback for incomplete DXF.
    """
    if not lines or thickness <= 0:
        return list(lines)

    min_length = max(thickness * 2.5, 0.45)
    search = max(thickness * 1.7, 0.08)
    tree = STRtree(lines)
    supported = set()

    for i, line in enumerate(lines):
        if line.length < min_length:
            continue
        (x1, y1), (x2, y2) = line.coords[0], line.coords[-1]
        angle = math.atan2(y2 - y1, x2 - x1)
        for j in tree.query(line.buffer(search)):
            if i == j:
                continue
            other = lines[j]
            if other.length < min_length:
                continue
            (a1, b1), (a2, b2) = other.coords[0], other.coords[-1]
            other_angle = math.atan2(b2 - b1, a2 - a1)
            delta = abs((angle - other_angle + math.pi / 2) % math.pi - math.pi / 2)
            if delta > math.radians(8):
                continue
            distance = line.distance(other)
            if not thickness * 0.20 <= distance <= search:
                continue
            ux, uy = math.cos(angle), math.sin(angle)
            p1, p2 = x1 * ux + y1 * uy, x2 * ux + y2 * uy
            q1, q2 = a1 * ux + b1 * uy, a2 * ux + b2 * uy
            overlap = min(max(p1, p2), max(q1, q2)) - max(min(p1, p2), min(q1, q2))
            if overlap >= min(line.length, other.length) * 0.20:
                supported.add(i)
                supported.add(j)

    if not supported:
        return list(lines)

    # Preserve long single centre-lines only where they physically continue a
    # validated wall.  Isolated symbols remain excluded.
    accepted = [lines[i] for i in sorted(supported)]
    accepted_tree = STRtree(accepted)
    for i, line in enumerate(lines):
        if i in supported or line.length < max(thickness * 8.0, 2.0):
            continue
        if len(accepted_tree.query(line.buffer(thickness * 1.5))) > 0:
            accepted.append(line)

    logger.info("Structural wall filter: %d -> %d segments", len(lines), len(accepted))
    return accepted


# ======================= сшивание дверных проёмов =======================
def _bridge_openings(lines: List[LineString], max_gap: float) -> List[LineString]:
    """
    Соединяет висящие концы стен, если они ближе max_gap друг к другу.

    Это закрывает дверные проёмы точечно, в отличие от глобального
    «раздули-сжали», которое заодно заливает узкие коридоры.
    """
    if max_gap <= 0 or not lines:
        return []

    deg: Dict[Tuple[int, int], int] = defaultdict(int)
    pos: Dict[Tuple[int, int], Tuple[float, float]] = {}
    grid = max_gap / 100.0
    for ln in lines:
        for p in (ln.coords[0], ln.coords[-1]):
            k = (round(p[0] / grid), round(p[1] / grid))
            deg[k] += 1
            pos[k] = (p[0], p[1])

    free = [pos[k] for k, d in deg.items() if d == 1]
    if len(free) < 2:
        return []
    if len(free) > MAX_BRIDGE_ENDS:
        logger.warning("Слишком много висящих концов (%d), пропускаю сшивание", len(free))
        return []

    pts = [Point(p) for p in free]
    ptree = STRtree(pts)
    wtree = STRtree(lines)

    bridges: List[LineString] = []
    used = set()
    for i, p in enumerate(pts):
        if i in used:
            continue
        best_j, best_d = None, None
        for j in ptree.query(p.buffer(max_gap)):
            if j == i or j in used:
                continue
            d = p.distance(pts[j])
            if 1e-12 < d <= max_gap and (best_d is None or d < best_d):
                best_j, best_d = j, d
        if best_j is None:
            continue
        seg = LineString([free[i], free[best_j]])
        # мост не должен пересекать существующие стены
        if any(seg.crosses(lines[k]) for k in wtree.query(seg)):
            continue
        used.add(i)
        used.add(best_j)
        bridges.append(seg)
    return bridges


# ======================= схематизация и чистка =======================
def _line_angle(line: LineString) -> float:
    (x1, y1) = line.coords[0]
    (x2, y2) = line.coords[-1]
    return math.atan2(y2 - y1, x2 - x1)


def _project_point_on_line(p: Tuple[float, float], line: LineString):
    """Ортогональная проекция точки p на отрезок line (с ограничением)."""
    (a1, b1) = line.coords[0]
    (a2, b2) = line.coords[-1]
    vx, vy = a2 - a1, b2 - b1
    ll = vx * vx + vy * vy
    if ll < 1e-12:
        return None
    t = ((p[0] - a1) * vx + (p[1] - b1) * vy) / ll
    t = max(0.0, min(1.0, t))
    return (a1 + vx * t, b1 + vy * t)


def _axis_overlap(line_a: LineString, line_b: LineString) -> Optional[LineString]:
    """
    Осевой отрезок для пары параллельных линий.

    Берётся общая часть проекций двух граней стены на общее направление,
    для каждого параметра t вычисляется середина между гранями.
    """
    (x1, y1) = line_a.coords[0]
    (x2, y2) = line_a.coords[-1]
    ux, uy = x2 - x1, y2 - y1
    length = math.hypot(ux, uy)
    if length < 1e-12:
        return None
    ux /= length
    uy /= length

    # проекции концов B на направление A
    (a1, b1) = line_b.coords[0]
    (a2, b2) = line_b.coords[-1]
    t_a = (a1 - x1) * ux + (b1 - y1) * uy
    t_b = (a2 - x1) * ux + (b2 - y1) * uy
    t1, t2 = sorted((t_a, t_b))

    t_start = max(0.0, t1)
    t_end = min(length, t2)
    if t_end - t_start < 1e-9:
        return None

    def mid(t: float):
        pa = (x1 + ux * t, y1 + uy * t)
        pb = _project_point_on_line(pa, line_b)
        if pb is None:
            return None
        return ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)

    m1 = mid(t_start)
    m2 = mid(t_end)
    if m1 is None or m2 is None:
        return None
    return LineString([m1, m2])


def extract_wall_centerlines(walls: Iterable[Wall],
                             wall_thickness: Optional[float] = None,
                             max_gap_ratio: float = 2.5,
                             min_pair_overlap: float = 0.35) -> List[Wall]:
    """
    Преобразует двухлинейные стены в осевые линии — «перенос чертежа в схему».

    Каждая пара параллельных отрезков на расстоянии ≈ толщине стены
    заменяется ОДНИМ отрезком — серединой между гранями.  Одиночные линии
    (уже осевые, ненесущие перегородки, «негабаритные») сохраняются как есть.

    Возвращает список стен в том же формате (x1, y1, x2, y2).
    """
    lines = _as_lines(walls)
    if not lines:
        return []

    if wall_thickness is None or wall_thickness <= 0:
        wall_thickness, _ = estimate_wall_thickness(lines)
    if wall_thickness is None or wall_thickness <= 0:
        # пар нет — ничего схематизировать, все линии уже одиночные
        return [(ln.coords[0][0], ln.coords[0][1],
                 ln.coords[-1][0], ln.coords[-1][1]) for ln in lines]

    min_len = max(wall_thickness * 2.5, 0.45)
    search = wall_thickness * max_gap_ratio
    tree = STRtree(lines)

    pair_idx: Dict[int, int] = {}
    centerlines: List[LineString] = []

    for i, line in enumerate(lines):
        if i in pair_idx or line.length < min_len:
            continue
        best_j, best_seg = None, None
        ang_i = _line_angle(line)
        for j in tree.query(line.buffer(search)):
            if i >= j or j in pair_idx:
                continue
            other = lines[j]
            if other.length < min_len:
                continue
            # параллельность (±8°)
            if abs((ang_i - _line_angle(other) + math.pi / 2) % math.pi - math.pi / 2) > math.radians(8):
                continue
            d = line.distance(other)
            if not wall_thickness * 0.35 <= d <= search:
                continue
            seg = _axis_overlap(line, other)
            if seg is None:
                continue
            if seg.length < min_len * min_pair_overlap:
                continue
            best_j, best_seg = j, seg
            break
        if best_j is not None:
            pair_idx[i] = best_j
            pair_idx[best_j] = i
            centerlines.append(best_seg)

    # одиночные линии, не вошедшие в пары, сохраняем как есть
    result = list(centerlines)
    for i, ln in enumerate(lines):
        if i not in pair_idx and ln.length >= min_len * 0.6:
            result.append(ln)

    logger.info("Centerlines: %d стен -> %d осей "
                "(осталось одиночных: %d)",
                len(lines), len(centerlines),
                sum(1 for i, ln in enumerate(lines)
                    if i not in pair_idx and ln.length >= min_len * 0.6))

    return [(ln.coords[0][0], ln.coords[0][1],
             ln.coords[-1][0], ln.coords[-1][1]) for ln in result]


def _snap_ends_to_network(lines: Sequence[LineString], tol: float,
                          iterations: int = 3) -> List[LineString]:
    """
    Притягивает концы отрезков к сети (углы/тавры сходятся в узлах).

    Итеративно: на каждом проходе каждый конец прижимается к ближайшей
    точке среди (а) концов других отрезков и (б) ортогональных проекций
    на другие отрезки, если расстояние ≤ tol.  Повтор до стабилизации
    (максимум iterations проходов) — после первого прижимания соседние
    концы становятся ближе и схватываются на следующей итерации.

    Висящие концы (дверные/оконные проёмы) остаются как есть — их зашивает
    _bridge_openings() на следующем шаге.
    """
    if tol <= 0 or len(lines) <= 1:
        return list(lines)

    work = list(lines)
    for _ in range(iterations):
        changed = False
        tree = STRtree(work)
        new_lines: List[LineString] = []

        for i, ln in enumerate(work):
            (sx, sy) = ln.coords[0]
            (ex, ey) = ln.coords[-1]

            for is_start, pt in ((True, (sx, sy)), (False, (ex, ey))):
                p = Point(pt)
                best_pt, best_d = None, tol
                for k in tree.query(p.buffer(tol)):
                    if k == i:
                        continue
                    other = work[k]
                    # (а) концы других отрезков — точка-точка
                    for op in (other.coords[0], other.coords[-1]):
                        d = p.distance(Point(op))
                        if d < best_d:
                            best_d = d
                            best_pt = op
                    # (б) ортогональная проекция на другой отрезок
                    proj = other.interpolate(other.project(p))
                    d = p.distance(proj)
                    if d < best_d:
                        best_d = d
                        best_pt = (proj.x, proj.y)
                if best_pt is not None:
                    if is_start:
                        if (sx, sy) != best_pt:
                            sx, sy = best_pt
                            changed = True
                    else:
                        if (ex, ey) != best_pt:
                            ex, ey = best_pt
                            changed = True

            if (sx, sy) != (ex, ey):
                new_lines.append(LineString([(sx, sy), (ex, ey)]))

        work = new_lines
        if not changed or not work:
            break

    return work


def snap_wall_ends(walls: Iterable[Wall], tol: Optional[float] = None) -> List[Wall]:
    """
    Публичная обёртка: прижимает концы отрезков к сети (углы/тавры сходятся).

    tol — радиус поиска соседней линии (по умолчанию ~2.5 толщины стены,
    определённой автоматически).  Висящие концы проёмов не трогаются.
    """
    lines = _as_lines(walls)
    if not lines:
        return []
    if tol is None or tol <= 0:
        thickness, _ = estimate_wall_thickness(lines)
        tol = max(thickness * 2.5, 0.3) if thickness > 0 else 0.3
    snapped = _snap_ends_to_network(lines, tol)
    return [(ln.coords[0][0], ln.coords[0][1],
             ln.coords[-1][0], ln.coords[-1][1]) for ln in snapped]


def _merge_collinear_lines(lines: Sequence[LineString],
                           angle_tol_deg: float = 3.0,
                           gap_tol: Optional[float] = None) -> List[LineString]:
    """
    Объединяет коллинеарные куски одной стены в один длинный отрезок.

    После схематизации длинная стена, нарисованная в DXF несколькими
    сегментами (с дверными проёмами, косяками, стыками), остаётся набором
    коротких осей.  Здесь они группируются по направлению и коллинеарности
    (транзитивно: кусок может соединять два дальнейших) и сливаются в один
    отрезок от min до max проекции на общую ось, если суммарный разрыв
    между кусками не превышает gap_tol (иначе это разные стены на одной
    линии — например, по разные стороны широкого дверного проёма).

    Возвращает список LineString.
    """
    if len(lines) <= 1:
        return list(lines)

    if gap_tol is None or gap_tol <= 0:
        ext = _extent(lines)
        gap_tol = max(ext * 0.002, 0.05)

    # --- транзитивная группировка коллинеарных кусков (BFS по графу соседства)
    tree = STRtree(lines)
    groups: List[List[int]] = []
    assigned: set = set()

    def _on_same_line(a: LineString, b: LineString) -> bool:
        """b лежит на той же бесконечной линии, что и a (в пределах gap_tol)."""
        (x1, y1) = a.coords[0]
        (x2, y2) = a.coords[-1]
        vx, vy = x2 - x1, y2 - y1
        ll = vx * vx + vy * vy
        if ll < 1e-12:
            return False
        # расстояние от концов b до бесконечной линии a
        for p in (b.coords[0], b.coords[-1]):
            t = ((p[0] - x1) * vx + (p[1] - y1) * vy) / ll
            px = x1 + vx * t
            py = y1 + vy * t
            if math.hypot(p[0] - px, p[1] - py) > gap_tol:
                return False
        return True

    for i in range(len(lines)):
        if i in assigned:
            continue
        queue = [i]
        group: List[int] = []
        while queue:
            k = queue.pop()
            if k in assigned:
                continue
            assigned.add(k)
            group.append(k)
            ln_k = lines[k]
            ang_k = _line_angle(ln_k)
            for j in tree.query(ln_k.buffer(gap_tol * 3.0)):
                if j in assigned or j == k:
                    continue
                other = lines[j]
                ang_j = _line_angle(other)
                # минимальная разница направлений без учёта ориентации:
                # параллельность 0°..180° -> угол 0, перпендикуляр -> 90°
                d_ang = abs((ang_k - ang_j + math.pi / 2) % math.pi - math.pi / 2)
                if d_ang > math.radians(angle_tol_deg):
                    continue
                if not _on_same_line(ln_k, other):
                    continue
                queue.append(j)
        groups.append(group)

    # --- слияние каждой группы в один отрезок (если разрыв невелик)
    out: List[LineString] = []
    for group in groups:
        if len(group) == 1:
            out.append(lines[group[0]])
            continue

        base = lines[group[0]]
        (x1, y1) = base.coords[0]
        (x2, y2) = base.coords[-1]
        ux, uy = x2 - x1, y2 - y1
        length = math.hypot(ux, uy)
        if length < 1e-12:
            out.extend(lines[idx] for idx in group)
            continue
        ux /= length
        uy /= length
        nx, ny = -uy, ux

        # интервалы [t_start, t_end] каждого куска на общей оси
        intervals: List[Tuple[float, float]] = []
        offsets: List[float] = []
        for idx in group:
            ln = lines[idx]
            p0 = ln.coords[0]
            p1 = ln.coords[-1]
            t0 = (p0[0] - x1) * ux + (p0[1] - y1) * uy
            t1 = (p1[0] - x1) * ux + (p1[1] - y1) * uy
            intervals.append((min(t0, t1), max(t0, t1)))
            mid = ln.interpolate(0.5, normalized=True)
            offsets.append((mid.x - x1) * nx + (mid.y - y1) * ny)

        # суммарный разрыв между интервалами > gap_tol*1.5 => разные стены
        # (микро-зазоры после snap сливаются, широкий дверной проём — нет)
        intervals.sort()
        merged_t: List[Tuple[float, float]] = []
        for a, b in intervals:
            if not merged_t or a > merged_t[-1][1] + gap_tol * 1.5:
                merged_t.append([a, b])
            else:
                merged_t[-1][1] = max(merged_t[-1][1], b)
        merged_t = [(a, b) for a, b in merged_t]

        if len(merged_t) > 1:
            out.extend(lines[idx] for idx in group)
            continue

        off = sum(offsets) / len(offsets)
        t_min = merged_t[0][0]
        t_max = merged_t[0][1]
        p1 = (x1 + ux * t_min + nx * off, y1 + uy * t_min + ny * off)
        p2 = (x1 + ux * t_max + nx * off, y1 + uy * t_max + ny * off)
        out.append(LineString([p1, p2]))

    return out


def merge_collinear_segments(walls: Iterable[Wall],
                             angle_tol_deg: float = 3.0,
                             gap_tol: Optional[float] = None) -> List[Wall]:
    """
    Публичная обёртка: объединяет коллинеарные куски одной стены в один
    отрезок.  Возвращает список стен в формате (x1, y1, x2, y2).
    """
    lines = _as_lines(walls)
    if not lines:
        return []
    merged = _merge_collinear_lines(lines, angle_tol_deg, gap_tol)
    return [(ln.coords[0][0], ln.coords[0][1],
             ln.coords[-1][0], ln.coords[-1][1]) for ln in merged]


def _drop_junk(lines: Sequence[LineString], min_length: float, tol: float) -> List[LineString]:
    """
    Удаляет короткий мусор: изолированные обрезки (круги нумерации, балки,
    обрывки размерных линий) и короткие висячие хвосты.  Короткий отрезок,
    у которого ОБА конца касаются других линий, — это косяк/перемычка/окно,
    он сохраняется (двери и окна должны учитываться).
    """
    if len(lines) <= 1:
        return [ln for ln in lines if ln.length >= min_length * 0.3]

    hard_min = max(min_length * 0.35, 0.08)
    tree = STRtree(lines)
    keep: List[LineString] = []
    radius = max(tol * 2.0, hard_min * 0.5)

    for i, ln in enumerate(lines):
        if ln.length < hard_min:
            continue
        if ln.length >= min_length:
            keep.append(ln)
            continue
        # короткий отрезок: держим только если он что-то соединяет
        n_start = 0
        n_end = 0
        for k in tree.query(ln.buffer(radius)):
            if k == i:
                continue
            d1 = Point(ln.coords[0]).distance(lines[k])
            d2 = Point(ln.coords[-1]).distance(lines[k])
            if d1 <= radius:
                n_start += 1
            if d2 <= radius:
                n_end += 1
        if n_start > 0 and n_end > 0:
            keep.append(ln)
    return keep


def cleanup_segments(walls: Iterable[Wall],
                     min_length: Optional[float] = None,
                     snap_tol: Optional[float] = None) -> List[Wall]:
    """
    Чистка схемы после схематизации:

      * выбрасывает дубликаты;
      * склеивает коллинеарные отрезки, соприкасающиеся концами;
      * **объединяет коллинеарные куски одной стены в один длинный отрезок**
        (длинная стена, нарисованная в DXF несколькими сегментами, становится
        одной стеной; куски по разные стороны широкого дверного проёма не
        склеиваются — это разные стены);
      * удаляет очень короткие обрезки;
      * удаляет изолированные и короткие висячие хвосты (балки, круги
        нумерации, размерные линии), сохраняя косяки/окна/перемычки.

    Возвращает список стен в формате (x1, y1, x2, y2).
    """
    lines = _as_lines(walls)
    if not lines:
        return []

    if min_length is None or min_length <= 0:
        ext = _extent(lines)
        min_length = max(ext * 0.005, 0.2)
    grid = snap_tol or max(min_length * 0.25, 1e-6)

    # 1. дубликаты
    lines = _dedupe(lines, grid)
    if not lines:
        return []

    # 2. склейка коллинеарных касающихся отрезков (unary_union сам сливает)
    try:
        merged = unary_union(lines)
    except Exception:
        merged = None
    if merged is None or merged.is_empty:
        return []
    try:
        merged = shapely.set_precision(merged, grid_size=grid)
    except Exception:
        pass
    lines = [ln for ln in _flatten_lines(merged) if ln.length >= 1e-9]
    if not lines:
        return []

    # 2.5. объединяем коллинеарные куски одной стены в один длинный отрезок.
    # Порог зазора — половина min_length: стены по разные стороны широкого
    # дверного проёма остаются раздельными.
    lines = _merge_collinear_lines(lines, gap_tol=max(min_length * 0.5, grid * 2.0))

    # 3. выбрасываем мусор
    lines = _drop_junk(lines, min_length, grid)

    return [(ln.coords[0][0], ln.coords[0][1],
             ln.coords[-1][0], ln.coords[-1][1]) for ln in lines]


# ======================= режим thin =======================
def _rooms_thin(lines: List[LineString], grid: float, min_area: float,
                max_area: Optional[float], simplify_tol: float) -> List[Pts]:
    faces = [p for p in polygonize(lines) if not p.is_empty and p.is_valid]
    rooms: List[Pts] = []
    for poly in faces:
        if poly.area < min_area:
            continue
        if max_area is not None and poly.area > max_area:
            continue
        pts = _poly_to_pts(poly, simplify_tol)
        if pts and _polygon_area(pts) >= min_area:
            rooms.append(pts)
    return rooms


def _close_step(mass, r: float, thickness: float) -> Tuple[Any, int]:
    """
    Один шаг замыкания радиусом r: раздули на r, сжали обратно, взяли
    разницу и оставили только компактные заплатки.

    Компактная заплатка = проём (её габарит порядка ширины двери).
    Заплатка во всю длину коридора компактной не является — коридор
    остаётся комнатой, а не превращается в стену.
    """
    try:
        closed = (mass.buffer(r, join_style=2, mitre_limit=2.0)
                      .buffer(-r, join_style=2, mitre_limit=2.0))
    except Exception:
        return mass, 0
    if closed.is_empty or not closed.is_valid:
        return mass, 0

    patches = closed.difference(mass)
    parts = [g for g in getattr(patches, "geoms", [patches])
             if g.geom_type == "Polygon" and not g.is_empty]
    if not parts:
        return mass, 0

    max_side = 2.0 * r + 2.0 * thickness
    max_patch_area = max_side * thickness * 4.0
    eps = max(thickness * 0.05, 1e-9)

    keep = []
    for p in parts:
        # у заплатки бывают волосяные усы нулевой ширины вдоль стены —
        # из-за них габарит выглядит огромным. Съедаем их эрозией.
        try:
            core = p.buffer(-eps, join_style=2)
        except Exception:
            core = p
        pieces = [g for g in getattr(core, "geoms", [core])
                  if g.geom_type == "Polygon" and not g.is_empty]
        for piece in pieces:
            q = piece.buffer(eps, join_style=2).intersection(p)
            if q.is_empty or q.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            minx, miny, maxx, maxy = q.bounds
            if max(maxx - minx, maxy - miny) > max_side:
                continue                  # длинная заплатка = коридор, не проём
            if q.area > max_patch_area:
                continue
            keep.append(q)

    if not keep:
        return mass, 0
    return unary_union([mass] + keep), len(keep)


def _close_openings(mass, door_gap: float, thickness: float):
    """
    Заделывает дверные и оконные проёмы, наращивая радиус замыкания.

    Ступени важны: сразу большой радиус слипает проём с узким коридором
    в одну длинную заплатку, и тогда отбраковывается всё сразу. Мелкие
    радиусы сначала запечатывают двери, а коридор к моменту большого
    радиуса уже отделён.
    """
    if door_gap <= 0:
        return mass
    radii = sorted({round(door_gap * k, 6) for k in (0.25, 0.35, 0.5)})
    total = 0
    for r in radii:
        if r <= 0:
            continue
        mass, n = _close_step(mass, r, thickness)
        total += n
    if total:
        logger.info("Заделано проёмов: %d", total)
    return mass


# ======================= режим solid =======================
def _rooms_solid(lines: List[LineString], thickness: float, min_area: float,
                 max_area: Optional[float], min_width: float,
                 simplify_tol: float, door_gap: float = 0.0,
                 measure: str = "inner") -> List[Pts]:
    """
    Комната = связная компонента свободного пространства внутри здания.

    1. линии стен -> сплошная масса (буфер на полтолщины);
    2. заделываем дверные и оконные проёмы;
    3. габаритная рамка минус масса стен = всё свободное место;
    4. компонента, касающаяся рамки, — это улица, её выбрасываем;
    5. оставшиеся компоненты расширяем на полтолщины — буфер съел ровно
       столько же с каждой грани стены, так что контур возвращается на
       внутреннюю грань (measure="inner", площадь для уборки).
       measure="center" даёт контур по осям стен (площадь по СП).
    """
    if not lines or thickness <= 0:
        return []

    merged = unary_union(lines)
    # +2 % — чтобы буферы двух граней стены гарантированно слиплись и не
    # оставили волосяную щель внутри стены
    half = thickness / 2.0 * 1.02
    mass = merged.buffer(half, cap_style=2, join_style=2, mitre_limit=2.0)
    if mass.is_empty:
        return []

    # добиваем волосяные щели, которые не закрыл мост (0.15 толщины — безопасно,
    # такой радиус не заливает даже узкий коридор)
    eps = thickness * 0.15
    if eps > 0:
        closed = mass.buffer(eps, join_style=2).buffer(-eps, join_style=2)
        if not closed.is_empty and closed.is_valid:
            mass = closed

    # заделываем дверные/оконные проёмы
    mass = _close_openings(mass, door_gap, thickness)

    minx, miny, maxx, maxy = mass.bounds
    pad = thickness * 4
    env = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    free = env.difference(mass)
    parts = [g for g in getattr(free, "geoms", [free])
             if g.geom_type == "Polygon" and not g.is_empty]

    border = env.exterior
    rooms: List[Pts] = []
    for part in parts:
        if part.intersects(border):        # улица, а не комната
            continue
        if part.area < min_area * 0.5:
            continue
        if max_area is not None and part.area > max_area:
            continue
        # тест на «щель»: комната должна быть шире min_width
        if min_width > 0 and part.buffer(-min_width / 2.0).is_empty:
            continue
        grow = half * (2.0 if measure == "center" else 1.0)
        grown = part.buffer(grow, join_style=2, mitre_limit=2.0)
        pts = _poly_to_pts(grown, simplify_tol)
        if not pts:
            continue
        area = _polygon_area(pts)
        if area < min_area or (max_area is not None and area > max_area):
            continue
        rooms.append(pts)
    return rooms


# ======================= главная точка входа =======================
def detect_rooms(walls: Iterable[Wall],
                 mode: str = "auto",
                 wall_thickness: Optional[float] = None,
                 door_gap: Optional[float] = None,
                 min_area: float = 1.0,
                 max_area: Optional[float] = None,
                 min_width: Optional[float] = None,
                 grid: Optional[float] = None,
                 simplify_tol: Optional[float] = None,
                 measure: str = "inner") -> List[Pts]:
    """
    Строит комнаты по отрезкам стен.

    mode:            "auto" | "thin" | "solid"
    wall_thickness:  толщина стены; None — определяется автоматически
    door_gap:        макс. ширина проёма, который надо зашить; None — авто
    min_area:        минимальная площадь комнаты
    max_area:        максимальная площадь (отсекает «улицу»), None — без лимита
    min_width:       минимальная ширина комнаты, отсекает щели между линиями
    grid:            шаг округления координат (сшивает микрозазоры)
    simplify_tol:    допуск упрощения контура; режет число точек на сцене
    measure:         "inner" — площадь по внутренним граням стен (для уборки),
                     "center" — по осям стен (как в экспликации СП)
    """
    lines = _as_lines(walls)
    if not lines:
        return []

    ext = _extent(lines)
    if ext <= 0:
        return []

    if grid is None:
        grid = max(ext * 1e-5, 1e-9)          # ~1 мм на плане 100 м
    lines = _dedupe(lines, grid)
    lines = _node(lines, grid)
    if not lines:
        return []

    thickness, frac = (wall_thickness, 1.0) if wall_thickness else \
        estimate_wall_thickness(lines)

    if mode == "auto":
        mode = "solid" if (thickness > 0 and frac >= 0.35) else "thin"
        logger.info("Режим определён автоматически: %s (толщина=%.4f, доля пар=%.2f)",
                    mode, thickness, frac)

    if simplify_tol is None:
        simplify_tol = (thickness * 0.25) if thickness > 0 else ext * 1e-4
    if min_width is None:
        min_width = (thickness * 2.5) if thickness > 0 else 0.0

    if mode == "solid":
        if not thickness:
            logger.warning("Толщина стен не определена, откат в режим thin")
            return _rooms_thin(lines, grid, min_area, max_area, simplify_tol)
        lines = _parallel_wall_faces(lines, thickness)
        lines = _node(lines, grid)
        if not lines:
            return []
        if door_gap is None:
            door_gap = max(thickness * 8, ext * 0.01)
        # мостами чиним только неаккуратные стыки линий; проёмы закрывает
        # _close_openings внутри _rooms_solid
        bridges = _bridge_openings(lines, thickness * 2.0)
        if bridges:
            logger.info("Сшито стыков стен: %d", len(bridges))
            lines = _node(lines + bridges, grid)
        return _rooms_solid(lines, thickness, min_area, max_area, min_width,
                            simplify_tol, door_gap, measure)

    if door_gap:
        bridges = _bridge_openings(lines, door_gap)
        if bridges:
            lines = _node(lines + bridges, grid)
    return _rooms_thin(lines, grid, min_area, max_area, simplify_tol)


# ======================= обратная совместимость =======================
def build_rooms_from_walls(walls: List[Wall],
                           snap_tolerance: float = 0.05,
                           min_area: float = 0.5,
                           **kwargs) -> List[Pts]:
    """
    Старая сигнатура, которую зовёт app.py.

    snap_tolerance теперь работает как шаг округления координат и как
    максимальный зашиваемый зазор — прежнего snap(merged, merged, tol),
    который и выжирал память, больше нет.
    """
    kwargs.setdefault("mode", "auto")
    kwargs.setdefault("door_gap", snap_tolerance if snap_tolerance else None)
    kwargs.setdefault("grid", (snap_tolerance / 10.0) if snap_tolerance else None)
    return detect_rooms(walls, min_area=min_area, **kwargs)


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
    """Нодирование теперь внутри detect_rooms; оставлено для совместимости."""
    return walls