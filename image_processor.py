"""
image_processor — автоматическая растровая разметка помещений по плану
пожарной/эвакуационной схемы.

Публичный интерфейс, совместимый с app.py:
    img = load_image(path)
    result = detect_floor_plan(img)

result:
    {
        "rooms": [
            {"points": [(x, y), ...], "room_type": ""}
        ]
    }

Главная идея версии 2:
1. Из изображения извлекаются только тёмные малонасыщенные линии стен.
2. Цветные пути эвакуации, пиктограммы и текст не считаются стенами.
3. Разрывы стен восстанавливаются не одним большим closing, а несколькими
   направленными масштабами. Это позволяет одновременно находить:
   - маленькие помещения с узкими дверями;
   - большие помещения;
   - внешние двери/окна, которые иначе соединяют комнату с фоном.
4. Результаты нескольких масштабов объединяются. Крупный кандидат,
   содержащий несколько более стабильных маленьких кандидатов, считается
   "склеенным" помещением и отбрасывается.
5. Контуры очищаются, упрощаются и ортогонализируются.
6. Для отладки можно получить цветную полупрозрачную визуализацию комнат.

Главное отличие: перед поиском комнат строится геометрический каркас стен.
Тонкие дверные дуги, лестничные штрихи и тёмные части пиктограмм не
считаются стенами только потому, что они тёмные.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

Point = Tuple[float, float]


# ============================================================================
# 1. Загрузка
# ============================================================================

def load_image(path: str):
    """Загрузить изображение в BGR, включая Windows-пути с кириллицей."""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Не удалось прочитать изображение: {path}")
    return img


# ============================================================================
# 2. Маска стен
# ============================================================================

def _dark_wall_candidate(
    img: np.ndarray,
    v_thresh: int = 110,
    chroma_thresh: int = 25,
) -> np.ndarray:
    """Первичная маска потенциально чёрных/серых линий."""
    b, g, r = cv2.split(img)
    mx = np.maximum(np.maximum(b, g), r).astype(np.int16)
    mn = np.minimum(np.minimum(b, g), r).astype(np.int16)
    chroma = mx - mn

    mask = ((mx < v_thresh) & (chroma <= chroma_thresh)).astype(np.uint8) * 255
    return cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )


def _wall_mask(
    img: np.ndarray,
    v_thresh: int = 110,
    chroma_thresh: int = 25,
) -> np.ndarray:
    """
    Построить именно КАРКАС СТЕН, а не просто маску всех тёмных объектов.

    Ключевая идея новой версии:
      1. сначала находим все тёмные пиксели;
      2. distance transform оставляет только достаточно толстое ядро стен;
      3. тонкие дуги дверей, лестничные штрихи, текст и контуры значков
         в ядро не попадают;
      4. ядро расширяется обратно до толщины стены.

    Это принципиально важно для планов эвакуации: зелёные маршруты,
    огнетушители и прочие цветные элементы уже отбрасываются chroma-фильтром,
    а чёрные дуги дверей/штриховка лестниц отбрасываются по толщине.
    """
    dark = _dark_wall_candidate(
        img,
        v_thresh=v_thresh,
        chroma_thresh=chroma_thresh,
    )

    dist = cv2.distanceTransform(dark, cv2.DIST_L2, 5)
    values = dist[dark > 0]

    if len(values) == 0:
        return dark

    h, w = dark.shape[:2]
    raw_thickness = 2.0 * float(np.percentile(values, 90))
    thickness = float(np.clip(raw_thickness, 2.0, math.hypot(h, w) * 0.03))

    # Ядро должно быть достаточно толстым, чтобы выбросить тонкие
    # графические элементы, но не настолько, чтобы исчезли реальные стены.
    core_radius = max(1.8, thickness * 0.22)
    core = (dist >= core_radius).astype(np.uint8) * 255

    # Возвращаем ядру примерно исходную толщину стены.
    expand = max(1, int(round(thickness * 0.50)))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (2 * expand + 1, 2 * expand + 1),
    )
    wall = cv2.dilate(core, kernel)

    # Убираем совсем мелкие изолированные остатки, которые иногда
    # появляются на JPEG-шумах.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(wall, 8)
    clean = np.zeros_like(wall)
    min_component = max(12, int(h * w * 0.00002))
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area >= min_component or max(bw, bh) >= max(12, int(thickness * 3)):
            clean[labels == i] = 255

    return clean


def _remove_small_blobs(mask: np.ndarray, *args, **kwargs) -> np.ndarray:
    """Совместимый API: после нового wall-mask дополнительная чистка не нужна."""
    return mask


# ============================================================================
# 3. Оценка толщины
# ============================================================================

def _estimate_thickness(mask: np.ndarray) -> float:
    """Оценить характерную толщину основной сети стен."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return 3.0

    biggest = max(
        range(1, n),
        key=lambda i: int(stats[i, cv2.CC_STAT_AREA]),
    )
    main = np.uint8(labels == biggest) * 255

    dist = cv2.distanceTransform(main, cv2.DIST_L2, 5)
    vals = dist[main > 0]
    vals = vals[vals > 0]

    if len(vals) == 0:
        return 3.0

    h, w = mask.shape[:2]
    diag = math.hypot(h, w)

    # 90-й процентиль устойчивее медианы для планов, где есть
    # много тонких пиктограмм/штрихов.
    thickness = 2.0 * float(np.percentile(vals, 90))
    return float(np.clip(thickness, 2.0, diag * 0.03))


