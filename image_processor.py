"""Распознавание помещений на планах пожарной эвакуации (российский стандарт).

Версия 3: цельные стены вместо "осколков", закрытие дверных проёмов без
заливки крупных проходов, детекция лестниц как групп параллельных линий,
устойчивая постобработка (склейка соседних осколков одной комнаты).

Публичные функции (сигнатуры и форматы возврата не менялись):
    detect_floor_plan(image, min_area_px=None) -> dict
    detect_walls(image) -> List[Polygon]
    detect_room_candidates(image) -> List[dict]
    draw_walls(image, contours, color=(0, 0, 255), thickness=2) -> np.ndarray
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Tuple, Optional, Any

import cv2
import numpy as np

Point = Tuple[float, float]
Polygon = List[Point]

# ================== ГЛОБАЛЬНЫЕ НАСТРОЙКИ (можно подстраивать) ==================
DEBUG = True                 # True -> в папку DEBUG_DIR сохраняются промежуточные маски
DEBUG_DIR = "debug"

# --- Постобработка / склейка ---
MERGE_CLOSE_ROOMS = True         # Склеивать полигоны-осколки одной комнаты
MERGE_DISTANCE_FACTOR = 0.02     # Доля от min(h, w) - порог склейки по центрам
MERGE_OVERLAP_IOU = 0.15         # Доп. порог склейки по пересечению bbox (IoU)
SMALL_FRAGMENT_AREA_FACTOR = 0.15  # Осколок < этой доли от медианной площади считается "мелким"
                                    # и агрессивно ищет соседа для склейки

# --- Фон / рамка листа ---
# Полосы фона вокруг здания (край листа, области за пределами постройки) не
# должны попадать в список комнат. Полигон отбраковывается, если он либо
# занимает почти весь лист целиком, либо представляет собой тонкую полосу,
# растянутую почти на всю ширину/высоту изображения.
BACKGROUND_FULL_SPAN_RATIO = 0.95   # bbox по обеим осям > этой доли размера листа -> фон
BACKGROUND_BAND_SPAN_RATIO = 0.85   # bbox по одной оси > этой доли размера листа
BACKGROUND_BAND_THIN_FACTOR = 0.06  # ...и при этом другая сторона тоньше этой доли min(h, w)
# Доп. эвристика для "остаточных" полос фона, которые НЕ тянутся почти на
# весь лист (например, пустое поле сбоку от здания на неполную высоту):
# длинная и почти идеально прямоугольная (без выступов/ниш) область с
# большим соотношением сторон — подозрительна. Если легитимные узкие
# коридоры в проекте так же вытянуты, увеличьте BACKGROUND_ASPECT_MAX.
BACKGROUND_ASPECT_MAX = 5.0
BACKGROUND_COMPACTNESS_MIN = 0.65

# --- Сглаживание контуров комнат ---
POLY_SIMPLIFY_EPS_FACTOR = 0.006    # Доля от периметра для approxPolyDP (больше = меньше вершин)
ORTHOGONAL_SNAP = True              # Подравнивать почти гориз./вертик. рёбра под 90°
ORTHOGONAL_ANGLE_TOL_DEG = 9        # Допуск угла для подравнивания (градусы от 0/90)

# --- Стены: во сколько раз кластеризовать/закрывать линии относительно
#     "базового" масштаба изображения (1.0 = изображение ~1000x1000 px) ---
WALL_CLOSE_KERNEL_FACTOR = 1.0    # Размер ядра морфологического закрытия стен
WALL_MIN_COMPONENT_RATIO = 0.02   # Доля от площади наибольшего компонента стен,
                                   # ниже которой компонент (иконка/текст/мусор) удаляется

# --- Дверные проёмы ---
DOOR_GAP_MAX_FACTOR = 0.045       # Макс. длина закрываемого проёма (доля от min(h, w))
DOOR_GAP_MIN_FACTOR = 0.006       # Мин. толщина ядра для первого прохода закрытия
DOOR_GAP_ASPECT_MAX = 4.5         # Проём не должен быть слишком "квадратным мусором"

# --- Лестницы ---
STAIR_MIN_PARALLEL_LINES = 3      # Минимум параллельных коротких линий в группе
STAIR_LINE_LEN_FACTOR = 0.012     # Мин. длина линии-ступени (доля от min(h, w))
STAIR_CLUSTER_GAP_FACTOR = 0.01   # Допустимый разброс между соседними ступенями

# ================================================================================


# ===== Вспомогательные функции =====
def _odd(v: float, minimum: int = 3) -> int:
    v = max(minimum, int(round(v)))
    return v if v % 2 else v + 1


def _img_scale(h: int, w: int) -> float:
    """Масштаб относительно "эталонного" изображения ~1000x1000px."""
    return max(0.4, math.sqrt((w * h) / 1_000_000.0))


def _debug_save(name: str, mask: np.ndarray) -> None:
    if not DEBUG:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    cv2.imwrite(os.path.join(DEBUG_DIR, name), mask)


def _remove_small_components(mask: np.ndarray, min_area: int, connectivity: int = 8) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[labels == i] = 255
    return out


def _keep_dominant_components(mask: np.ndarray, ratio: float, connectivity: int = 8) -> np.ndarray:
    """Оставляет крупнейший компонент стен + все компоненты, чья площадь не
    ничтожна по сравнению с ним. Отсекает иконки/текст/мусор внутри комнат,
    которые не связаны с общей сетью стен."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity)
    if n <= 1:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    if areas.size == 0:
        return mask
    max_area = float(areas.max())
    out = np.zeros_like(mask)
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= max_area * ratio:
            out[labels == i] = 255
    return out


