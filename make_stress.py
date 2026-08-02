"""Стресс-тест: сетка 24x15 комнат, двухлинейные стены, двери, мебель в блоках."""
import ezdxf, random

NX, NY = 24, 15
CW, CH = 5000.0, 4000.0      # мм
T = 200.0
DOOR = 900.0

doc = ezdxf.new("R2010")
doc.header["$INSUNITS"] = 4
msp = doc.modelspace()
for n in ("Стены", "Мебель", "Размеры", "Подписи"):
    doc.layers.add(n)
W = {"layer": "Стены"}

# блок мебели -> вставляем через INSERT (проверяем разворачивание блоков)
blk = doc.blocks.new(name="СТОЛ")
blk.add_lwpolyline([(0, 0), (1400, 0), (1400, 700), (0, 700)], close=True,
                   dxfattribs={"layer": "Мебель"})

def wall_line(x1, y1, x2, y2, gaps):
    """Линия с вырезанными проёмами. gaps — список (t_start, t_end) по длине."""
    if not gaps:
        msp.add_line((x1, y1), (x2, y2), dxfattribs=W)
        return
    dx, dy = x2 - x1, y2 - y1
    cuts = [0.0]
    for a, b in sorted(gaps):
        cuts += [a, b]
    cuts.append(1.0)
    for i in range(0, len(cuts) - 1, 2):
        a, b = cuts[i], cuts[i + 1]
        if b - a < 1e-9:
            continue
        msp.add_line((x1 + dx * a, y1 + dy * a), (x1 + dx * b, y1 + dy * b), dxfattribs=W)

H = NY * CH
L = NX * CW

# вертикальные линии сетки
for i in range(NX + 1):
    x = i * CW
    for off in (-T / 2, T / 2):
        if i == 0:
            off = T / 2 + off - T / 2 + off  # наружная: обе линии внутрь не важны
        gaps = []
        if 0 < i < NX:                      # двери во внутренних стенах
            for j in range(NY):
                c = (j * CH + CH / 2) / H
                gaps.append((c - DOOR / 2 / H, c + DOOR / 2 / H))
        wall_line(x + off, 0, x + off, H, gaps)

# горизонтальные линии сетки
for j in range(NY + 1):
    y = j * CH
    for off in (-T / 2, T / 2):
        gaps = []
        if 0 < j < NY:
            for i in range(0, NX, 3):       # дверь в каждой третьей ячейке
                c = (i * CW + CW / 2) / L
                gaps.append((c - DOOR / 2 / L, c + DOOR / 2 / L))
        wall_line(0, y + off, L, y + off, gaps)

# шум: мебель блоками, размеры, подписи
random.seed(0)
for i in range(NX):
    for j in range(NY):
        msp.add_blockref("СТОЛ", (i * CW + 800, j * CH + 800), dxfattribs={"layer": "Мебель"})
        msp.add_text(f"Пом. {i*NY+j+1}", height=250,
                     dxfattribs={"layer": "Подписи"}).set_placement(
                         (i * CW + CW / 2, j * CH + CH / 2))
for k in range(60):
    msp.add_line((-1000 - k * 120, 0), (-1000 - k * 120, H), dxfattribs={"layer": "Размеры"})

doc.saveas("plan_stress.dxf")
print("ok, комнат должно быть", NX * NY)
