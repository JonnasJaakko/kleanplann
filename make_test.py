"""Синтетический DXF: двухлинейные стены, проёмы, мебель, размеры, подписи."""
import ezdxf, random

doc = ezdxf.new("R2010", setup=True)
doc.header["$INSUNITS"] = 4  # мм
msp = doc.modelspace()
for name in ("Стены", "Мебель", "Размеры", "Двери", "Подписи"):
    doc.layers.add(name)

W = {"layer": "Стены"}

def poly(pts, closed=True):
    msp.add_lwpolyline(pts, close=closed, dxfattribs=W)

def seg(a, b):
    msp.add_line(a, b, dxfattribs=W)

T = 200
# наружная стена: два замкнутых контура
poly([(0, 0), (12000, 0), (12000, 8000), (0, 8000)])
poly([(T, T), (12000 - T, T), (12000 - T, 8000 - T), (T, 8000 - T)])

# внутренняя вертикальная стена x=6000, проём с косяками y=2000..2900
for x in (5900, 6100):
    seg((x, T), (x, 2000))
    seg((x, 2900), (x, 8000 - T))
seg((5900, 2000), (6100, 2000))       # косяк
seg((5900, 2900), (6100, 2900))       # косяк

# внутренняя горизонтальная стена y=4000, проём БЕЗ косяков x=8000..8900
for y in (3900, 4100):
    seg((6100, y), (8000, y))
    seg((8900, y), (12000 - T, y))

# --- шум ---
for i in range(40):  # мебель
    x = random.uniform(500, 5000); y = random.uniform(500, 7000)
    msp.add_lwpolyline([(x, y), (x + 600, y), (x + 600, y + 400), (x, y + 400)],
                       close=True, dxfattribs={"layer": "Мебель"})
for i in range(12):  # размерные линии
    msp.add_line((-800 - i * 100, 0), (-800 - i * 100, 8000), dxfattribs={"layer": "Размеры"})
msp.add_arc((5900, 2000), 900, 0, 90, dxfattribs={"layer": "Двери"})  # полотно двери

msp.add_text("Кабинет 101", height=200,
             dxfattribs={"layer": "Подписи"}).set_placement((3000, 4000))
msp.add_text("Санузел", height=200,
             dxfattribs={"layer": "Подписи"}).set_placement((9000, 6000))
msp.add_text("Коридор", height=200,
             dxfattribs={"layer": "Подписи"}).set_placement((9000, 2000))

doc.saveas("plan_test.dxf")
print("ok")