def polygon_area_pixels(points: Polygon) -> float:
    return abs(cv2.contourArea(np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)))


def _polygon_centroid(p: Polygon) -> Tuple[float, float]:
    cx = sum(pt[0] for pt in p) / len(p)
    cy = sum(pt[1] for pt in p) / len(p)
    return cx, cy


def _polygon_distance(p1: Polygon, p2: Polygon) -> float:
    cx1, cy1 = _polygon_centroid(p1)
    cx2, cy2 = _polygon_centroid(p2)
    return math.hypot(cx1 - cx2, cy1 - cy2)


def _bbox(p: Polygon) -> Tuple[float, float, float, float]:
    xs = [pt[0] for pt in p]
    ys = [pt[1] for pt in p]
    return min(xs), min(ys), max(xs), max(ys)


def _is_background_margin(poly: Polygon, image_shape: Tuple[int, int]) -> bool:
    """Отсекает полосы/рамку фона вокруг здания, которые не являются
    настоящими комнатами (край листа, поля вокруг постройки)."""
    h, w = image_shape
    x1, y1, x2, y2 = _bbox(poly)
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0:
        return True
    span_w, span_h = width / w, height / h
    base = min(h, w)
    if span_w > BACKGROUND_FULL_SPAN_RATIO and span_h > BACKGROUND_FULL_SPAN_RATIO:
        return True
    thin_h = height < base * BACKGROUND_BAND_THIN_FACTOR
    thin_w = width < base * BACKGROUND_BAND_THIN_FACTOR
    if span_w > BACKGROUND_BAND_SPAN_RATIO and thin_h:
        return True
    if span_h > BACKGROUND_BAND_SPAN_RATIO and thin_w:
        return True

    # Остаточная полоса, не дотягивающая до "почти всего листа", но очень
    # вытянутая и почти лишённая выступов/ниш (типично для пустого поля
    # рядом со зданием, а не для настоящей комнаты)
    aspect = max(width, height) / max(1e-6, min(width, height))
    if aspect > BACKGROUND_ASPECT_MAX:
        area = polygon_area_pixels(poly)
        compact = area / max(width * height, 1e-6)
        if compact > BACKGROUND_COMPACTNESS_MIN:
            return True
    return False