# ============================================================================
# 4. Восстановление разрывов стен
# ============================================================================

def _directional_gap_repair(
    mask: np.ndarray,
    radius: int,
) -> np.ndarray:
    """
    Закрывает небольшие разрывы вдоль горизонтальных и вертикальных стен.

    Это принципиально отличается от большого квадратного closing:
    горизонтальный kernel работает только вдоль строк, вертикальный —
    только вдоль столбцов. Поэтому мы можем восстановить дверь/окно,
    не превращая близкие перпендикулярные стены в одну толстую массу.
    """
    radius = max(1, int(radius))

    horizontal = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (2 * radius + 1, 1),
    )
    vertical = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, 2 * radius + 1),
    )

    out = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, horizontal)
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, vertical)

    return out


def _repair_scales(thickness: float) -> List[int]:
    """
    Несколько масштабов восстановления.

    Один масштаб принципиально недостаточен:
      маленький -> не закрывает наружную дверь;
      большой -> склеивает несколько маленьких комнат.

    Поэтому комнаты ищутся на нескольких масштабах, а потом кандидаты
    собираются в единый набор.
    """
    factors = (1.67, 2.0, 2.33, 2.67, 3.0, 3.33, 3.67, 4.0, 4.33)
    values = {max(2, int(round(thickness * f))) for f in factors}
    return sorted(values)


# ============================================================================
# 4.5. Закрытие дверных проёмов и выбор масштаба
# ============================================================================

def _close_wall_gaps(wall_mask: np.ndarray, radius: int) -> np.ndarray:
    """
    Закрывает только линейные разрывы стен.

    В отличие от квадратного closing, здесь отдельно закрываются
    горизонтальные и вертикальные разрывы. Это позволяет закрыть дверь,
    не превращая соседний широкий проход в стену.
    """
    radius = max(1, int(radius))
    out = wall_mask.copy()

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * radius + 1, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2 * radius + 1))

    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, hk)
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, vk)
    return out


def _room_components(
    closed_wall_mask: np.ndarray,
    min_area: float,
) -> List[Dict[str, Any]]:
    """Получить реальные внутренние области после закрытия дверей."""
    free = cv2.bitwise_not(closed_wall_mask)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, 8)

    border_labels = (
        set(labels[0, :].tolist())
        | set(labels[-1, :].tolist())
        | set(labels[:, 0].tolist())
        | set(labels[:, -1].tolist())
    )
    border_labels.discard(0)

    result: List[Dict[str, Any]] = []
    for i in range(1, n):
        if i in border_labels:
            continue

        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue

        mask = np.uint8(labels == i) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        cnt = max(contours, key=cv2.contourArea)
        contour_area = float(cv2.contourArea(cnt))
        if contour_area < min_area * 0.55:
            continue

        x, y, w, h = cv2.boundingRect(cnt)
        bbox_area = max(1, w * h)
        rectangularity = contour_area / bbox_area

        result.append({
            "mask": mask,
            "contour": cnt,
            "pixel_area": area,
            "area": contour_area,
            "bbox": (x, y, w, h),
            "rectangularity": rectangularity,
        })

    return result


