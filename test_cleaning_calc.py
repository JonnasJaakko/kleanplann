"""
Тест расчёта времени уборки по новой формуле:
    время одной уборки = площадь × BASE_TIME_PER_SQ_M × коэффициент
    суммарное время за день = время одной уборки × частота

Проверяет:
  * предоставленные пользователем данные;
  * дополнительные варианты;
  * валидацию (отрицательная площадь, коэффициент, частота).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sanitarnorm import (
    calculate_room_cleaning,
    calculate_cleaning_summary,
    BASE_TIME_PER_SQ_M,
    ValidationError,
)

# ── Тестовый набор ──────────────────────────────────────────────
TEST_ROOMS = [
    {"room_type": "коридор", "area_m2": 50,  "complexity": 1.0, "frequency": 2},
    {"room_type": "санузел", "area_m2": 15,  "complexity": 1.8, "frequency": 3},
    {"room_type": "склад",   "area_m2": 100, "complexity": 0.8, "frequency": 1},
    {"room_type": "кухня",   "area_m2": 30,  "complexity": 1.5, "frequency": 2},
]

EXTRA_ROOMS = [
    # пустой тип — должен использовать complexity/frequency по умолчанию
    {"room_type": "зал",     "area_m2": 200, "complexity": 1.2, "frequency": 2},
    # кабинет — 1 раз в день, коэф 1.0
    {"room_type": "кабинет", "area_m2": 20,  "complexity": 1.0, "frequency": 1},
    # большой зал — проверка масштаба
    {"room_type": "зал",     "area_m2": 500, "complexity": 1.2, "frequency": 2},
]


def print_summary(summary: dict, label: str):
    print(f"\n=== {label} ===")
    print(f"{'Тип':<10} {'Площадь':>8} {'Коэфф':>6} {'Частота':>8} {'1 уборка':>10} {'В день':>10}")
    print("-" * 60)
    for r in summary["rooms"]:
        print(f"{r['room_type']:<10} {r['area_m2']:>8.1f} {r['complexity']:>6.2f} "
              f"{r['frequency']:>8} {r['time_per_cleaning_min']:>10.1f} {r['total_daily_min']:>10.1f}")
    print("-" * 60)
    print(f"ИТОГО: {summary['total_hours_str']}  ({summary['total_minutes']} мин, {summary['total_hours']} ч)")


def test_validation():
    print("\n=== ВАЛИДАЦИЯ ===")
    ok = True
    cases = [
        # (описание, параметры, ожидаемая ошибка)
        ("отрицательная площадь",
         dict(room_type="коридор", area_m2=-10, complexity=1.0, frequency=1),
         "ValidationError"),
        ("нулевая площадь",
         dict(room_type="коридор", area_m2=0, complexity=1.0, frequency=1),
         "ValidationError"),
        ("отрицательный коэффициент",
         dict(room_type="коридор", area_m2=10, complexity=-1.0, frequency=1),
         "ValidationError"),
        ("нулевой коэффициент",
         dict(room_type="коридор", area_m2=10, complexity=0, frequency=1),
         "ValidationError"),
        ("отрицательная частота",
         dict(room_type="коридор", area_m2=10, complexity=1.0, frequency=-1),
         "ValidationError"),
        ("нулевая частота",
         dict(room_type="коридор", area_m2=10, complexity=1.0, frequency=0),
         "ValidationError"),
    ]
    for desc, params, expected in cases:
        try:
            calculate_room_cleaning(**params)
            print(f"  ✗ НЕ ОШИБКА: {desc}")
            ok = False
        except ValidationError as e:
            print(f"  ✓ {desc}: {e}")
        except Exception as e:
            print(f"  ✗ НЕПРАВИЛЬНАЯ ОШИБКА {type(e).__name__} для {desc}: {e}")
            ok = False
    return ok


def test_manual_expected():
    """Проверка вручную рассчитанных значений для тестового набора."""
    print("\n=== РУЧНАЯ ПРОВЕРКА ===")
    ok = True
    # Коридор: 50 × 1.0 × 1.0 = 50 мин; ×2 = 100 мин
    # Санузел: 15 × 1.0 × 1.8 = 27 мин; ×3 = 81 мин
    # Склад: 100 × 1.0 × 0.8 = 80 мин; ×1 = 80 мин
    # Кухня: 30 × 1.0 × 1.5 = 45 мин; ×2 = 90 мин
    # Итого в день: 100 + 81 + 80 + 90 = 351 мин = 5 ч 51 мин
    expected = {
        "коридор": (50.0, 100.0),
        "санузел": (37.0, 111.0),  # +10 мин бонус SANITARY_BONUS_MINUTES
        "склад": (80.0, 80.0),
        "кухня": (45.0, 90.0),
    }
    for r in TEST_ROOMS:
        calc = calculate_room_cleaning(**r)
        exp_per, exp_total = expected[r["room_type"]]
        if abs(calc["time_per_cleaning_min"] - exp_per) > 0.01 or \
           abs(calc["total_daily_min"] - exp_total) > 0.01:
            print(f"  ✗ {r['room_type']}: получили {calc['time_per_cleaning_min']}/{calc['total_daily_min']}, "
                  f"ожидали {exp_per}/{exp_total}")
            ok = False
        else:
            print(f"  ✓ {r['room_type']}: {calc['time_per_cleaning_min']} мин/уборка, "
                  f"{calc['total_daily_min']} мин/день")
    return ok


def main():
    print(f"Базовая норма: {BASE_TIME_PER_SQ_M} мин/м²")
    print("=" * 60)

    # 1. Предоставленный набор
    summary = calculate_cleaning_summary(TEST_ROOMS)
    print_summary(summary, "Тестовый набор (от пользователя)")

    # 2. Дополнительные варианты
    extra_summary = calculate_cleaning_summary(EXTRA_ROOMS)
    print_summary(extra_summary, "Дополнительные варианты")

    # 3. Валидация
    ok_valid = test_validation()

    # 4. Ручная проверка
    ok_manual = test_manual_expected()

    print("\n" + ("✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ" if (ok_valid and ok_manual) else "✗ ЕСТЬ ПРОБЛЕМЫ"))
    sys.exit(0 if (ok_valid and ok_manual) else 1)


if __name__ == "__main__":
    main()