def _orthogonal_regularize(poly: Polygon, tol_deg: float = ORTHOGONAL_ANGLE_TOL_DEG) -> Polygon:
    """Подравнивает почти горизонтальные/вертикальные рёбра под точные 0/90°,
    убирая "лестничный" шум по контуру без изменения формы неортогональных
    участков (например, скруглённых/диагональных углов, если такие есть)."""
    if len(poly) < 3:
        return poly
    pts = [list(p) for p in poly]
    n = len(pts)
    for _pass in range(2):
        for i in range(n):
            prev = pts[i - 1]
            cur = pts[i]
            dx, dy = cur[0] - prev[0], cur[1] - prev[1]
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue
            angle = math.degrees(math.atan2(dy, dx)) % 180
            a = angle if angle <= 90 else 180 - angle
            if a <= tol_deg:
                cur[1] = prev[1]
            elif a >= 90 - tol_deg:
                cur[0] = prev[0]
    # Убираем вырожденные (совпадающие/почти совпадающие) последовательные точки
    cleaned: List[Point] = []
    for p in pts:
        if not cleaned or math.hypot(p[0] - cleaned[-1][0], p[1] - cleaned[-1][1]) > 1e-3:
            cleaned.append((float(p[0]), float(p[1])))
    if len(cleaned) >= 2 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1e-3:
        cleaned.pop()
    return cleaned if len(cleaned) >= 3 else poly


def _bbox_iou(p1: Polygon, p2: Polygon) -> float:
    x1, y1, x2, y2 = _bbox(p1)
    x3, y3, x4, y4 = _bbox(p2)
    ix1, iy1 = max(x1, x3), max(y1, y3)
    ix2, iy2 = min(x2, x4), min(y2, y4)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    a1 = max(1e-6, (x2 - x1) * (y2 - y1))
    a2 = max(1e-6, (x4 - x3) * (y4 - y3))
    return inter / min(a1, a2)


def _polygons_touch_or_overlap(p1: Polygon, p2: Polygon, gap_px: float) -> bool:
    """Точная проверка (по растру, в общих координатах bbox) на то, что два
    полигона реально соприкасаются/пересекаются - в отличие от bbox-IoU это
    не даёт ложных срабатываний для не-выпуклых форм (например, коридора
    "крестом", чей bbox перекрывает bbox почти всех комнат)."""
    x1, y1, x2, y2 = _bbox(p1)
    x3, y3, x4, y4 = _bbox(p2)
    ix1, iy1 = min(x1, x3) - gap_px, min(y1, y3) - gap_px
    ix2, iy2 = max(x2, x4) + gap_px, max(y2, y4) + gap_px
    ww, hh = ix2 - ix1, iy2 - iy1
    if ww <= 0 or hh <= 0 or ww * hh > 4_000_000:
        return False
    scale_px = 1.0
    canvas_w, canvas_h = int(ww) + 2, int(hh) + 2
    m1 = np.zeros((canvas_h, canvas_w), np.uint8)
    m2 = np.zeros((canvas_h, canvas_w), np.uint8)
    pts1 = np.asarray([[int(p[0] - ix1), int(p[1] - iy1)] for p in p1], dtype=np.int32)
    pts2 = np.asarray([[int(p[0] - ix1), int(p[1] - iy1)] for p in p2], dtype=np.int32)
    cv2.fillPoly(m1, [pts1], 255)
    cv2.fillPoly(m2, [pts2], 255)
    if gap_px > 0:
        k = _odd(gap_px)
        m1 = cv2.dilate(m1, np.ones((k, k), np.uint8))
    return bool(np.any(cv2.bitwise_and(m1, m2)))


def _should_merge(p1: Polygon, p2: Polygon, max_dist: float) -> bool:
    if _polygon_distance(p1, p2) >= max_dist * 3:
        return False  # заведомо слишком далеко, не тратим время на растровую проверку
    return _polygons_touch_or_overlap(p1, p2, gap_px=max(2.0, max_dist * 0.5))


