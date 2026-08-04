"""
Прогон алгоритма по синтетическим чертежам.

    python run_test.py            # все готовые plan_*.dxf
    python run_test.py файл.dxf   # свой чертёж

Тестовые DXF пересоздаются скриптами make_test.py / make_stress.py /
make_corridor.py в текущем каталоге.
"""
import logging
import os
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dxf_analyzer import analyze_dxf
from room_builder import detect_rooms

EXPECTED = {
    "plan_test.dxf": 3,
    "plan_corridor.dxf": 6,
    "plan_stress.dxf": 360,
    "plan_big.dxf": 2400,
}


def run(path, expected=None):
    t = time.time()
    res = analyze_dxf(path)
    dt = time.time() - t
    print(f"\n=== {path} ===")
    if not res.success:
        print(f"ОШИБКА — {res.error}")
        return
    rooms = res.building.rooms
    exp = f" / ожидалось {expected}" if expected else ""
    print(f"слои стен: {res.stats['wall_layers']}")
    print(f"отрезков: {res.stats['segments']}, "
          f"толщина стены ≈ {res.stats['wall_thickness_m']:.3f} м")
    print(f"комнат: {len(rooms)}{exp}, "
          f"суммарная площадь {res.stats['total_area_sqm']:.1f} м², {dt:.1f} с")
    if len(rooms) <= 25:
        for r in rooms:
            n = len(r.polygon.exterior.coords) - 1
            print(f"   {r.area_sqm:8.2f} м²  точек {n:3d}  «{r.text_label}»")
    else:
        pts = max(len(r.polygon.exterior.coords) - 1 for r in rooms)
        print(f"   макс. точек в контуре: {pts}")


def self_check():
    """Ручной редактор: одиночные осевые линии, сетка 2x2 -> 4 комнаты."""
    walls = [(x, 0, x, 200) for x in (0, 100, 200)]
    walls += [(0, y, 200, y) for y in (0, 100, 200)]
    rooms = detect_rooms(walls, mode="thin", min_area=100)
    print(f"\n=== режим thin (ручной редактор) ===\nкомнат: {len(rooms)} / ожидалось 4")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:]
    if args:
        for p in args:
            run(p)
    else:
        found = False
        for name, exp in EXPECTED.items():
            if os.path.exists(name):
                found = True
                run(name, exp)
        if not found:
            print("Нет тестовых DXF. Сначала: python make_test.py && "
                  "python make_corridor.py && python make_stress.py")
        self_check()
    print(f"\nпик RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} МБ")