def _scale_quality(
    candidates: List[Dict[str, Any]],
    image_area: int,
) -> float:
    """
    Оценка одного масштаба.

    Нам нужен не максимальный number of blobs, а масштаб, где:
      - есть достаточно крупных помещений;
      - мало микрорегионов;
      - нет одного огромного помещения, означающего незакрытые двери.
    """
    if not candidates:
        return -1e9

    areas = sorted((c["area"] for c in candidates), reverse=True)
    significant = [a for a in areas if a >= image_area * 0.01]
    medium = [a for a in areas if a >= image_area * 0.005]

    if not significant:
        return -1e9

    count = len(significant)
    # Для обычного плана количество комнат обычно лежит далеко ниже
    # нескольких десятков. После 20 качество масштаба резко падает:
    # это почти всегда разбиение коридоров/значков на мелкие области.
    count_score = 3.0 * min(count, 16) - 0.55 * max(0, count - 16) ** 2

    # Слишком маленькие области — шум.
    tiny_penalty = 0.25 * max(0, len(medium) - count)

    # Если одна область занимает почти весь внутренний контур, двери ещё
    # не закрылись. Небольшой бонус за более равномерное разбиение.
    largest_ratio = areas[0] / max(1.0, sum(areas))
    huge_penalty = 7.0 * max(0.0, largest_ratio - 0.72)

    return count_score - tiny_penalty - huge_penalty


def _select_single_scale(
    wall_mask: np.ndarray,
    thickness: float,
    image_area: int,
) -> Tuple[int, List[Dict[str, Any]], List[Tuple[int, float, int]]]:
    """Выбрать один наиболее правдоподобный размер закрытия дверей."""
    factors = (1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0, 4.25)
    evaluations: List[Tuple[int, float, List[Dict[str, Any]]]] = []

    min_area = max(350.0, image_area * 0.0045)

    for factor in factors:
        radius = max(2, int(round(thickness * factor)))
        closed = _close_wall_gaps(wall_mask, radius)
        candidates = _room_components(closed, min_area=min_area)
        quality = _scale_quality(candidates, image_area)
        evaluations.append((radius, quality, candidates))

    # При одинаковом качестве предпочитаем более крупный радиус: он обычно
    # закрывает дополнительные двери, но не влияет на уже закрытые стены.
    evaluations.sort(key=lambda x: (x[1], x[0]), reverse=True)
    best_radius, _, best_candidates = evaluations[0]

    debug = [
        (r, round(q, 3), len(c))
        for r, q, c in evaluations
    ]
    return best_radius, best_candidates, debug


# ============================================================================
# 5. Извлечение кандидатов комнат на одном масштабе
# ============================================================================