def _merge_close_polygons(polys: List[Polygon], max_dist: float) -> List[Polygon]:
    """Объединяет полигоны, которые скорее всего являются осколками одной
    комнаты (близкие центры или сильно пересекающиеся bbox). Полигоны разных
    комнат (разделённые стеной, т.е. далеко и без пересечения) не трогает."""
    if len(polys) <= 1:
        return polys

    areas = [polygon_area_pixels(p) for p in polys]
    median_area = float(np.median(areas)) if areas else 0.0
    small_thr = median_area * SMALL_FRAGMENT_AREA_FACTOR

    n = len(polys)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            dist_thr = max_dist
            # Мелкие осколки склеиваем охотнее (типичный случай: узкая щель
            # между "почти замкнутой" комнатой и оторванным клочком стены)
            if areas[i] < small_thr or areas[j] < small_thr:
                dist_thr = max_dist * 2.0
            if _should_merge(polys[i], polys[j], dist_thr):
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged: List[Polygon] = []
    for idxs in groups.values():
        if len(idxs) == 1:
            merged.append(polys[idxs[0]])
            continue
        all_pts: List[Point] = []
        for k in idxs:
            all_pts.extend(polys[k])
        hull = cv2.convexHull(np.asarray(all_pts, dtype=np.float32))
        merged.append([(float(p[0][0]), float(p[0][1])) for p in hull])
    return merged


# ===== Удаление цветных маркеров (пути эвакуации, пиктограммы) =====
def _remove_colored_markers(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Зелёный (маршрут эвакуации, значок "лестница/выход")
    green_mask = cv2.inRange(hsv, np.array([35, 60, 40]), np.array([85, 255, 255]))
    # Красный (огнетушитель, пожар) - две дуги оттенков
    red_mask = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([0, 90, 60]), np.array([10, 255, 255])),
        cv2.inRange(hsv, np.array([170, 90, 60]), np.array([180, 255, 255])),
    )
    # Насыщенный оранжевый (значок пожара/аптечки), НЕ трогаем бледную заливку комнат
    orange_mask = cv2.inRange(hsv, np.array([10, 130, 120]), np.array([25, 255, 255]))
    # Белые стрелки на зелёном фоне попадают под green после dilate - не нужен отдельный проход

    markers_mask = cv2.bitwise_or(green_mask, red_mask)
    markers_mask = cv2.bitwise_or(markers_mask, orange_mask)

    kernel = np.ones((3, 3), np.uint8)
    markers_mask = cv2.dilate(markers_mask, kernel, iterations=2)

    image_no_markers = image.copy()
    image_no_markers[markers_mask > 0] = [128, 128, 128]
    _debug_save("0_markers_mask.jpg", markers_mask)
    return image_no_markers


# ===== Выделение "сырых" стен =====
def _extract_walls(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, w = gray.shape
    scale = _img_scale(h, w)

    blur_k = _odd(3 * scale)
    blurred = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)

    block = _odd(31 * scale, 15)
    adapt = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 7
    )

    # Тёмные (низкая яркость, низкая насыщенность) пиксели - типичный цвет стен/контуров
    # "Тёмное" (по каналу V) - работает независимо от оттенка: стены могут
    # быть чёрными, серыми, коричневыми, тёмно-бордовыми и т.д.
    val = hsv[:, :, 2]
    dark_v = (val < 100).astype(np.uint8) * 255

    edges = cv2.Canny(gray, 40, 130)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)

    wall = cv2.bitwise_or(adapt, dark_v)
    wall = cv2.bitwise_or(wall, edges)

    min_area = max(15, int(h * w * 0.000008))
    wall = _remove_small_components(wall, min_area)
    _debug_save("1_wall_raw.jpg", wall)
    return wall


