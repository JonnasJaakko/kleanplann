"""
Нормативы уборки — расчёт времени по формуле: время = площадь × база × коэффициент.

Базовая норма: 1 минута на 1 квадратный метр (изменяемый параметр).
"""
from typing import Dict, List, Tuple

# Базовая норма времени уборки (мин/м²) — изменяемый параметр
BASE_TIME_PER_SQ_M = 1.0  # 1 минута на 1 кв.м

# Коэффициенты сложности по типам помещений
COMPLEXITY_FACTOR: Dict[str, float] = {
    "коридор": 1.0,
    "санузел": 1.8,
    "склад": 0.8,
    "зал": 1.2,
    "кабинет": 1.0,
    "кухня": 1.5
}

# Требуемая частота уборки (раз в день)
DEFAULT_FREQUENCY_PER_DAY: Dict[str, int] = {
    "коридор": 2,
    "санузел": 3,
    "склад": 1,
    "зал": 2,
    "кабинет": 1,
    "кухня": 2
}

# Допустимое время на перемещение между зонами (минут)
TRANSIT_TIME_MINUTES = 2.0

# Скорость перемещения уборщика (метров в минуту)
WALKING_SPEED_M_PER_MIN = 50.0

# Проходимость по умолчанию в зависимости от типа помещения
DEFAULT_TRAFFIC_PER_TYPE: Dict[str, int] = {
    "коридор": 50,
    "санузел": 40,
    "склад": 10,
    "зал": 30,
    "кабинет": 30,
    "кухня": 20,
}

# Дополнительное время на уборку санузла (минут) — сверх формулы
SANITARY_BONUS_MINUTES = 10


# ──────────────────────────────────────────────
# Валидация
# ──────────────────────────────────────────────

class ValidationError(ValueError):
    """Ошибка валидации входных данных."""
    pass


def validate_room_data(area_m2: float, complexity: float, frequency: int) -> None:
    """Проверяет корректность данных помещения.

    Raises:
        ValidationError: если площадь ≤ 0, коэффициент ≤ 0, частота ≤ 0.
    """
    if area_m2 <= 0:
        raise ValidationError(f"Площадь должна быть положительной, получено: {area_m2}")
    if complexity <= 0:
        raise ValidationError(f"Коэффициент сложности должен быть положительным, получено: {complexity}")
    if frequency <= 0:
        raise ValidationError(f"Частота уборки должна быть положительной, получено: {frequency}")


# ──────────────────────────────────────────────
# Расчёт времени уборки
# ──────────────────────────────────────────────

def get_cleaning_time_minutes(room_type: str, area_m2: float) -> float:
    """Время одной уборки помещения (минут).

    Формула: Площадь × BASE_TIME_PER_SQ_M × Коэффициент сложности.
    Для санузла добавляется SANITARY_BONUS_MINUTES сверху.
    """
    if area_m2 <= 0:
        return 0.0
    factor = COMPLEXITY_FACTOR.get(room_type, 1.0)
    base = area_m2 * BASE_TIME_PER_SQ_M * factor
    if room_type == "санузел":
        base += SANITARY_BONUS_MINUTES
    return base


def get_frequency_per_day(room_type: str) -> int:
    """Сколько раз в день нужно убирать помещение данного типа."""
    return DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1)


# ──────────────────────────────────────────────
# Расширенный расчёт для одного помещения
# ──────────────────────────────────────────────

def calculate_room_cleaning(room_type: str, area_m2: float,
                            complexity: float = None, frequency: int = None) -> dict:
    """Возвращает полный расчёт для одного помещения.

    Параметры:
        room_type — тип помещения (строка, может быть произвольной)
        area_m2 — площадь в кв.м
        complexity — коэффициент сложности (если None, берётся из COMPLEXITY_FACTOR)
        frequency — частота уборки в день (если None, берётся из DEFAULT_FREQUENCY_PER_DAY)

    Возвращает словарь:
        {
            "room_type": str,
            "area_m2": float,
            "complexity": float,
            "frequency": int,
            "time_per_cleaning_min": float,   # время одной уборки (мин)
            "total_daily_min": float,         # суммарное время за день (мин)
        }

    Raises:
        ValidationError: при некорректных данных.
    """
    if complexity is None:
        complexity = COMPLEXITY_FACTOR.get(room_type, 1.0)
    if frequency is None:
        frequency = DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1)

    validate_room_data(area_m2, complexity, frequency)

    time_per = get_cleaning_time_minutes(room_type, area_m2)
    total = time_per * frequency

    return {
        "room_type": room_type,
        "area_m2": area_m2,
        "complexity": complexity,
        "frequency": frequency,
        "time_per_cleaning_min": round(time_per, 1),
        "total_daily_min": round(total, 1),
    }


# ──────────────────────────────────────────────
# Расчёт для списка помещений
# ──────────────────────────────────────────────

def calculate_cleaning_summary(rooms: List[dict]) -> dict:
    """Принимает список помещений и возвращает сводку.

    Каждый элемент списка — словарь с ключами:
        room_type, area_m2, [complexity], [frequency]

    Возвращает:
        {
            "rooms": [расчёт для каждого помещения],
            "total_minutes": float,     # суммарное время в минутах
            "total_hours": float,       # суммарное время в часах
            "total_hours_str": str,     # строка "X ч Y мин"
        }

    Raises:
        ValidationError: при некорректных данных любого помещения.
    """
    results = []
    grand_total = 0.0

    for r in rooms:
        calc = calculate_room_cleaning(
            room_type=r.get("room_type", ""),
            area_m2=r.get("area_m2", 0),
            complexity=r.get("complexity"),
            frequency=r.get("frequency"),
        )
        results.append(calc)
        grand_total += calc["total_daily_min"]

    hours = int(grand_total // 60)
    minutes = int(round(grand_total % 60))

    return {
        "rooms": results,
        "total_minutes": round(grand_total, 1),
        "total_hours": round(grand_total / 60, 2),
        "total_hours_str": f"{hours} ч {minutes} мин",
    }