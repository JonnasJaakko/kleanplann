"""
image_processor — превращает растровый план эвакуации (фото/скрин плана
пожарной безопасности) в разметку этажа на комнаты, в духе того, как это
делает room_builder.py для DXF, но на входе — картинка, а не набор
отрезков.

Публичный интерфейс (его ждёт app.py):

    img = load_image(path)                 # -> numpy BGR massiv (cv2)
    result = detect_floor_plan(img)         # -> {"rooms": [...]}

    result["rooms"] — список словарей:
        {"points": [(x, y), ...], "room_type": ""}
    Координаты — в пикселях исходного изображения, то есть их можно
    напрямую использовать как координаты сцены (как уже делает app.py,
    оборачивая точки в Wall/Room).

------------------------------------------------------------------
Идея алгоритма (коротко)
------------------------------------------------------------------
Автор задачи сформулировал 4 правила — они и есть каркас алгоритма:

  1. "Имеют значение только стены" — всё остальное мусор.
     -> Стены на таких планах рисуют тёмными (чёрными/тёмно-серыми)
        линиями, а весь "мусор" (зелёные стрелки эвакуации, красные
        значки огнетушителей/телефонов, жёлтые треугольники,
        текстовые подписи) — цветной. Поэтому сперва строится
        цветовая маска "тёмное и малонасыщенное" (см. _wall_mask),
        а затем из неё дополнительно вычищаются компактные пятна
        (иконки) — они не похожи на стены ни формой, ни размером
        (см. _remove_small_blobs).

  2. "Итог должен быть как на эталонном скриншоте" — то есть чистые
     прямоугольные закрашенные комнаты с небольшим числом углов,
     а не шумная пиксельная лесенка.
     -> Контур каждой найденной комнаты дополнительно (a) упрощается
        (cv2.approxPolyDP), (b) "ортогонализируется" — рёбра, близкие
        к горизонтали/вертикали, принудительно выравниваются, чтобы
        углы были ровно 90° даже если при растеризации/закрытии
        проёмов появилось небольшое отклонение, (c) склеиваются
        точки, лежащие ближе допуска друг к другу.

  3. "Разметка стен происходит поверх стен на изображении".
     -> Стены не восстанавливаются как тонкие осевые линии с
        последующим "раздуванием" (это давало бы смещение), а сразу
        берутся как есть — толстая закрашенная область (маска),
        полученная из пикселей картинки. Граница комнаты — это
        граница между этой маской и свободным пространством, то есть
        она проходит ровно по внутреннему краю нарисованных стен.

  4. "Сначала общий контур этажа, потом комнаты внутри, потом
     склейка близких точек и стен".
     -> Именно в этом порядке и работает пайплайн:
        а) строится общая "масса" стен (union всех закрашенных
           контуров стен) — это и есть каркас этажа;
        б) в этой массе замыкаются дверные проёмы (staged closing,
           см. _close_gaps) — без этого шага соседние комнаты
           остаются связаны через открытые дверные проёмы и не
           разделяются;
        в) от прямоугольника, охватывающего всё здание, отнимается
           масса стен — то, что осталось и не касается внешней рамки
           (не "улица"/фон), и есть отдельные комнаты;
        г) для каждой комнаты — очистка контура и склейка близких
           точек/рёбер (см. правило 2).

------------------------------------------------------------------
Почему проёмы замыкаются именно так (staged closing)
------------------------------------------------------------------
Если сразу "заплыть" все проёмы одним большим радиусом — слипнется
всё подряд: и настоящие двери, и просто широкие открытые проходы
(коридоры без дверей), которые слипать нельзя — на эталоне коридор
остаётся отдельным помещением, а не частью соседней комнаты.

Поэтому проёмы закрываются по нарастающей: сначала совсем маленьким
радиусом (закрываются только самые узкие щели — типичный дверной
проём), потом чуть больше, и так далее. К моменту, когда радиус
дорастает до размера, сопоставимого с шириной открытого коридора,
коридор уже отрезан от примыкающих дверных проёмов предыдущими,
более ранними шагами, и остаётся целым, отдельным помещением, а не
сливается с соседями.

На каждом шаге патч "заклеивания" проверяется по площади — слишком
большие заплатки (значит, это не дверь, а широкий проём/коридор)
отбраковываются и не применяются.

Это тот же принцип, что и в room_builder._close_openings (там —
для векторных данных DXF), только реализован в растре поверх маски,
полученной прямо из пикселей картинки.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

Point = Tuple[float, float]


# ============================================================================
# 1. Загрузка изображения
# ============================================================================

def load_image(path: str):
    """
    Загружает изображение в BGR (как cv2.imread), но умеет читать пути
    с не-ASCII символами (кириллица в имени файла/папки) — обычный
    cv2.imread на Windows с такими путями возвращает None.
    """
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Не удалось прочитать изображение: {path}")
    return img


# ============================================================================
# 2. Цветовая маска стен + чистка "мусора" (иконок/стрелок/текста)
# ============================================================================

def _wall_mask(img, v_thresh: int = 110, chroma_thresh: int = 25):
    """
    Пиксель считается "стеной", если он одновременно:
      - достаточно тёмный (максимум каналов BGR < v_thresh);
      - достаточно "серый", т.е. малонасыщенный (разброс между
        максимальным и минимальным каналом BGR <= chroma_thresh).

    Это НЕ HSV-насыщенность: для очень тёмных пикселей относительная
    насыщенность (S в HSV) может ложно взлетать почти до максимума
    даже на однопиксельном JPEG-шуме (если V близко к 0, любая мелкая
    разница каналов даёт большое relative S). Абсолютный разброс
    каналов (chroma = max-min) от этой проблемы не страдает и
    надёжно отделяет чёрно-серые стены от цветных значков/стрелок.
    """
    b, g, r = cv2.split(img)
    mx = np.maximum(np.maximum(b, g), r).astype(np.int16)
    mn = np.minimum(np.minimum(b, g), r).astype(np.int16)
    chroma = mx - mn
    mask = ((mx < v_thresh) & (chroma <= chroma_thresh)).astype(np.uint8) * 255

    # Небольшое замыкание (радиус 1), чтобы залатать единичные
    # проколы от JPEG-шума в сплошных линиях стен — иначе тонкая
    # стена может распасться на цепочку мелких компонент и попасть
    # под чистку "мусора" на следующем шаге.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def _remove_small_blobs(
    mask,
    min_elongation: float = 2.2,
    min_dim_frac: float = 0.06,
    min_area_frac: float = 0.01,
):
    """
    Убирает из маски компактные пятна — это условные обозначения
    (огнетушитель, телефон, треугольник опасности, пиктограммы
    "выход"/человечек и т.п.), а не стены.

    Стены (в отличие от значков) почти всегда вытянутые (сильно
    отличается длинная и короткая сторона bbox) ИЛИ являются частью
    большой связной сети стен, покрывающей значительную часть
    изображения. Значок — наоборот, компактный (близкий к квадрату)
    и маленький. Компонента сохраняется, если выполняется хотя бы
    одно из условий:
      - вытянутость (long/short bbox side) >= min_elongation;
      - самая длинная сторона bbox >= min_dim_frac * диагональ кадра;
      - площадь компоненты >= min_area_frac * площадь кадра.
    """
    h, w = mask.shape[:2]
    diag = math.hypot(h, w)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        bw = stats[i, cv2.CC_STAT_WIDTH]
        bh = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        elongation = max(bw, bh) / max(1, min(bw, bh))
        big_dim = max(bw, bh) >= min_dim_frac * diag
        big_area = area >= min_area_frac * (h * w)
        if elongation >= min_elongation or big_dim or big_area:
            out[labels == i] = 255
    return out


# ============================================================================
# 3. Оценка толщины стен
# ============================================================================

def _estimate_thickness(mask) -> float:
    """
    Оценивает характерную толщину стен (в пикселях исходной картинки).

    Берётся самая большая связная компонента маски (это основная сеть
    стен), для неё считается distance transform (расстояние каждого
    "стенного" пикселя до ближайшего фона). Толщина стены в её
    прямом (не угловом/не стыковом) участке — это удвоенное значение
    distance transform в её "оси".

    Медиана здесь занижает оценку: у тонких перегородок и штриховки
    лестниц (диагональные короткие штрихи) пикселей много, и они
    "перетягивают" медиану вниз. Поэтому берётся достаточно высокий
    процентиль (90-й) — он ближе к толщине основных, "весомых" стен,
    но не настолько высок, чтобы попасть на раздутые стыки/углы.
    """
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return 3.0
    areas = [(i, stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)]
    areas.sort(key=lambda t: -t[1])
    biggest = areas[0][0]
    main = np.uint8(labels == biggest) * 255
    dist = cv2.distanceTransform(main, cv2.DIST_L2, 5)
    vals = dist[main > 0]
    vals = vals[vals > 0]
    if len(vals) == 0:
        return 3.0
    h, w = mask.shape[:2]
    diag = math.hypot(h, w)
    thickness = 2.0 * float(np.percentile(vals, 90))
    return float(np.clip(thickness, 2.0, diag * 0.03))


# ============================================================================
# 4. Замыкание проёмов (двери) — растровый staged closing
# ============================================================================

def _close_gaps(
    mask,
    thickness: float,
    radii_factors: Sequence[float] = (1.0, 1.5, 2.0, 2.5, 3.0),
    patch_area_factor: float = 150.0,
):
    """
    Поэтапно "заклеивает" узкие проёмы в стенах (двери, входные
    группы) морфологическим закрытием (dilate -> erode) с
    прямоугольным структурным элементом.

    Прямоугольное (не круглое) ядро специально выбрано, чтобы не
    скруглять внутренние углы комнат — план в основном прямоугольный,
    и MORPH_RECT сохраняет прямые углы намного лучше, чем MORPH_ELLIPSE
    (эллиптическое ядро при закрытии на большом радиусе очень заметно
    "скругляет" углы свободного пространства).

    На каждом шаге радиус растёт (radii_factors по возрастанию).
    Между текущей маской и маской после закрытия на данном радиусе
    берётся разница (что нового "заклеилось") — эта разница режется
    на связные заплатки, и заплатка принимается только если её
    площадь не превышает patch_area_factor * thickness^2. Смысл: то,
    что заклеилось узким радиусом — почти наверняка настоящий дверной
    проём (небольшая заплатка); то, что требует широкого радиуса и
    даёт большую площадь — скорее всего широкий открытый проход
    (коридор), который заклеивать не нужно, комнаты там и не должны
    разделяться.

    Именно поэтому радиусы идут от маленького к большому: узкие двери
    закрываются первыми и перестают быть "частью" открытого
    пространства коридора уже к моменту, когда радиус дорастает до
    его ширины — иначе одна большая заплатка накрыла бы сразу и
    дверь, и часть коридора, и была бы забракована целиком.
    """
    cur = mask.copy()
    max_patch_area = patch_area_factor * thickness * thickness
    for factor in radii_factors:
        r = max(1, int(round(thickness * factor)))
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1))
        closed = cv2.morphologyEx(cur, cv2.MORPH_CLOSE, k)
        diff = cv2.subtract(closed, cur)
        if diff.max() == 0:
            continue
        n, labels, stats, _ = cv2.connectedComponentsWithStats(diff, connectivity=8)
        accept = np.zeros_like(diff)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] <= max_patch_area:
                accept[labels == i] = 255
        cur = cv2.bitwise_or(cur, accept)
    return cur


# ============================================================================
# 5. Извлечение комнат (связные области свободного пространства)
# ============================================================================

def _extract_room_masks(
    closed_wall_mask,
    min_area: float,
    min_width: float,
) -> List[Tuple[int, np.ndarray]]:
    """
    Свободное пространство (всё, что не стена) режется на связные
    компоненты. Компонента, которая касается рамки кадра, считается
    "улицей"/фоном снаружи здания (общий контур этажа как раз и
    определяется тем, что он отделяет это внешнее пространство от
    внутреннего) и в комнаты не идёт. Остальные компоненты — кандидаты
    в комнаты, дополнительно отсеиваются слишком маленькие/слишком
    узкие (щели, недозаклеенные огрехи маски).

    Возвращает список (label, bool-маска) для каждой найденной комнаты.
    """
    free = cv2.bitwise_not(closed_wall_mask)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=8)
    border_labels = (
        set(labels[0, :].tolist())
        | set(labels[-1, :].tolist())
        | set(labels[:, 0].tolist())
        | set(labels[:, -1].tolist())
    )
    border_labels.discard(0)

    rooms = []
    half_w = max(1, int(round(min_width / 2.0)))
    erosion_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * half_w + 1, 2 * half_w + 1))
    for i in range(1, n):
        if i in border_labels:
            continue
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        room_mask = np.uint8(labels == i) * 255
        if min_width > 0:
            eroded = cv2.erode(room_mask, erosion_kernel)
            if not eroded.any():
                continue  # слишком узкая полоска — не настоящая комната
        rooms.append((i, room_mask))
    return rooms


# ============================================================================
# 6. Контур комнаты -> чистый прямоугольный полигон
# ============================================================================

def _contour_to_points(cnt, simplify_frac: float = 0.006) -> List[Point]:
    """Пиксельный контур -> упрощённый список вершин (approxPolyDP)."""
    peri = cv2.arcLength(cnt, True)
    eps = max(1.5, simplify_frac * peri)
    approx = cv2.approxPolyDP(cnt, eps, True)
    return [(float(p[0][0]), float(p[0][1])) for p in approx]


def _orthogonalize(points: List[Point], angle_tol_deg: float = 8.0) -> List[Point]:
    """
    Рёбра, близкие по направлению к горизонтали/вертикали (в пределах
    angle_tol_deg), принудительно выравниваются в горизонталь/
    вертикаль — общая (усреднённая) координата навязывается обеим
    вершинам ребра. Это убирает "рыхлость" в 1-2 пикселя, оставшуюся
    после растеризации/упрощения, и даёт ровные прямые углы, как в
    эталонной разметке.

    Диагональные стены (в примерах встречаются, например, у лестниц)
    в допуск не попадают и остаются как есть.
    """
    n = len(points)
    if n < 3:
        return points
    out = list(points)
    for i in range(n):
        x1, y1 = out[i]
        x2, y2 = out[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
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


def _merge_close_points(points: List[Point], tol: float) -> List[Point]:
    """
    Склеивает соседние вершины, расстояние между которыми меньше tol
    (финальная "склейка близко находящихся точек", о которой просили
    в задаче) — убирает дребезг в 1-3 пикселя после ортогонализации.
    """
    if not points:
        return points
    out: List[Point] = []
    for p in points:
        if out and math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) < tol:
            continue
        out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < tol:
        out.pop()
    return out


def _clean_room_polygon(room_mask, thickness: float) -> Optional[List[Point]]:
    contours, _ = cv2.findContours(room_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    pts = _contour_to_points(cnt)
    if len(pts) < 3:
        return None
    # Два прохода: после выравнивания рёбер иногда появляются новые
    # пары близких вершин, которые стоит склеить/выровнять ещё раз.
    for _ in range(2):
        pts = _orthogonalize(pts)
        pts = _merge_close_points(pts, tol=max(2.0, thickness * 0.5))
    if len(pts) < 3:
        return None
    return [(round(x, 1), round(y, 1)) for x, y in pts]


# ============================================================================
# 7. Главная функция
# ============================================================================

def detect_floor_plan(
    img,
    v_thresh: int = 110,
    chroma_thresh: int = 25,
    door_gap_factor: float = 5.0,
    min_room_side: float = 6.0,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Основная функция: растровый план пожарной эвакуации -> разметка
    комнат этажа.

    Параметры:
        img              — изображение (numpy BGR, как из load_image).
        v_thresh,
        chroma_thresh    — пороги отделения "стена" (тёмное+серое) от
                            фона и цветных значков/стрелок, см. _wall_mask.
        door_gap_factor  — во сколько раз типичный дверной проём шире
                            толщины стены; определяет, насколько
                            широкие проёмы будут "заклеены" при
                            разделении комнат (см. _close_gaps).
        min_room_side     — минимальная "толщина" области (в толщинах
                            стены), чтобы считаться комнатой, а не
                            щелью/огрехом маски.
        debug            — если True, в результат добавляются
                            промежуточные маски (ключ "debug") —
                            удобно для подбора порогов на новых планах.

    Возвращает:
        {"rooms": [{"points": [(x, y), ...], "room_type": ""}, ...]}
    """
    if img is None:
        raise ValueError("detect_floor_plan: пустое изображение")

    h, w = img.shape[:2]

    # 1) Только стены: цветовая маска + чистка значков/стрелок/текста.
    raw_mask = _wall_mask(img, v_thresh=v_thresh, chroma_thresh=chroma_thresh)
    wall_mask = _remove_small_blobs(raw_mask)

    # 2) Толщина стен -> производные от неё параметры.
    thickness = _estimate_thickness(wall_mask)
    diag = math.hypot(h, w)
    door_gap = float(np.clip(thickness * door_gap_factor, thickness * 3, diag * 0.08))

    # 3) Общий контур этажа + разметка комнат внутри: замыкаем дверные
    #    проёмы, чтобы соседние комнаты отделились друг от друга.
    radii_factors = [f * (door_gap / (thickness * door_gap_factor)) for f in (1.0, 1.5, 2.0, 2.5, 3.0)]
    closed_mask = _close_gaps(wall_mask, thickness, radii_factors=radii_factors)

    # 4) Комнаты = связные области свободного пространства, не
    #    касающиеся рамки кадра (рамка кадра ~ "улица"/фон снаружи
    #    здания, т.е. вне общего контура этажа).
    min_area = (thickness * 6.0) ** 2
    min_width = thickness * min_room_side / 3.0
    room_masks = _extract_room_masks(closed_mask, min_area=min_area, min_width=min_width)

    # 5) Склейка близких точек и стен: у каждой комнаты чистим контур.
    rooms: List[Dict[str, Any]] = []
    for _label, room_mask in room_masks:
        pts = _clean_room_polygon(room_mask, thickness)
        if not pts or len(pts) < 3:
            continue
        rooms.append({"points": pts, "room_type": ""})

    result: Dict[str, Any] = {"rooms": rooms}
    if debug:
        result["debug"] = {
            "wall_mask": wall_mask,
            "closed_mask": closed_mask,
            "thickness": thickness,
            "door_gap": door_gap,
        }
    return result


