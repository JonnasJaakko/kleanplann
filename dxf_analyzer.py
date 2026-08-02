"""
dxf_analyzer — извлечение комнат из DXF-файлов.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import ezdxf
from ezdxf import path as ezpath
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from room_builder import detect_rooms, estimate_wall_thickness

logger = logging.getLogger(__name__)

MAX_ENTITIES = 500_000        # больше сущностей не читаем
MAX_SEGMENTS = 400_000        # больше отрезков не копим
MAX_BLOCK_DEPTH = 4           # глубина разворачивания вложенных блоков

WALL_KEYWORDS = ("стен", "перегород", "wall", "partition", "a-wall", "несущ")
NOISE_KEYWORDS = (
    "двер", "door", "окн", "window", "витраж",
    "мебел", "furn", "оборуд", "equip", "сантех", "plumb", "техно",
    "размер", "dim", "вынос", "leader", "текст", "text", "шрифт",
    "штрих", "hatch", "заливк",
    "ось", "оси", "axis", "grid", "сетк", "координ",
    "рамк", "штамп", "title", "лист",
    "лестниц", "stair", "пандус", "площад",
    "элект", "elec", "вент", "hvac", "отоплен", "спринкл",
    "defpoints", "озелен", "благоустр", "мусор",
)
GEOM_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")


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
    stats: Dict[str, Any] = field(default_factory=dict)


_UNIT_SCALE = {
    1: 0.0254,   # дюймы
    2: 0.3048,   # футы
    4: 0.001,    # мм
    5: 0.01,     # см
    6: 1.0,      # м
    8: 1e-6,     # микроны
    9: 0.001,    # мм (legacy)
}


def _get_units_scale(doc) -> float:
    insunits = doc.header.get("$INSUNITS", 0)
    scale = _UNIT_SCALE.get(insunits)
    if scale is None:
        logger.warning("INSUNITS=%s не распознан, предполагаем миллиметры", insunits)
        return 0.001
    return scale


def _guess_scale_by_extent(segments: List[LineString], scale: float) -> float:
    if not segments:
        return scale
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for s in segments:
        a, b, c, d = s.bounds
        minx = min(minx, a); miny = min(miny, b)
        maxx = max(maxx, c); maxy = max(maxy, d)
    diag = math.hypot(maxx - minx, maxy - miny)
    if 3.0 <= diag <= 3000.0:            # правдоподобное здание, 3 м … 3 км
        return scale
    for factor in (1000.0, 100.0, 10.0, 0.1, 0.01, 0.001):
        if 3.0 <= diag * factor <= 3000.0:
            logger.warning("Габарит %.1f подозрителен, масштаб поправлен в %g раз", diag, factor)
            return scale * factor
    return scale


def _layer_is_usable(doc, name: str) -> bool:
    try:
        layer = doc.layers.get(name)
    except Exception:
        return True
    try:
        if layer.is_off() or layer.is_frozen():
            return False
    except Exception:
        pass
    return True


def _is_noise_layer(name: str) -> bool:
    low = name.lower()
    if any(kw in low for kw in WALL_KEYWORDS):
        return False
    return any(kw in low for kw in NOISE_KEYWORDS)


def _iter_entities(msp, depth: int = 0):
    count = 0
    for e in msp:
        count += 1
        if count > MAX_ENTITIES:
            logger.warning("Достигнут лимит сущностей %d", MAX_ENTITIES)
            return
        if e.dxftype() == "INSERT":
            if depth >= MAX_BLOCK_DEPTH:
                continue
            try:
                sub = list(e.virtual_entities())
            except Exception as ex:
                logger.debug("Блок %s не развернулся: %s", e.dxf.name, ex)
                continue
            yield from _iter_entities(sub, depth + 1)
        else:
            yield e


def _entity_segments(entity, scale: float, sagitta: float) -> List[LineString]:
    dxftype = entity.dxftype()
    if dxftype not in GEOM_TYPES:
        return []
    try:
        p = ezpath.make_path(entity)
    except Exception:
        return []
    if len(p) == 0:
        return []
    try:
        pts = [(v.x * scale, v.y * scale) for v in p.flattening(distance=sagitta)]
    except Exception:
        return []
    segs = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a != b:
            segs.append(LineString([a, b]))
    return segs


def _collect_by_layer(msp, doc, scale: float, sagitta: float) -> Dict[str, List[LineString]]:
    """Собирает отрезки, сгруппированные по слою."""
    by_layer: Dict[str, List[LineString]] = defaultdict(list)
    total = 0
    for e in _iter_entities(msp):
        layer = getattr(e.dxf, "layer", "0")
        if not _layer_is_usable(doc, layer):
            continue
        segs = _entity_segments(e, scale, sagitta)
        if not segs:
            continue
        by_layer[layer].extend(segs)
        total += len(segs)
        if total > MAX_SEGMENTS:
            logger.warning("Достигнут лимит отрезков %d", MAX_SEGMENTS)
            break
    return by_layer


def _pick_wall_layers(by_layer: Dict[str, List[LineString]],
                      explicit: Optional[List[str]] = None) -> List[str]:
    if explicit:
        present = [l for l in explicit if l in by_layer]
        if present:
            return present

    named = [l for l in by_layer if any(kw in l.lower() for kw in WALL_KEYWORDS)]
    if named:
        return named

    lengths = {l: sum(s.length for s in segs) for l, segs in by_layer.items()
               if not _is_noise_layer(l)}
    if not lengths:
        lengths = {l: sum(s.length for s in segs) for l, segs in by_layer.items()}
    if not lengths:
        return []

    ranked = sorted(lengths.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in ranked)
    picked, acc = [], 0.0
    for name, length in ranked:
        picked.append(name)
        acc += length
        if total > 0 and acc / total >= 0.7:   # 70 % длины линий чертежа
            break
    return picked


# ------------------- текстовые подписи -------------------
def _collect_texts(msp, doc, scale: float) -> List[Tuple[Point, str]]:
    out = []
    for e in _iter_entities(msp):
        t = e.dxftype()
        if t not in ("TEXT", "MTEXT"):
            continue
        layer = getattr(e.dxf, "layer", "0")
        if not _layer_is_usable(doc, layer):
            continue
        try:
            if t == "MTEXT":
                content = e.plain_text()
                ins = e.dxf.insert
            else:
                content = e.dxf.text
                ins = e.dxf.insert
            content = (content or "").strip()
            if content:
                out.append((Point(ins.x * scale, ins.y * scale), content))
        except Exception:
            continue
    return out


def _label_rooms(rooms_pts: List[List[Tuple[float, float]]],
                 texts: List[Tuple[Point, str]]) -> List[str]:
    labels = [""] * len(rooms_pts)
    if not texts:
        return labels
    polys = [Polygon(p) for p in rooms_pts]
    tree = STRtree([pt for pt, _ in texts])
    for i, poly in enumerate(polys):
        if poly.is_empty or not poly.is_valid:
            continue
        found = []
        for idx in tree.query(poly):
            pt, content = texts[idx]
            if poly.contains(pt):
                found.append(content)
        labels[i] = " ".join(found).strip()
    return labels


def _guess_room_type(text: str, keywords: Dict[str, List[str]]) -> str:
    low = text.lower()
    for room_type, words in keywords.items():
        for w in words:
            if w and w.lower() in low:
                return room_type
    return "не определён"


# ------------------- сбор стен (используется и из app.py) -------------------
def load_wall_segments(filepath: str, config: Optional[Dict] = None):
    config = config or {}
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()

    scale = _get_units_scale(doc)
    sagitta = config.get("arc_sagitta_m", 0.02) / scale   # погрешность сплющивания дуг

    by_layer = _collect_by_layer(msp, doc, scale, sagitta)
    if not by_layer:
        raise ValueError("В чертеже не найдено линейной геометрии")

    wall_layers = _pick_wall_layers(by_layer, config.get("wall_layers"))
    if not wall_layers:
        raise ValueError("Не удалось определить слои стен")

    segments: List[LineString] = []
    for name in wall_layers:
        segments.extend(by_layer[name])
    if not segments:
        raise ValueError("Не найдены линии стен")

    warnings: List[str] = []
    corrected = _guess_scale_by_extent(segments, scale)
    if corrected != scale:
        k = corrected / scale
        segments = [LineString([(x * k, y * k) for x, y in s.coords]) for s in segments]
        warnings.append(f"Единицы чертежа уточнены по габаритам (×{k:g})")
        scale = corrected

    thickness = config.get("wall_thickness_m")
    frac = 1.0
    if not thickness:
        thickness, frac = estimate_wall_thickness(segments, max_thickness=0.8)
    logger.info("Слои стен: %s; отрезков: %d; толщина ≈ %.3f м (доля пар %.2f)",
                wall_layers, len(segments), thickness or 0.0, frac)

    info = {
        "scale": scale,
        "wall_layers": wall_layers,
        "all_layers": sorted(by_layer),
        "wall_thickness_m": thickness or None,
        "paired_fraction": frac,
        "warnings": warnings,
    }
    return doc, segments, info


def rooms_from_segments(segments: List[LineString], info: Dict,
                        config: Optional[Dict] = None) -> List[List[Tuple[float, float]]]:
    """Комнаты по уже собранным отрезкам (в метрах)."""
    config = config or {}
    walls = [(s.coords[0][0], s.coords[0][1], s.coords[-1][0], s.coords[-1][1])
             for s in segments]
    return detect_rooms(
        walls,
        mode=config.get("mode", "auto"),
        wall_thickness=config.get("wall_thickness_m") or info.get("wall_thickness_m"),
        door_gap=config.get("door_gap_m", 1.6),
        min_area=config.get("min_room_area_sqm", 1.5),
        max_area=config.get("max_room_area_sqm"),
        min_width=config.get("min_room_width_m", 0.7),
        simplify_tol=config.get("simplify_tol_m", 0.03),
        measure=config.get("measure", "inner"),
    )


# ------------------- главная функция -------------------
def analyze_dxf(filepath: str, config: Optional[Dict] = None) -> AnalysisResult:
    config = config or {}

    try:
        doc, segments, info = load_wall_segments(filepath, config)
    except Exception as e:
        logger.error("Не удалось разобрать DXF: %s", e)
        return AnalysisResult(False, error=f"Ошибка чтения DXF: {e}")

    msp = doc.modelspace()
    scale = info["scale"]
    warnings = list(info["warnings"])
    thickness = info["wall_thickness_m"]
    walls = [(s.coords[0][0], s.coords[0][1], s.coords[-1][0], s.coords[-1][1])
             for s in segments]

    rooms_pts = detect_rooms(
        walls,
        mode=config.get("mode", "auto"),
        wall_thickness=thickness or None,
        door_gap=config.get("door_gap_m", 1.6),
        min_area=config.get("min_room_area_sqm", 1.5),
        max_area=config.get("max_room_area_sqm"),
        min_width=config.get("min_room_width_m", 0.7),
        simplify_tol=config.get("simplify_tol_m", 0.03),
        measure=config.get("measure", "inner"),
    )
    if not rooms_pts:
        return AnalysisResult(False, error="Не удалось построить замкнутые контуры",
                              warnings=warnings)

    texts = _collect_texts(msp, doc, scale)
    labels = _label_rooms(rooms_pts, texts)
    keywords = config.get("room_type_keywords", {})

    rooms: List[Room] = []
    for i, pts in enumerate(rooms_pts):
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        label = labels[i]
        rooms.append(Room(id=len(rooms), polygon=poly, area_sqm=poly.area,
                          room_type=_guess_room_type(label, keywords),
                          text_label=label))

    stats = {
        "wall_layers": info["wall_layers"],
        "segments": len(segments),
        "wall_thickness_m": thickness,
        "scale": scale,
        "rooms": len(rooms),
        "total_area_sqm": sum(r.area_sqm for r in rooms),
    }
    logger.info("Построено комнат: %d, суммарная площадь %.1f м²",
                stats["rooms"], stats["total_area_sqm"])

    return AnalysisResult(True, building=Building(rooms=rooms),
                          warnings=warnings, stats=stats)
