import ezdxf, logging, math
from dataclasses import dataclass
from typing import List, Tuple, Optional
from shapely.geometry import LineString, Polygon, Point, MultiLineString
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

# Слои, которые считаются стенами
WALL_LAYERS = {
    "стены", "walls", "wall", "a-wall", "a-wall-int", "a-wall-ext",
    "s-wall", "s-col", "s-core", "a-glaz", "a-glaz-int", "a-glaz-ext",
    "0"
}

IGNORE_LAYERS = {
    "мебель", "furniture", "сантехника", "plumbing", "оборудование", "equipment",
    "жалюзи", "blinds", "размерная", "dimension", "grid", "оси", "dote",
    "текст", "text", "штриховка_мебель", "hatch_furniture",
    "двери", "doors", "окна", "windows"
}

MIN_WALL_LENGTH = 0.3   # 300 мм – всё, что короче, не может быть стеной

@dataclass
class TempWall:
    polyline: List[Tuple[float, float]]
    thickness: float = 0.1

def _get_scale(doc) -> float:
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits == 4: return 0.001
    if insunits == 5: return 0.01
    if insunits == 6: return 1.0
    if insunits == 1: return 0.0254
    return 0.001

def _is_dashed(entity) -> bool:
    if hasattr(entity.dxf, 'linetype'):
        lt = entity.dxf.linetype.lower()
        if any(kw in lt for kw in ['dash', 'dot', 'hidden', 'dashed', 'acad_iso', 'phantom']):
            return True
    return False

def _is_wall_layer(layer_name: str) -> bool:
    low = layer_name.lower()
    for ignore in IGNORE_LAYERS:
        if ignore in low:
            return False
    for wall in WALL_LAYERS:
        if wall in low:
            return True
    if low == "0" or "wall" in low:
        return True
    return False

def _extract_wall_lines(msp, scale: float) -> List[LineString]:
    """Собирает только отрезки стен, игнорируя пунктирные, короткие и нестеновые слои."""
    segments = []
    for entity in msp:
        if _is_dashed(entity):
            continue
        layer = entity.dxf.layer
        if not _is_wall_layer(layer):
            continue

        if entity.dxftype() == 'LINE':
            s, e = entity.dxf.start, entity.dxf.end
            seg = LineString([(s.x * scale, s.y * scale), (e.x * scale, e.y * scale)])
            if seg.length >= MIN_WALL_LENGTH:
                segments.append(seg)
        elif entity.dxftype() == 'LWPOLYLINE':
            pts = entity.get_points('xy')
            if len(pts) >= 2:
                scaled = [(x * scale, y * scale) for x, y in pts]
                for i in range(len(scaled) - 1):
                    seg = LineString([scaled[i], scaled[i+1]])
                    if seg.length >= MIN_WALL_LENGTH:
                        segments.append(seg)
        elif entity.dxftype() == 'HATCH':
            if any(w in layer.lower() for w in WALL_LAYERS):
                for path in entity.paths:
                    if path.path_type_flags & 2:
                        try:
                            pts = [(v.x * scale, v.y * scale) for v in path.vertices]
                        except AttributeError:
                            pts = [(v[0] * scale, v[1] * scale) for v in path.vertices]
                        if len(pts) >= 2:
                            for i in range(len(pts)):
                                seg = LineString([pts[i], pts[(i+1)%len(pts)]])
                                if seg.length >= MIN_WALL_LENGTH:
                                    segments.append(seg)
    return segments

def import_dxf(filepath: str, progress_callback=None) -> dict:
    """
    Возвращает:
        'walls': List[TempWall] – отрезки стен (центрированы)
        'bounds': (minx, miny, maxx, maxy) – габариты до центрирования (для информации)
    """
    try:
        if progress_callback: progress_callback(10, "Чтение DXF...")
        doc = ezdxf.readfile(filepath)
    except Exception as e:
        logger.error(f"Ошибка чтения: {e}")
        return {'walls': [], 'bounds': None}

    msp = doc.modelspace()
    scale = _get_scale(doc)

    if progress_callback: progress_callback(20, "Сбор линий стен...")
    lines = _extract_wall_lines(msp, scale)
    logger.info(f"Собрано отрезков стен: {len(lines)}")

    if not lines:
        # Резервный режим: берём все длинные линии
        logger.warning("Стены не найдены по слоям, применяю резервную эвристику")
        for entity in msp:
            if _is_dashed(entity):
                continue
            if entity.dxftype() == 'LINE':
                s, e = entity.dxf.start, entity.dxf.end
                seg = LineString([(s.x*scale, s.y*scale), (e.x*scale, e.y*scale)])
                if seg.length >= 0.5:
                    lines.append(seg)
            elif entity.dxftype() == 'LWPOLYLINE':
                pts = entity.get_points('xy')
                if len(pts) >= 2:
                    scaled = [(x*scale, y*scale) for x, y in pts]
                    for i in range(len(scaled)-1):
                        seg = LineString([scaled[i], scaled[i+1]])
                        if seg.length >= 0.5:
                            lines.append(seg)
    if not lines:
        return {'walls': [], 'bounds': None}

    # Объединяем, упрощаем, центрируем
    if progress_callback: progress_callback(50, "Упрощение и центрирование...")
    merged = unary_union(lines)
    simplified = merged.simplify(0.1, preserve_topology=True)

    # Центрирование
    bounds = simplified.bounds
    center_x = (bounds[0] + bounds[2]) / 2
    center_y = (bounds[1] + bounds[3]) / 2

    # Разбиваем на отрезки
    if simplified.geom_type == 'LineString':
        line_list = [simplified]
    elif simplified.geom_type == 'MultiLineString':
        line_list = list(simplified.geoms)
    else:
        line_list = []

    walls = []
    for line in line_list:
        shifted = [(x - center_x, y - center_y) for x, y in line.coords]
        if len(shifted) >= 2:
            walls.append(TempWall(shifted, 0.1))

    if progress_callback: progress_callback(100, "Готово")
    return {'walls': walls, 'bounds': bounds}