"""
dxf_analyzer — модуль для извлечения комнат из DXF-файлов.
Использует новый room_builder (Shapely polygonize) и автонастройку параметров.
"""
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

import ezdxf
from shapely.geometry import LineString, Point, Polygon, MultiLineString
from shapely.ops import snap, unary_union

from room_builder import build_rooms_from_walls

logger = logging.getLogger(__name__)


# ------------------------- классы данных -------------------------
@dataclass
class Door:
    id: int
    position: Tuple[float, float]
    width: float
    room1_id: Optional[int] = None
    room2_id: Optional[int] = None

@dataclass
class Window:
    id: int
    position: Tuple[float, float]
    width: float
    room_id: Optional[int] = None

@dataclass
class Room:
    id: int
    polygon: Polygon
    area_sqm: float
    room_type: str = "не определён"
    text_label: str = ""
    door_ids: List[int] = field(default_factory=list)
    window_ids: List[int] = field(default_factory=list)
    neighbor_ids: List[int] = field(default_factory=list)

@dataclass
class Building:
    rooms: List[Room] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)
    windows: List[Window] = field(default_factory=list)

@dataclass
class AnalysisResult:
    success: bool
    building: Optional[Building] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# ------------------- вспомогательные функции -------------------
def _get_units_scale(doc) -> float:
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits == 4: return 0.001      # мм
    elif insunits == 5: return 0.01     # см
    elif insunits == 6: return 1.0      # м
    elif insunits == 1: return 0.0254   # дюймы
    logger.warning("INSUNITS не определён, предполагаем миллиметры")
    return 0.001

def _extract_lines(entity, scale: float) -> List[LineString]:
    segs = []
    if entity.dxftype() == 'LINE':
        s, e = entity.dxf.start, entity.dxf.end
        segs.append(LineString([(s.x*scale, s.y*scale), (e.x*scale, e.y*scale)]))
    elif entity.dxftype() == 'LWPOLYLINE':
        pts = entity.get_points('xy')
        if len(pts) >= 2:
            scaled = [(x*scale, y*scale) for x, y in pts]
            for i in range(len(scaled)-1):
                segs.append(LineString([scaled[i], scaled[i+1]]))
    elif entity.dxftype() == 'ARC':
        # Аппроксимируем дугу отрезками (10 сегментов)
        start = entity.dxf.start_angle
        end = entity.dxf.end_angle
        radius = entity.dxf.radius * scale
        center = (entity.dxf.center.x * scale, entity.dxf.center.y * scale)
        if end < start:
            end += 360
        step = (end - start) / 10.0
        pts = []
        for i in range(11):
            angle = math.radians(start + i * step)
            pts.append((center[0] + radius * math.cos(angle),
                        center[1] + radius * math.sin(angle)))
        for i in range(len(pts)-1):
            segs.append(LineString([pts[i], pts[i+1]]))
    return segs

def _collect_segments(msp, layer_names: List[str], scale: float,
                      use_heuristics: bool, min_len_mm: float, max_len_mm: float) -> List[LineString]:
    segments = []
    for name in layer_names:
        for entity in msp.query(f'*[layer=="{name}"]'):
            segments.extend(_extract_lines(entity, scale))
    if not segments and use_heuristics:
        logger.info("Слои стен не найдены, применяю эвристики")
        min_len = min_len_mm * scale
        max_len = max_len_mm * scale
        for entity in msp.query('*'):
            if entity.dxftype() in ('SPLINE','TEXT','MTEXT','DIMENSION','ATTDEF'):
                continue
            segs = _extract_lines(entity, scale)
            for seg in segs:
                if min_len < seg.length < max_len:
                    segments.append(seg)
    return segments

def _walls_from_segments(segments: List[LineString]) -> List[Tuple[float, float, float, float]]:
    walls = []
    for line in segments:
        coords = list(line.coords)
        if len(coords) >= 2:
            walls.append((coords[0][0], coords[0][1], coords[-1][0], coords[-1][1]))
    return walls