# ===== Сборка стен в цельные толстые линии =====
def _consolidate_walls(wall_mask: np.ndarray, image_shape: Tuple[int, int]) -> np.ndarray:
    h, w = image_shape
    scale = _img_scale(h, w)

    # 1. Сильное морфологическое закрытие, чтобы слить параллельные/сдвоенные
    #    контуры одной и той же стены в одну толстую линию (убирает "осколки")
    close_k = _odd(9 * scale * WALL_CLOSE_KERNEL_FACTOR)
    closed = cv2.morphologyEx(
        wall_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k)),
        iterations=1,
    )

    # 2. Усиливаем протяжённые горизонтальные/вертикальные структуры (сами стены),
    #    что помогает дотянуть слегка прерванные линии без раздувания мелких пятен
    line_len = max(9, int(25 * scale))
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (line_len, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, line_len))
    lines_h = cv2.morphologyEx(wall_mask, cv2.MORPH_CLOSE, hk)
    lines_v = cv2.morphologyEx(wall_mask, cv2.MORPH_CLOSE, vk)
    lines = cv2.bitwise_or(lines_h, lines_v)

    combined = cv2.bitwise_or(closed, lines)

    # 3. Небольшая дилатация, чтобы гарантированно замкнуть контур комнаты
    dil_k = _odd(3 * scale)
    combined = cv2.dilate(combined, np.ones((dil_k, dil_k), np.uint8), iterations=1)

    # 4. Убираем мусор (текст, иконки), не связанный с основной сетью стен
    combined = _keep_dominant_components(combined, WALL_MIN_COMPONENT_RATIO)

    min_area = max(80, int(h * w * 0.00003))
    combined = _remove_small_components(combined, min_area)
    _debug_save("2_wall_consolidated.jpg", combined)
    return combined


# ===== Закрытие дверных проёмов (без заливки крупных проходов) =====
def _close_door_gaps(wall_mask: np.ndarray, image_shape: Tuple[int, int]) -> np.ndarray:
    h, w = image_shape
    s = _img_scale(h, w)
    base = min(h, w)

    max_gap = max(10, int(base * DOOR_GAP_MAX_FACTOR))
    result = wall_mask

    # Два прохода нарастающим ядром: сперва закрываем совсем маленькие
    # разрывы (щели между сегментами одной стены), затем - типичные дверные
    # проёмы. Порог max_gap не даёт "залить" большие проходы/коридоры.
    passes = [
        max(5, int(base * DOOR_GAP_MIN_FACTOR)),
        max(9, int(base * DOOR_GAP_MIN_FACTOR * 2.2)),
        max_gap,
    ]
    for kernel_size in passes:
        kernel_size = _odd(kernel_size, 5)
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        closed = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel, iterations=1)
        added = cv2.subtract(closed, result)
        if np.count_nonzero(added) == 0:
            continue
        n, labels, stats, _ = cv2.connectedComponentsWithStats(added, 8)
        accepted = np.zeros_like(added)
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            long_side = max(ww, hh)
            short_side = max(1, min(ww, hh))
            aspect = long_side / short_side
            if aspect > DOOR_GAP_ASPECT_MAX:
                continue
            if long_side > max_gap:
                continue
            if area > max_gap * max_gap * 1.5:
                continue
            accepted[labels == i] = 255
        result = cv2.bitwise_or(result, accepted)

    _debug_save("3_doors_closed.jpg", result)
    return result


