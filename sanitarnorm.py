"""Единая модель трудоёмкости и периодичности уборки KleanPlann.

Нормативы хранятся в одном месте. Погода влияет на трудоёмкость одной уборки,
а увеличение кратности применяется только к помещениям, реально чувствительным
к осадкам/грязи (коридоры и лестницы), а не ко всему объекту подряд.
"""
from __future__ import annotations

import math

GOST_TIME_PER_SQ_M = {
    "кабинет": 0.30,
    "коридор": 0.30,
    "зал": 0.25,
    "склад": 0.20,
    "санузел": 1.50,
    "кухня": 1.20,
    "лестница": 0.40,
    "default": 0.40,
}

SETUP_TIME_PER_ROOM = {
    "санузел": 5.0,
    "кабинет": 3.0,
    "default": 3.0,
}

DEFAULT_FREQUENCY_PER_DAY = {
    "санузел": 3,
    "коридор": 2,
    "кабинет": 1,
    "зал": 1,
    "склад": 1,
    "кухня": 2,
    "лестница": 2,
    "default": 1,
}

# Повышаем кратность только там, где погода действительно увеличивает загрязнение.
# Фактор 1.2 = дождь, 1.5 = снег, 1.8 = сильный дождь/осадки.
WEATHER_FREQUENCY_BOOST = {
    1.2: {"коридор": 1, "лестница": 1},
    1.5: {"коридор": 1, "лестница": 1},
    1.8: {"коридор": 1, "лестница": 1},
}

COMPLEXITY_FACTOR = {
    "санузел": 1.30,
    "кабинет": 1.00,
    "коридор": 1.00,
    "зал": 1.00,
    "склад": 0.80,
    "кухня": 1.00,
    "лестница": 1.00,
    "default": 1.00,
}

DEFAULT_TRAFFIC_PER_TYPE = {
    "санузел": 50,
    "коридор": 100,
    "кабинет": 10,
    "зал": 20,
    "склад": 2,
    "кухня": 30,
    "лестница": 100,
}

_TYPE_ALIASES = {
    "wc": "санузел", "w.c.": "санузел", "toilet": "санузел", "туалет": "санузел",
    "bathroom": "санузел", "restroom": "санузел", "сан.узел": "санузел",
    "office": "кабинет", "room": "кабинет", "кабинет": "кабинет",
    "corridor": "коридор", "hallway": "коридор", "коридор": "коридор",
    "store": "склад", "storage": "склад", "склад": "склад",
    "hall": "зал", "lobby": "зал", "зал": "зал",
    "kitchen": "кухня", "кухня": "кухня",
    "stairs": "лестница", "staircase": "лестница", "лестница": "лестница",
}


def normalize_room_type(room_type: str) -> str:
    value = (room_type or "").strip().lower()
    if not value:
        return "default"
    for alias, canonical in _TYPE_ALIASES.items():
        if alias in value:
            return canonical
    for canonical in GOST_TIME_PER_SQ_M:
        if canonical != "default" and canonical in value:
            return canonical
    return "default"


def _weather_bucket(weather_factor: float) -> float:
    try:
        value = float(weather_factor or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    return min((1.0, 1.2, 1.5, 1.8), key=lambda x: abs(x - value))


def get_cleaning_time_minutes(
    room_type: str,
    area_m2: float,
    weather_factor: float = 1.0,
    cleaning_type: str = "поддерживающая",
) -> float:
    """Время одной уборки помещения в минутах."""
    canonical = normalize_room_type(room_type)
    base_rate = GOST_TIME_PER_SQ_M[canonical]
    comp_factor = COMPLEXITY_FACTOR[canonical]
    setup_time = SETUP_TIME_PER_ROOM.get(canonical, SETUP_TIME_PER_ROOM["default"])
    try:
        weather = max(0.5, float(weather_factor or 1.0))
    except (TypeError, ValueError):
        weather = 1.0
    multiplier = 2.0 if str(cleaning_type).strip().lower() == "генеральная" else 1.0
    area = max(0.0, float(area_m2 or 0.0))
    return float(math.ceil((setup_time + area * base_rate * comp_factor * weather) * multiplier))


def get_frequency_per_day(room_type: str) -> int:
    """Базовая нормативная кратность без учёта погоды."""
    return int(DEFAULT_FREQUENCY_PER_DAY.get(normalize_room_type(room_type), 1))


def get_effective_frequency(room_type: str, weather_factor: float = 1.0) -> int:
    """Кратность с погодной поправкой без бессмысленного удвоения всех помещений."""
    canonical = normalize_room_type(room_type)
    base = get_frequency_per_day(canonical)
    bucket = _weather_bucket(weather_factor)
    boost = WEATHER_FREQUENCY_BOOST.get(bucket, {}).get(canonical, 0)
    return max(1, base + int(boost))


class ValidationError(ValueError):
    pass


def calculate_room_cleaning(room_type, area_m2, complexity=1.0, frequency=1):
    if float(area_m2) <= 0:
        raise ValidationError("Площадь должна быть > 0")
    if float(complexity) <= 0:
        raise ValidationError("Коэффициент должен быть > 0")
    if int(frequency) <= 0:
        raise ValidationError("Кратность должна быть > 0")
    base = get_cleaning_time_minutes(room_type, area_m2)
    per = float(math.ceil(base * float(complexity)))
    return {
        "room_type": room_type,
        "area_m2": float(area_m2),
        "complexity": float(complexity),
        "frequency": int(frequency),
        "time_per_cleaning_min": per,
        "total_daily_min": per * int(frequency),
    }


def calculate_cleaning_summary(rooms):
    rows = [calculate_room_cleaning(**item) for item in rooms]
    total = sum(r["total_daily_min"] for r in rows)
    return {
        "rooms": rows,
        "total_minutes": round(total, 1),
        "total_hours": round(total / 60, 2),
        "total_hours_str": f"{total / 60:.2f} ч",
    }


BASE_TIME_PER_SQ_M = 0.4
