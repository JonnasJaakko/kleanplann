"""
Нормативы СанПиН и коэффициенты для типов помещений.
"""
from typing import Dict, Tuple

# Время уборки 1 м² в минутах (базовое)
BASE_TIME_PER_SQ_M = 0.05 * 60  # было 0.05 часа = 3 мин

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

def get_cleaning_time_minutes(room_type: str, area_m2: float) -> float:
    """Возвращает расчётное время уборки помещения в минутах."""
    factor = COMPLEXITY_FACTOR.get(room_type, 1.0)
    return area_m2 * BASE_TIME_PER_SQ_M * factor

def get_frequency_per_day(room_type: str) -> int:
    """Сколько раз в день нужно убирать помещение данного типа."""
    return DEFAULT_FREQUENCY_PER_DAY.get(room_type, 1)