# sanitarnorm.py
"""
Отраслевые нормативы времени и частоты уборки на основе ГОСТ Р 51870-2014 и СанПиН.
Единый источник правды для планировщика и калькулятора стоимости.
"""
import math

# Базовое время уборки 1 кв. метра (в минутах) по ГОСТ Р 51870-2014
GOST_TIME_PER_SQ_M = {
    "кабинет": 0.3,    # Норма поддерживающей уборки административных зон
    "коридор": 0.3,    # Норма для зон с транзитным трафиком
    "зал": 0.25,       # Большие открытые пространства (высокая скорость прохода мопом/техникой)
    "склад": 0.2,       # Минимальная влажная протирка полов
    "санузел": 1.5,    # Повышенное время на сантехнику, зеркала и дезинфекцию
    "кухня": 1.2,      # Зоны приема пищи (смыв жиров, дезинфекция)
    "лестница": 0.4,  # Базовое время прохода ступеней; коэффициент сложности = 1
    "default": 0.4
}

# Минимальное подготовительное время на одну комнату (в минутах)
# Включает: вход, вынос мусора, замену расходных материалов, смену салфетки/воды
SETUP_TIME_PER_ROOM = {
    "санузел": 5.0,
    "кабинет": 3.0,
    "default": 3.0
}

# Базовая кратность (периодичность) уборок за смену
DEFAULT_FREQUENCY_PER_DAY = {
    "санузел": 3,      # Соответствует СанПиН для мест общего пользования
    "коридор": 2,      # Утренний и вечерний пиковые потоки
    "кабинет": 1,      # Один раз в смену
    "зал": 1,
    "склад": 1,
    "кухня": 2,        # После обеденных перерывов
    "лестница": 3,     # Три уборки в день
    "default": 1
}

# Коэффициенты заставленности мебелью и сложности структуры помещения
COMPLEXITY_FACTOR = {
    "санузел": 1.3,
    "кабинет": 1.1,    # Офисные столы, стулья, оргтехника
    "коридор": 1.0,    # Свободные пространства
    "зал": 0.9,        # Высокая доступность для инвентаря
    "склад": 0.8,
    "лестница": 1.0,
    "default": 1.0
}

DEFAULT_TRAFFIC_PER_TYPE = {
    "санузел": 50, "коридор": 100, "кабинет": 10, "зал": 20, "склад": 2, "кухня": 30, "лестница": 100
}

def get_cleaning_time_minutes(room_type: str, area_m2: float, weather_factor: float = 1.0, cleaning_type: str = "поддерживающая") -> float:
    """Рассчитывает точное время одной уборки помещения по ГОСТу."""
    room_type_lower = room_type.lower() if room_type else "default"
    
    base_rate = GOST_TIME_PER_SQ_M.get("default")
    for k, v in GOST_TIME_PER_SQ_M.items():
        if k in room_type_lower:
            base_rate = v
            break
            
    comp_factor = COMPLEXITY_FACTOR.get("default")
    for k, v in COMPLEXITY_FACTOR.items():
        if k in room_type_lower:
            comp_factor = v
            break
            
    setup_time = SETUP_TIME_PER_ROOM.get("default")
    for k, v in SETUP_TIME_PER_ROOM.items():
        if k in room_type_lower:
            setup_time = v
            break

    multiplier = 2.0 if str(cleaning_type).strip().lower() == "генеральная" else 1.0
    pure_duration = (setup_time + (area_m2 * base_rate * comp_factor * weather_factor)) * multiplier
    return float(math.ceil(pure_duration))

def get_frequency_per_day(room_type: str) -> int:
    """Возвращает базовую кратность уборок для типа комнаты."""
    room_type_lower = room_type.lower() if room_type else "default"
    for k, v in DEFAULT_FREQUENCY_PER_DAY.items():
        if k in room_type_lower:
            return v
    return DEFAULT_FREQUENCY_PER_DAY["default"]
