import ezdxf
doc = ezdxf.new("R2010"); doc.header["$INSUNITS"] = 4
msp = doc.modelspace(); doc.layers.add("Стены"); doc.layers.add("Подписи")
W = {"layer": "Стены"}
def line(a, b): msp.add_line(a, b, dxfattribs=W)
def band(y, x1, x2, doors):
    """горизонтальная стена: две линии y±100 с проёмами шириной 900"""
    for yy in (y - 100, y + 100):
        xs = [x1]
        for d in doors: xs += [d - 450, d + 450]
        xs.append(x2)
        for i in range(0, len(xs) - 1, 2): line((xs[i], yy), (xs[i + 1], yy))
def vband(x, y1, y2):
    for xx in (x - 100, x + 100): line((xx, y1), (xx, y2))
msp.add_lwpolyline([(0,0),(12000,0),(12000,8000),(0,8000)], close=True, dxfattribs=W)
msp.add_lwpolyline([(200,200),(11800,200),(11800,7800),(200,7800)], close=True, dxfattribs=W)
band(3500, 200, 11800, [2000, 6000, 10000])   # нижняя стена коридора
band(4900, 200, 11800, [3000, 9000])          # верхняя стена коридора
vband(4000, 200, 3400); vband(8000, 200, 3400)
vband(6000, 5000, 7800)
for name, xy in [("Каб1",(2000,1800)),("Каб2",(6000,1800)),("Каб3",(10000,1800)),
                 ("Коридор",(6000,4200)),("Зал1",(3000,6500)),("Зал2",(9000,6500))]:
    msp.add_text(name, height=200, dxfattribs={"layer":"Подписи"}).set_placement(xy)
doc.saveas("plan_corridor.dxf"); print("ok")