# ===== Детектирование лестниц (группы параллельных коротких линий) =====
def _detect_stairs(image: np.ndarray) -> List[Polygon]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    scale = _img_scale(h, w)
    base = min(h, w)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)
    _, dark = cv2.threshold(gray_eq, 110, 255, cv2.THRESH_BINARY_INV)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    edges = cv2.Canny(dark, 50, 150)

    min_len = max(10, int(base * STAIR_LINE_LEN_FACTOR))
    max_gap = max(3, int(6 * scale))
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=max(8, int(base * STAIR_LINE_LEN_FACTOR)),
        minLineLength=min_len, maxLineGap=max_gap,
    )
    if lines is None:
        return []

    horizontal, vertical = [], []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = line
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < min_len:
            continue
        angle = abs(math.degrees(math.atan2(dy, dx))) % 180
        if angle <= 15 or angle >= 165:
            horizontal.append((x1, y1, x2, y2, length))
        elif 75 <= angle <= 105:
            vertical.append((x1, y1, x2, y2, length))

    candidates: List[Polygon] = []
    cluster_gap = max(5, int(base * STAIR_CLUSTER_GAP_FACTOR))

    def cluster_by(lines_list, key_fn, gap):
        clusters = []
        for line in sorted(lines_list, key=key_fn):
            k = key_fn(line)
            placed = False
            for cl in clusters:
                if abs(k - cl["mean"]) <= gap:
                    cl["lines"].append(line)
                    cl["mean"] = float(np.mean([key_fn(l) for l in cl["lines"]]))
                    placed = True
                    break
            if not placed:
                clusters.append({"mean": k, "lines": [line]})
        return clusters

    # Горизонтальные "ступени"
    if len(horizontal) >= STAIR_MIN_PARALLEL_LINES:
        clusters = cluster_by(horizontal, lambda l: (l[1] + l[3]) / 2, cluster_gap)
        for cl in clusters:
            if len(cl["lines"]) >= STAIR_MIN_PARALLEL_LINES:
                ys = sorted([(l[1] + l[3]) / 2 for l in cl["lines"]])
                diffs = np.diff(ys)
                if len(diffs) >= 2 and np.std(diffs) < np.mean(diffs) * 0.35 + 1e-6:
                    xs, ys_all = [], []
                    for l in cl["lines"]:
                        xs.extend([l[0], l[2]])
                        ys_all.extend([l[1], l[3]])
                    pad = max(3, int(5 * scale))
                    candidates.append([
                        (min(xs) - pad, min(ys_all) - pad),
                        (max(xs) + pad, min(ys_all) - pad),
                        (max(xs) + pad, max(ys_all) + pad),
                        (min(xs) - pad, max(ys_all) + pad),
                    ])

    # Вертикальные "ступени"
    if len(vertical) >= STAIR_MIN_PARALLEL_LINES:
        clusters = cluster_by(vertical, lambda l: (l[0] + l[2]) / 2, cluster_gap)
        for cl in clusters:
            if len(cl["lines"]) >= STAIR_MIN_PARALLEL_LINES:
                xs = sorted([(l[0] + l[2]) / 2 for l in cl["lines"]])
                diffs = np.diff(xs)
                if len(diffs) >= 2 and np.std(diffs) < np.mean(diffs) * 0.35 + 1e-6:
                    ys, xs_all = [], []
                    for l in cl["lines"]:
                        ys.extend([l[1], l[3]])
                        xs_all.extend([l[0], l[2]])
                    pad = max(3, int(5 * scale))
                    candidates.append([
                        (min(xs_all) - pad, min(ys) - pad),
                        (max(xs_all) + pad, min(ys) - pad),
                        (max(xs_all) + pad, max(ys) + pad),
                        (min(xs_all) - pad, max(ys) + pad),
                    ])

    unique: List[Polygon] = []
    for cand in candidates:
        if not any(_polygons_overlap(cand, u, threshold=0.5) for u in unique):
            unique.append(cand)
    return unique


def _polygons_overlap(p1: Polygon, p2: Polygon, threshold: float = 0.5) -> bool:
    x1, y1, x2, y2 = _bbox(p1)
    x3, y3, x4, y4 = _bbox(p2)
    ix1, iy1 = max(x1, x3), max(y1, y3)
    ix2, iy2 = min(x2, x4), min(y2, y4)
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    inter_area = (ix2 - ix1) * (iy2 - iy1)
    a1 = (x2 - x1) * (y2 - y1)
    a2 = (x4 - x3) * (y4 - y3)
    if a1 <= 0 or a2 <= 0:
        return False
    return inter_area / min(a1, a2) > threshold