def _find_text_inside(polygon: Polygon, text_entities: List, scale: float) -> str:
    found = []
    for text in text_entities:
        insert = text.dxf.insert
        point = Point(insert.x * scale, insert.y * scale)
        if polygon.contains(point) or polygon.touches(point):
            found.append(text.dxf.text)
    return " ".join(found).strip()

def _guess_room_type(text: str, keywords: Dict[str, List[str]]) -> str:
    text_lower = text.lower()
    for room_type, words in keywords.items():
        for w in words:
            if w in text_lower:
                return room_type
    return "не определён"

# ------------------- главная функция -------------------
def analyze_dxf(filepath: str, config: Optional[Dict] = None) -> AnalysisResult:
    if config is None:
        config = {}
    warnings = []
    try:
        doc = ezdxf.readfile(filepath)
        msp = doc.modelspace()
    except Exception as e:
        logger.error(f"Не удалось загрузить DXF: {e}")
        return AnalysisResult(False, error=f"Ошибка чтения DXF: {e}")

    scale = _get_units_scale(doc)
    logger.info(f"Масштабный коэффициент в метры: {scale}")

    # --- автоопределение слоёв стен ---
    layers = set()
    for e in msp:
        layers.add(e.dxf.layer)
    wall_layers = [l for l in layers if any(kw in l.lower() for kw in ["стен", "wall", "перегород", "partition"])]
    if not wall_layers:
        # слои с наибольшим количеством LINE/LWPOLYLINE
        layer_counts = defaultdict(int)
        for e in msp:
            if e.dxftype() in ('LINE','LWPOLYLINE'):
                layer_counts[e.dxf.layer] += 1
        if layer_counts:
            top = sorted(layer_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            wall_layers = [l for l, _ in top]
    if not wall_layers:
        wall_layers = ["0"]

    # --- параметры из config или авто ---
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits in (4,0):
        gap_tol = config.get("gap_tolerance_mm", 300) * scale
        min_len = config.get("min_wall_length_mm", 200) * scale
        max_len = config.get("max_wall_length_mm", 15000) * scale
    else:
        gap_tol = config.get("gap_tolerance_mm", 30) * scale
        min_len = config.get("min_wall_length_mm", 20) * scale
        max_len = config.get("max_wall_length_mm", 1500) * scale

    # --- сбор отрезков ---
    segments = _collect_segments(msp, wall_layers, scale,
                                 config.get("use_heuristics_if_no_layers", True),
                                 config.get("min_wall_length_mm", 200),
                                 config.get("max_wall_length_mm", 15000))
    if not segments:
        return AnalysisResult(False, error="Не найдены линии стен")

    logger.info(f"Собрано отрезков: {len(segments)}")

    # --- преобразуем в список стен для room_builder ---
    walls = _walls_from_segments(segments)

    # --- передаём snap_tolerance в room_builder ---
    rooms_pts = build_rooms_from_walls(walls, snap_tolerance=gap_tol,
                                       min_area=config.get("min_room_area_sqm", 0.5))
    if not rooms_pts:
        return AnalysisResult(False, error="Не удалось построить замкнутые контуры")

    # --- текстовые метки ---
    text_layers = config.get("text_layers", [])
    text_entities = []
    for name in text_layers:
        for t in msp.query(f'TEXT[layer=="{name}"]'):
            text_entities.append(t)
    if not text_entities:
        for t in msp.query('TEXT'):
            text_entities.append(t)

    # --- формируем объекты Room ---
    rooms = []
    for i, pts in enumerate(rooms_pts):
        poly = Polygon(pts)
        if not poly.is_valid or poly.area < config.get("min_room_area_sqm", 0.5):
            continue
        text = _find_text_inside(poly, text_entities, scale)
        room_type = _guess_room_type(text, config.get("room_type_keywords", {}))
        rooms.append(Room(id=i, polygon=poly, area_sqm=poly.area,
                          room_type=room_type, text_label=text))

    building = Building(rooms=rooms)
    return AnalysisResult(True, building=building, warnings=warnings)