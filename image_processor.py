# """
# Обработка изображений: загрузка (с поддержкой кириллицы), детекция стен,
# масштабирование площадей.
# """
# import cv2
# import numpy as np
# from typing import List, Tuple, Optional

# def load_image(filepath: str) -> np.ndarray:
#     """
#     Загружает изображение, корректно обрабатывая пути с кириллицей.
#     """
#     with open(filepath, 'rb') as f:
#         data = np.frombuffer(f.read(), dtype=np.uint8)
#     img = cv2.imdecode(data, cv2.IMREAD_COLOR)
#     if img is None:
#         raise ValueError(f"Не удалось загрузить изображение {filepath}")
#     return img

# def detect_walls(image: np.ndarray) -> List[List[Tuple[float, float]]]:
#     """
#     Возвращает список контуров комнат в пиксельных координатах.
#     Если распознавание не удаётся, возвращает один контур — границы всего изображения.
#     """
#     gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
#     blurred = cv2.GaussianBlur(gray, (5, 5), 0)
#     thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
#                                    cv2.THRESH_BINARY_INV, 11, 2)
#     kernel = np.ones((3, 3), np.uint8)
#     closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
#     contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#     rooms = []
#     min_area_pixels = 500
#     h, w = image.shape[:2]
#     for cnt in contours:
#         area = cv2.contourArea(cnt)
#         if area < min_area_pixels:
#             continue
#         epsilon = 0.005 * cv2.arcLength(cnt, True)
#         approx = cv2.approxPolyDP(cnt, epsilon, True)
#         points = [(float(pt[0][0]), float(pt[0][1])) for pt in approx]
#         if len(points) >= 3:
#             rooms.append(points)
#     if not rooms:
#         rooms = [[(0, 0), (w-1, 0), (w-1, h-1), (0, h-1)]]
#     return rooms

# def draw_walls(image: np.ndarray, contours: List[List[Tuple[float, float]]],
#                color=(0, 0, 255), thickness=2) -> np.ndarray:
#     vis = image.copy()
#     for cnt in contours:
#         pts = np.array(cnt, dtype=np.int32).reshape((-1, 1, 2))
#         cv2.polylines(vis, [pts], isClosed=True, color=color, thickness=thickness)
#     return vis

# def calibrate_scale(calib_line: Tuple[Tuple[float, float], Tuple[float, float], float]) -> float:
#     """Вычисляет масштаб: метров на пиксель (m/px)."""
#     (x1, y1), (x2, y2), length_m = calib_line
#     pixel_len = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
#     if pixel_len == 0:
#         return 0.0
#     return length_m / pixel_len

# def polygon_area_pixels(points: List[Tuple[float, float]]) -> float:
#     """Площадь полигона в пикселях (шнуровка)."""
#     n = len(points)
#     area = 0.0
#     for i in range(n):
#         x1, y1 = points[i]
#         x2, y2 = points[(i+1)%n]
#         area += x1*y2 - x2*y1
#     return abs(area) / 2.0

import cv2, numpy as np

def load_image(filepath: str) -> np.ndarray:
    with open(filepath, 'rb') as f: data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None: raise ValueError(f"Не удалось загрузить изображение {filepath}")
    return img

def detect_walls(image: np.ndarray):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5,5), 0)
    # Canny для выделения границ
    edges = cv2.Canny(blurred, 50, 150)
    # Дилатация для соединения близких линий
    kernel = np.ones((2,2), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rooms = []; h,w = image.shape[:2]
    for cnt in contours:
        if cv2.contourArea(cnt) < 500: continue
        epsilon = 0.005 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, epsilon, True)
        pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(pts) >= 3: rooms.append(pts)
    if not rooms: rooms = [[(0,0),(w-1,0),(w-1,h-1),(0,h-1)]]
    return rooms

def calibrate_scale(calib_line):
    (x1,y1),(x2,y2), length_m = calib_line
    pixel_len = np.sqrt((x2-x1)**2 + (y2-y1)**2)
    if pixel_len == 0: return 0.0
    return length_m / pixel_len