# ===== Извлечение комнат из финальной маски стен =====
def _extract_rooms(wall_mask: np.ndarray, min_area_px: int) -> List[Polygon]:
    h, w = wall_mask.shape
    pad = max(8, int(min(h, w) * 0.01))
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
    canvas[pad:pad + h, pad:pad + w] = wall_mask
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1] - 1, canvas.shape[0] - 1), 255, pad)

    free = cv2.bitwise_not(canvas)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, 8)

    rooms: List[Polygon] = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_area_px:
            continue
        x, y, ww, hh = stats[i, :4]
        # Отбрасываем компонент, если он касается внешней рамки (значит, "утёк" наружу)
        if x <= 0 or y <= 0 or x + ww >= canvas.shape[1] - 1 or y + hh >= canvas.shape[0] - 1:
            continue

        component = (labels == i).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        cnt = max(contours, key=cv2.contourArea)
        if cv2.contourArea(cnt) < min_area_px:
            continue

        peri = cv2.arcLength(cnt, True)
        eps = max(1.5, peri * POLY_SIMPLIFY_EPS_FACTOR)
        approx = cv2.approxPolyDP(cnt, eps, True)
        pts = [(float(p[0][0] - pad), float(p[0][1] - pad)) for p in approx]
        if ORTHOGONAL_SNAP:
            pts = _orthogonal_regularize(pts)
        if len(pts) >= 3:
            rooms.append(pts)
    return rooms


# ===== Классификация типа помещения =====
def _classify_room(poly: Polygon, all_polys: List[Polygon], stair_polys: List[Polygon]) -> Tuple[str, float]:
    area = polygon_area_pixels(poly)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 0 or height <= 0:
        return "", 0.0
    aspect = max(width, height) / max(1e-6, min(width, height))
    rect_area = width * height
    compact = area / max(rect_area, 1e-6)

    cx, cy = _polygon_centroid(poly)
    for stair in stair_polys:
        cnt_stair = np.asarray(stair, dtype=np.float32).reshape(-1, 1, 2)
        if cv2.pointPolygonTest(cnt_stair, (cx, cy), False) >= 0:
            return "лестница", 1.0

    if aspect >= 3.0 or (aspect >= 2.2 and compact < 0.72):
        return "коридор", 0.78
    if compact < 0.55 and area > np.median([polygon_area_pixels(p) for p in all_polys]):
        return "коридор", 0.62
    median = np.median([polygon_area_pixels(p) for p in all_polys]) if all_polys else area
    if area < median * 0.25:
        return "санузел", 0.55
    if area > median * 2.5:
        return "зал", 0.60
    return "кабинет", 0.45


