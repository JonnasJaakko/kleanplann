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