# ============================================================================
# 8. Автономный запуск для отладки / подбора параметров
# ============================================================================

def _debug_visualize(img, result: Dict[str, Any]):
    """Рисует найденные комнаты поверх исходного изображения (для CLI)."""
    overlay = np.zeros_like(img)
    rng = np.random.RandomState(0)
    for room in result["rooms"]:
        pts = np.array(room["points"], dtype=np.int32)
        color = tuple(int(c) for c in rng.randint(60, 255, 3))
        cv2.fillPoly(overlay, [pts], color)
        for p in room["points"]:
            cv2.circle(overlay, (int(p[0]), int(p[1])), 3, (0, 0, 255), -1)
    return cv2.addWeighted(img, 0.5, overlay, 0.6, 0)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python image_processor.py <путь_к_плану.jpg> [путь_к_результату.png]")
        raise SystemExit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "debug_rooms.png"

    image = load_image(in_path)
    res = detect_floor_plan(image, debug=True)
    print(f"Найдено комнат: {len(res['rooms'])}")
    print(f"Оценённая толщина стены: {res['debug']['thickness']:.1f}px, "
          f"ширина двери для закрытия: {res['debug']['door_gap']:.1f}px")
    for i, room in enumerate(res["rooms"], 1):
        print(f"  Комната {i}: {len(room['points'])} вершин")

    vis = _debug_visualize(image, res)
    cv2.imwrite(out_path, vis)
    print(f"Визуализация сохранена: {out_path}")