# ===== ОСНОВНЫЕ ПУБЛИЧНЫЕ ФУНКЦИИ =====
def load_image(filepath: str) -> np.ndarray:
    with open(filepath, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Не удалось загрузить изображение {filepath}")
    return image


def calibrate_scale(calib_line):
    (x1, y1), (x2, y2), length_m = calib_line
    pixel_len = math.hypot(x2 - x1, y2 - y1)
    return 0.0 if pixel_len == 0 else float(length_m) / pixel_len


def detect_floor_plan(image: np.ndarray, min_area_px: Optional[int] = None) -> Dict[str, Any]:
    if image is None or image.ndim != 3:
        raise ValueError("Ожидалось цветное изображение")

    h, w = image.shape[:2]
    if DEBUG:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(DEBUG_DIR, "0_original.jpg"), image)

    # 1. Удаляем цветные маркеры (пути эвакуации, пиктограммы)
    image_clean = _remove_colored_markers(image)

    # 2. Сырая маска стен
    wall_raw = _extract_walls(image_clean)

    # 3. Сборка в цельные толстые линии + удаление мусора (иконки/текст)
    wall_consolidated = _consolidate_walls(wall_raw, (h, w))

    # 4. Закрытие дверных проёмов (без заливки крупных проходов)
    wall_final = _close_door_gaps(wall_consolidated, (h, w))

    min_area_final = max(60, int(h * w * 0.00002))
    wall_final = _remove_small_components(wall_final, min_area_final)
    _debug_save("4_wall_final.jpg", wall_final)

    # 5. Лестницы
    stair_polys = _detect_stairs(image_clean)
    if DEBUG:
        vis_stairs = image.copy()
        for poly in stair_polys:
            pts = np.asarray(poly, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis_stairs, [pts], True, (255, 0, 0), 2)
        cv2.imwrite(os.path.join(DEBUG_DIR, "5_stairs.jpg"), vis_stairs)

    # 6. Комнаты (заливка свободного пространства между стенами)
    if min_area_px is None:
        min_area_px = max(500, int(h * w * 0.0003))
    room_polys = _extract_rooms(wall_final, min_area_px)
    room_polys = [p for p in room_polys if not _is_background_margin(p, (h, w))]
    if DEBUG:
        vis_rooms_raw = image.copy()
        for poly in room_polys:
            pts = np.asarray(poly, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis_rooms_raw, [pts], True, (0, 255, 255), 2)
        cv2.imwrite(os.path.join(DEBUG_DIR, "6_rooms_raw.jpg"), vis_rooms_raw)

    # 7. Склейка соседних осколков одной комнаты (без слияния разных комнат)
    if MERGE_CLOSE_ROOMS:
        merge_dist = max(10, int(min(h, w) * MERGE_DISTANCE_FACTOR))
        room_polys = _merge_close_polygons(room_polys, merge_dist)
    if DEBUG:
        vis_rooms_merged = image.copy()
        for poly in room_polys:
            pts = np.asarray(poly, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis_rooms_merged, [pts], True, (0, 0, 255), 2)
        cv2.imwrite(os.path.join(DEBUG_DIR, "7_rooms_merged.jpg"), vis_rooms_merged)

    # 8. Классификация и присвоение ID
    typed_rooms = []
    for idx, poly in enumerate(room_polys, start=1):
        rt, conf = _classify_room(poly, room_polys, stair_polys)
        typed_rooms.append({
            "id": idx,
            "points": poly,
            "room_type": rt,
            "confidence": conf,
            "area_px": polygon_area_pixels(poly),
        })

    return {
        "rooms": typed_rooms,
        "walls_mask": wall_final,
        "stair_regions": stair_polys,
        "diagnostics": {
            "image_size": (w, h),
            "wall_pixels": int(np.count_nonzero(wall_final)),
            "room_count": len(typed_rooms),
            "stair_candidates": len(stair_polys),
        },
    }


# ===== Совместимые обёртки (сигнатуры сохранены) =====
def detect_walls(image: np.ndarray) -> List[Polygon]:
    return [x["points"] for x in detect_floor_plan(image)["rooms"]]


def detect_room_candidates(image: np.ndarray) -> List[Dict[str, Any]]:
    return detect_floor_plan(image)["rooms"]


def draw_walls(image: np.ndarray, contours: List[Polygon], color=(0, 0, 255), thickness=2) -> np.ndarray:
    vis = image.copy()
    for cnt in contours:
        pts = np.asarray(cnt, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(vis, [pts], True, color, thickness)
    return vis


# ===== Тест =====
if __name__ == "__main__":
    img = cv2.imread("plan.jpg")
    if img is not None:
        result = detect_floor_plan(img)
        rooms = result["rooms"]
        print(f"Найдено комнат: {len(rooms)}")
        vis = draw_walls(img, [r["points"] for r in rooms])
        cv2.imwrite("result.jpg", vis)
    else:
        print("Файл plan.jpg не найден")