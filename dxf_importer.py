import ezdxf, logging, math
from dataclasses import dataclass
from typing import List, Tuple, Optional
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

WALL_LAYERS = {"стены", "walls", "wall", "a-wall", "a-wall-int", "a-wall-ext", "0"}
PUNCTUATION_LAYERS = {"размерная", "dimension", "grid", "оси", "dote"}
MAX_POINTS = 50_000

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

def _is_wall_layer(layer_name: str) -> bool:
    low = layer_name.lower()
    if any(p in low for p in PUNCTUATION_LAYERS):
        return False
    return any(w in low for w in WALL_LAYERS)

def _extract_wall_lines(msp, scale: float) -> List[LineString]:
    segments = []
    for entity in msp:
        if not _is_wall_layer(entity.dxf.layer):
            continue
        if entity.dxftype() == 'LINE':
            s, e = entity.dxf.start, entity.dxf.end
            segments.append(LineString([(s.x*scale, s.y*scale), (e.x*scale, e.y*scale)]))
        elif entity.dxftype() == 'LWPOLYLINE':
            pts = entity.get_points('xy')
            if len(pts) >= 2:
                scaled = [(x*scale, y*scale) for x, y in pts]
                for i in range(len(scaled)-1):
                    segments.append(LineString([scaled[i], scaled[i+1]]))
        elif entity.dxftype() == 'HATCH':
            for path in entity.paths:
                if path.path_type_flags & 2:
                    try:
                        pts = [(v.x*scale, v.y*scale) for v in path.vertices]
                    except AttributeError:
                        pts = [(v[0]*scale, v[1]*scale) for v in path.vertices]
                    if len(pts) >= 2:
                        for i in range(len(pts)):
                            segments.append(LineString([pts[i], pts[(i+1)%len(pts)]]))
    return segments

def _count_points(geom) -> int:
    """Считает общее количество точек в LineString или MultiLineString."""
    if geom.geom_type == 'LineString':
        return len(geom.coords)
    elif geom.geom_type == 'MultiLineString':
        return sum(len(line.coords) for line in geom.geoms)
    else:
        return 0

def import_dxf(filepath: str, progress_callback=None) -> dict:
    try:
        if progress_callback: progress_callback(10, "Чтение DXF...")
        doc = ezdxf.readfile(filepath)
    except Exception as e:
        logger.error(f"Ошибка чтения: {e}")
        return {'walls': []}

    msp = doc.modelspace()
    scale = _get_scale(doc)

    if progress_callback: progress_callback(20, "Сбор линий стен...")
    lines = _extract_wall_lines(msp, scale)
    if not lines:
        logger.warning("Стены не найдены, пробую все слои")
        for entity in msp:
            if hasattr(entity.dxf, 'linetype') and 'dash' in entity.dxf.linetype.lower():
                continue
            if entity.dxftype() == 'LINE':
                s, e = entity.dxf.start, entity.dxf.end
                lines.append(LineString([(s.x*scale, s.y*scale), (e.x*scale, e.y*scale)]))
            elif entity.dxftype() == 'LWPOLYLINE':
                pts = entity.get_points('xy')
                if len(pts) >= 2:
                    scaled = [(x*scale, y*scale) for x, y in pts]
                    for i in range(len(scaled)-1):
                        lines.append(LineString([scaled[i], scaled[i+1]]))
    if not lines:
        return {'walls': []}

    if progress_callback: progress_callback(40, "Упрощение геометрии...")
    merged = unary_union(lines)
    simplified = merged.simplify(0.1, preserve_topology=True)
    total_pts = _count_points(simplified)
    if total_pts > MAX_POINTS:
        logger.info(f"Точек после упрощения: {total_pts}, ещё упрощаю...")
        simplified = simplified.simplify(0.2, preserve_topology=True)

    if progress_callback: progress_callback(60, "Разбор на отрезки...")
    if simplified.geom_type == 'LineString':
        line_list = [simplified]
    elif simplified.geom_type == 'MultiLineString':
        line_list = list(simplified.geoms)
    else:
        line_list = []

    if progress_callback: progress_callback(80, "Центрирование...")
    bounds = simplified.bounds
    center_x = (bounds[0] + bounds[2]) / 2
    center_y = (bounds[1] + bounds[3]) / 2

    walls = []
    for line in line_list:
        shifted = [(x - center_x, y - center_y) for x, y in line.coords]
        if len(shifted) >= 2:
            walls.append(TempWall(shifted, 0.1))

    if progress_callback: progress_callback(100, "Готово")
    return {'walls': walls}