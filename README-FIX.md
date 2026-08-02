# kleanplann — исправленный алгоритм распознавания комнат

Полная копия проекта с переписанными `room_builder.py` и `dxf_analyzer.py`
и правками в `app.py`. Оригиналы лежат рядом как `*.py.orig`.

## Запуск

```bash
cd kleanplann-fix
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # PySide6, shapely>=2.0, ezdxf>=1.0 и прочее

python run_test.py                     # прогон по синтетическим чертежам
python run_test.py мой_чертёж.dxf      # свой файл
python main.py                         # само приложение
```

Только алгоритму нужны лишь `shapely` и `ezdxf` — `run_test.py` работает без PySide6.

## Что где

| файл | что |
|---|---|
| `room_builder.py` | построение комнат: режимы `thin` / `solid`, заделка проёмов, оценка толщины стены |
| `dxf_analyzer.py` | чтение DXF: слои, блоки, дуги, единицы, подписи комнат |
| `rooms-algorithm.patch` | диф к исходному репозиторию (`git apply`) |
| `*.py.orig` | исходные версии для сравнения |
| `make_test.py` | 3 комнаты, проём с косяками и без |
| `make_corridor.py` | коридор 1.2 м + двери 0.9 м — проверка, что коридор не залился стеной |
| `make_stress.py` | сетка 360 комнат, мебель блоками, размерные линии |
| `plan_*.dxf` | уже сгенерированные чертежи (`plan_big.dxf` — 2400 комнат) |

## Ожидаемый результат `run_test.py`

```
plan_test.dxf       3 / 3      0.0 с
plan_corridor.dxf   6 / 6      0.0 с
plan_stress.dxf   360 / 360    1.4 с
plan_big.dxf     2400 / 2400  13.5 с
режим thin          4 / 4
пик RSS 348 МБ
```

## Настройка под конкретный чертёж

```python
from dxf_analyzer import analyze_dxf

res = analyze_dxf("plan.dxf", {
    "wall_layers": ["A-WALL", "Стены_несущие"],  # если автовыбор промахнулся
    "wall_thickness_m": 0.25,                    # если оценка неверна
    "door_gap_m": 1.6,      # макс. ширина проёма, который надо зашить
    "min_room_area_sqm": 1.5,
    "min_room_width_m": 0.7,
    "measure": "inner",     # "inner" — по внутренним граням, "center" — по осям стен
})
print(res.stats)
for r in res.building.rooms:
    print(r.id, round(r.area_sqm, 2), r.text_label)
```

Диагностика при промахе: включить `logging.basicConfig(level=logging.INFO)` —
лог печатает выбранные слои стен, число отрезков, оценённую толщину стены,
режим (`solid`/`thin`), число сшитых стыков и заделанных проёмов.

Типовые случаи:

* комнат сильно больше нормы и они узкие — режим ушёл в `thin`, задать
  `{"mode": "solid", "wall_thickness_m": <толщина>}`;
* соседние комнаты слиплись — проём шире `door_gap_m`, поднять значение;
* коридор пропал и стал стеной — `door_gap_m` больше ширины коридора, снизить.