def _extract_candidates_at_scale(
    wall_mask: np.ndarray,
    radius: int,
    min_area: float,
    min_width: float,
) -> List[Dict[str, Any]]:
    """
    Получить кандидатов комнат на одном масштабе.

    Внешняя область отбрасывается по касанию границы кадра.
    Именно поэтому большие наружные проёмы должны быть закрыты на
    одном из последующих масштабов.
    """
    closed = _directional_gap_repair(wall_mask, radius)
    free = cv2.bitwise_not(closed)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, 8)

    border_labels = (
        set(labels[0, :].tolist())
        | set(labels[-1, :].tolist())
        | set(labels[:, 0].tolist())
        | set(labels[:, -1].tolist())
    )
    border_labels.discard(0)

    candidates: List[Dict[str, Any]] = []

    for i in range(1, n):
        if i in border_labels:
            continue

        pixel_area = float(stats[i, cv2.CC_STAT_AREA])
        if pixel_area < min_area:
            continue

        room_mask = np.uint8(labels == i) * 255

        # Отбрасываем очень узкие полосы/остатки от линий.
        if min_width > 0:
            half_w = max(1, int(round(min_width / 2.0)))
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (2 * half_w + 1, 2 * half_w + 1),
            )
            if not cv2.erode(room_mask, kernel).any():
                continue

        contours, _ = cv2.findContours(
            room_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            continue

        cnt = max(contours, key=cv2.contourArea)
        contour_area = float(cv2.contourArea(cnt))
        if contour_area < min_area * 0.55:
            continue

        x, y, w, h = cv2.boundingRect(cnt)
        bbox_area = max(1, w * h)
        rectangularity = contour_area / bbox_area

        candidates.append(
            {
                "mask": room_mask,
                "contour": cnt,
                "pixel_area": pixel_area,
                "area": contour_area,
                "bbox": (x, y, w, h),
                "rectangularity": rectangularity,
                "radius": radius,
            }
        )

    return candidates


# ============================================================================
# 6. Сборка нескольких масштабов
# ============================================================================

def _bbox_intersection(a: Tuple[int, int, int, int],
                      b: Tuple[int, int, int, int]) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b

    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def _mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    aa = a > 0
    bb = b > 0
    inter = int(np.count_nonzero(aa & bb))
    union = int(np.count_nonzero(aa | bb))
    return inter / max(1, union)


def _candidate_stability(
    candidate: Dict[str, Any],
    all_candidates: List[Dict[str, Any]],
) -> int:
    """Сколько масштабов дают практически такой же контур."""
    x, y, w, h = candidate["bbox"]
    count = 0

    for other in all_candidates:
        ox, oy, ow, oh = other["bbox"]
        if (
            abs(x - ox) <= 3
            and abs(y - oy) <= 3
            and abs(w - ow) <= 4
            and abs(h - oh) <= 4
        ):
            count += 1

    return count


def _is_composite_candidate(
    candidate: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> bool:
    """
    Определяет крупную "склеенную" комнату.

    Если крупный кандидат содержит несколько отдельных кандидатов,
    найденных на других масштабах, то крупный кандидат не должен
    заменять их одним помещением.
    """
    x, y, w, h = candidate["bbox"]
    bbox_area = max(1, w * h)

    parts: List[Dict[str, Any]] = []

    for other in candidates:
        if other is candidate:
            continue

        # Не рассматриваем равные/более крупные варианты как части.
        if other["area"] >= candidate["area"] * 0.98:
            continue

        inter = _bbox_intersection(candidate["bbox"], other["bbox"])
        if inter / bbox_area > 0.10:
            parts.append(other)

    parts.sort(
        key=lambda c: _bbox_intersection(candidate["bbox"], c["bbox"]),
        reverse=True,
    )

    chosen: List[Dict[str, Any]] = []
    covered = 0

    for part in parts:
        # Не считать один и тот же фрагмент дважды.
        if any(
            _bbox_intersection(part["bbox"], old["bbox"])
            / max(1, part["bbox"][2] * part["bbox"][3])
            > 0.50
            for old in chosen
        ):
            continue

        chosen.append(part)
        covered += _bbox_intersection(candidate["bbox"], part["bbox"])

    # Два и более стабильных фрагмента, покрывающих большую часть
    # крупного bbox, означают, что кандидат является склейкой.
    return len(chosen) >= 2 and covered / bbox_area > 0.55


def _merge_multiscale_candidates(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Объединить результаты всех масштабов.

    Сначала убираются почти одинаковые кандидаты. Затем отбрасываются
    крупные кандидаты, которые состоят из нескольких более мелких.
    """
    if not candidates:
        return []

    for c in candidates:
        c["stability"] = _candidate_stability(c, candidates)
        c["score"] = (
            math.log1p(max(1.0, c["area"]))
            + 0.25 * c["stability"]
            + 0.5 * c["rectangularity"]
        )

    # Стабильные/качественные варианты идут первыми.
    ordered = sorted(candidates, key=lambda c: c["score"], reverse=True)

    unique: List[Dict[str, Any]] = []
    for c in ordered:
        if any(_mask_iou(c["mask"], u["mask"]) > 0.90 for u in unique):
            continue
        unique.append(c)

    # Сначала убираем составные кандидаты. Делать это по исходному
    # unique важно: иначе крупная склейка могла бы скрыть её составляющие.
    result = [
        c for c in unique
        if not _is_composite_candidate(c, unique)
    ]

    # Повторная дедупликация после удаления склеек.
    final: List[Dict[str, Any]] = []
    for c in sorted(result, key=lambda z: z["score"], reverse=True):
        if any(_mask_iou(c["mask"], u["mask"]) > 0.90 for u in final):
            continue
        final.append(c)

    return final


# ============================================================================
# 7. Контур -> чистый полигон
# ============================================================================

def _contour_to_points(
    cnt: np.ndarray,
    simplify_frac: float = 0.006,
) -> List[Point]:
    peri = cv2.arcLength(cnt, True)
    eps = max(1.5, simplify_frac * peri)
    approx = cv2.approxPolyDP(cnt, eps, True)

    return [
        (float(p[0][0]), float(p[0][1]))
        for p in approx
    ]


def _orthogonalize(
    points: List[Point],
    angle_tol_deg: float = 8.0,
) -> List[Point]:
    """
    Горизонтальные/вертикальные рёбра принудительно выравниваются.

    Диагональные стены не трогаются.
    """
    if len(points) < 3:
        return points

    out = list(points)
    n = len(out)

    for i in range(n):
        x1, y1 = out[i]
        x2, y2 = out[(i + 1) % n]

        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)

        if length < 1e-6:
            continue

        angle = math.degrees(math.atan2(dy, dx)) % 180

        if angle < angle_tol_deg or angle > 180 - angle_tol_deg:
            ny = (y1 + y2) / 2.0
            out[i] = (x1, ny)
            out[(i + 1) % n] = (x2, ny)

        elif abs(angle - 90) < angle_tol_deg:
            nx = (x1 + x2) / 2.0
            out[i] = (nx, y1)
            out[(i + 1) % n] = (nx, y2)

    return out


def _merge_close_points(
    points: List[Point],
    tol: float,
) -> List[Point]:
    if not points:
        return points

    out: List[Point] = []

    for p in points:
        if (
            out
            and math.hypot(
                p[0] - out[-1][0],
                p[1] - out[-1][1],
            ) < tol
        ):
            continue
        out.append(p)

    if (
        len(out) > 1
        and math.hypot(
            out[0][0] - out[-1][0],
            out[0][1] - out[-1][1],
        ) < tol
    ):
        out.pop()

    return out


def _clean_room_polygon(
    room_mask: np.ndarray,
    thickness: float,
) -> Optional[List[Point]]:
    contours, _ = cv2.findContours(
        room_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None

    cnt = max(contours, key=cv2.contourArea)

    if cv2.contourArea(cnt) <= 1.0:
        return None

    points = _contour_to_points(cnt)

    if len(points) < 3:
        return None

    for _ in range(2):
        points = _orthogonalize(points)
        points = _merge_close_points(
            points,
            tol=max(2.0, thickness * 0.45),
        )

    if len(points) < 3:
        return None

    return [
        (round(x, 1), round(y, 1))
        for x, y in points
    ]


# ============================================================================
# 8. Консервативное определение лестницы
# ============================================================================

def _staircase_score(
    img: np.ndarray,
    polygon: Sequence[Point],
) -> float:
    """
    Оценивает наличие лестничной графики внутри полигона.

    Метод намеренно консервативный: много повторяющихся коротких
    горизонтальных/вертикальных тёмных штрихов повышает score.
    Если score низкий, тип помещения не меняется.
    """
    if len(polygon) < 3:
        return 0.0

    mask = np.zeros(img.shape[:2], np.uint8)
    pts = np.array(polygon, dtype=np.int32)
    cv2.fillPoly(mask, [pts], 255)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 100)
    dark = cv2.bitwise_and(dark, mask)

    area = float(np.count_nonzero(mask))
    if area < 500:
        return 0.0

    # Считаем короткие повторяющиеся горизонтальные/вертикальные
    # сегменты через HoughLinesP.
    edges = cv2.Canny(dark, 50, 140)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(8, int(min(mask.shape) * 0.02)),
        minLineLength=5,
        maxLineGap=2,
    )

    if lines is None:
        return 0.0

    short_parallel = 0

    for line in lines[:, 0]:
        x1, y1, x2, y2 = map(int, line)
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        length = math.hypot(dx, dy)

        if 5 <= length <= 45 and (dx <= 2 or dy <= 2):
            short_parallel += 1

    density = short_parallel / max(1.0, area / 1000.0)
    return float(density)


def _assign_room_type(
    img: np.ndarray,
    polygon: Sequence[Point],
) -> str:
    # Порог намеренно высокий: лучше оставить обычную комнату,
    # чем ошибочно назвать помещение лестницей.
    return "лестница" if _staircase_score(img, polygon) >= 20.0 else ""


# ============================================================================
# 9. Цветная визуализация
# ============================================================================

_DEFAULT_ROOM_COLORS = [
    (235, 185, 150),
    (155, 220, 160),
    (180, 160, 235),
    (225, 215, 145),
    (235, 165, 185),
    (150, 215, 225),
    (205, 165, 225),
    (180, 210, 145),
    (235, 190, 135),
    (165, 190, 235),
    (215, 170, 145),
    (170, 220, 200),
]


def render_room_overlay(
    img: np.ndarray,
    result: Dict[str, Any],
    alpha: float = 0.34,
) -> np.ndarray:
    """
    Полупрозрачно закрасить найденные помещения.

    В отличие от старого debug_visualize здесь:
      - используются стабильные пастельные цвета;
      - стены/пути эвакуации остаются видимыми;
      - контуры комнат не заменяют исходную графику;
      - цвета повторяются циклически, если комнат больше палитры.
    """
    if img is None:
        raise ValueError("render_room_overlay: пустое изображение")

    alpha = float(np.clip(alpha, 0.0, 1.0))
    overlay = img.copy()

    for i, room in enumerate(result.get("rooms", [])):
        points = room.get("points") or []
        if len(points) < 3:
            continue

        pts = np.array(points, dtype=np.int32)
        color = _DEFAULT_ROOM_COLORS[i % len(_DEFAULT_ROOM_COLORS)]

        cv2.fillPoly(overlay, [pts], color)

    return cv2.addWeighted(overlay, alpha, img, 1.0 - alpha, 0)


def _debug_visualize(
    img: np.ndarray,
    result: Dict[str, Any],
) -> np.ndarray:
    """Совместимый старый debug-вызов, теперь с нормальной заливкой."""
    return render_room_overlay(img, result, alpha=0.42)


# ============================================================================
# 10. Главная функция
# ============================================================================

def detect_floor_plan(
    img: np.ndarray,
    v_thresh: int = 110,
    chroma_thresh: int = 25,
    door_gap_factor: float = 4.33,
    min_room_side: float = 5.0,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Растровый план -> список комнат.

    Параметры оставлены совместимыми с предыдущей версией.
    door_gap_factor теперь задаёт верхний масштаб multiscale-поиска,
    а не единственный размер морфологического closing.

    Возвращает:
        {"rooms": [{"points": [...], "room_type": ""}, ...]}

    Координаты остаются в пикселях исходного изображения.
    """
    if img is None:
        raise ValueError("detect_floor_plan: пустое изображение")

    h, w = img.shape[:2]

    # 1) Из изображения извлекаем именно толстый геометрический каркас стен.
    #    Цветные маршруты/иконки здесь уже не участвуют, а тонкие чёрные
    #    дуги дверей и штриховка лестниц отбрасываются по толщине.
    dark_candidate = _dark_wall_candidate(
        img,
        v_thresh=v_thresh,
        chroma_thresh=chroma_thresh,
    )
    wall_mask = _wall_mask(
        img,
        v_thresh=v_thresh,
        chroma_thresh=chroma_thresh,
    )

    # Толщину оцениваем по исходным тёмным стенам, а не по уже
    # расширенному каркасу: иначе dilation искусственно увеличит толщину
    # и все последующие размеры дверных проёмов станут слишком большими.
    thickness = _estimate_thickness(dark_candidate)
    image_area = h * w

    # 2) Сначала пробуем найти ОДИН лучший масштаб закрытия дверей.
    #    Для планов, подобных пользовательскому примеру, это существенно
    #    надёжнее, чем смешивать комнаты, найденные на разных масштабах.
    best_radius, single_candidates, scale_debug = _select_single_scale(
        wall_mask,
        thickness,
        image_area,
    )

    significant_count = sum(
        c["area"] >= image_area * 0.01
        for c in single_candidates
    )

    # 3) Если один масштаб дал слишком мало помещений, используем старый
    #    multiscale fallback. Это сохраняет хорошее поведение на небольших
    #    планах, где несколько маленьких комнат имеют очень узкие двери.
    if significant_count >= 8:
        selected = single_candidates
        used_mode = "single_scale"
    else:
        scales = _repair_scales(thickness)
        max_radius = max(
            2,
            int(round(thickness * float(door_gap_factor))),
        )
        scales = [r for r in scales if r <= max_radius]
        if max_radius not in scales:
            scales.append(max_radius)
        scales = sorted(set(scales))

        all_candidates: List[Dict[str, Any]] = []
        min_area = max(350.0, image_area * 0.0035)
        for radius in scales:
            all_candidates.extend(
                _extract_candidates_at_scale(
                    wall_mask,
                    radius=radius,
                    min_area=min_area,
                    min_width=0.0,
                )
            )

        selected = _merge_multiscale_candidates(all_candidates)
        used_mode = "multiscale_fallback"

    # Не допускаем микрорегионы даже после fallback. Маленькие настоящие
    # комнаты сохраняются, если они имеют площадь хотя бы 0.25% кадра.
    min_final_area = max(250.0, image_area * 0.0025)
    selected = [c for c in selected if c["area"] >= min_final_area]

    # Стабильный порядок сверху-вниз, слева-направо.
    selected.sort(
        key=lambda c: (
            c["bbox"][1],
            c["bbox"][0],
        )
    )

    rooms: List[Dict[str, Any]] = []

    for candidate in selected:
        points = _clean_room_polygon(
            candidate["mask"],
            thickness,
        )

        if not points or len(points) < 3:
            continue

        room_type = _assign_room_type(img, points)

        rooms.append(
            {
                "points": points,
                "room_type": room_type,
            }
        )

    result: Dict[str, Any] = {"rooms": rooms}

    if debug:
        result["debug"] = {
            "wall_mask": wall_mask,
            "thickness": thickness,
            "selected_radius": best_radius,
            "mode": used_mode,
            "scale_evaluation": scale_debug,
            "selected_count": len(selected),
        }

    return result


# ============================================================================
# 11. CLI
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Использование: python image_processor.py "
            "<путь_к_плану.jpg> [путь_к_результату.png]"
        )
        raise SystemExit(1)

    in_path = sys.argv[1]
    out_path = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "debug_rooms.png"
    )

    image = load_image(in_path)
    res = detect_floor_plan(image, debug=True)

    print(f"Найдено комнат: {len(res['rooms'])}")
    print(
        f"Оценённая толщина стены: "
        f"{res['debug']['thickness']:.1f}px"
    )
    print(f"Режим: {res['debug']['mode']}")
    print(f"Выбранный радиус закрытия: {res['debug']['selected_radius']} px")
    print(f"Масштабных оценок: {len(res['debug']['scale_evaluation'])}")

    for i, room in enumerate(res["rooms"], 1):
        print(
            f"  Комната {i}: "
            f"{len(room['points'])} вершин; "
            f"type={room['room_type']!r}"
        )

    vis = _debug_visualize(image, res)
    ok = cv2.imwrite(out_path, vis)

    if not ok:
        raise RuntimeError(
            f"Не удалось сохранить визуализацию: {out_path}"
        )

    print(f"Визуализация сохранена: {out_path}")
