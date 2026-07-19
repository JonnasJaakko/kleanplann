import json, os
from datetime import datetime, date, timedelta
from typing import List, Tuple, Dict, Any, Optional

class Wall:
    def __init__(self, x1: float, y1: float, x2: float, y2: float):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
    def to_dict(self): return [self.x1, self.y1, self.x2, self.y2]
    @classmethod
    def from_dict(cls, data): return cls(*data)

class Room:
    def __init__(self, room_id: int, polygon_points: List[Tuple[float, float]],
                 area_m2: float = 0.0, traffic: int = 10,
                 color: Tuple[int,int,int,int] = (255,0,0,50),
                 room_type: str = "", name: str = ""):
        self.id = room_id
        self.points = polygon_points
        self.area_m2 = area_m2
        self.traffic = traffic
        self.color = color
        self.room_type = room_type
        self.name = name if name else f"Комната {room_id+1}"
    def to_dict(self):
        return {'id':self.id, 'points':self.points, 'area_m2':self.area_m2,
                'traffic':self.traffic, 'color':self.color, 'room_type':self.room_type,
                'name':self.name}
    @classmethod
    def from_dict(cls, data):
        return cls(data['id'], data['points'], data['area_m2'], data['traffic'],
                   tuple(data.get('color', (255,0,0,50))),
                   data.get('room_type', ""), data.get('name', ""))

class Floor:
    def __init__(self, index: int = 0, name: str = "Этаж 1"):
        self.index = index
        self.name = name
        self.walls: List[Wall] = []
        self._cached_rooms: List[Room] = []
        self._dirty = True          # True = нужно перестроить комнаты
        self.total_area_m2: float = 0.0

    @property
    def rooms(self):
        """Возвращает комнаты, перестраивая их при необходимости."""
        if self._dirty:
            # Здесь будет вызываться build_rooms_for_floor из MainWindow.
            # Чтобы избежать циклического импорта, используем ленивый вызов.
            # Сам метод build_rooms_for_floor будет установлен извне (в app.py).
            if hasattr(self, '_builder'):
                self._builder(self)
            else:
                # Запасной вариант (если билдер не назначен)
                pass
            self._dirty = False
        return self._cached_rooms

    @rooms.setter
    def rooms(self, value):
        self._cached_rooms = value
        self._dirty = False

    def mark_dirty(self):
        """Помечает, что стены изменились и комнаты нужно пересчитать."""
        self._dirty = True

    def to_dict(self):
        return {
            'index': self.index,
            'name': self.name,
            'walls': [w.to_dict() for w in self.walls],
            'rooms': [r.to_dict() for r in self._cached_rooms],
            'total_area_m2': self.total_area_m2
        }

    @classmethod
    def from_dict(cls, data):
        f = cls(data['index'], data['name'])
        f.walls = [Wall.from_dict(w) for w in data.get('walls', [])]
        f.rooms = [Room.from_dict(r) for r in data.get('rooms', [])]
        f.total_area_m2 = data.get('total_area_m2', 0.0)
        return f

class Zone:
    def __init__(self, zone_id: int, name: str, room_ids: List[int],
                 color: Tuple[int,int,int,int]=(255,0,0,100), employee_index: int=0,
                 floor_index: int = 0):
        self.id=zone_id; self.name=name; self.room_ids=room_ids
        self.color=color; self.employee_index=employee_index; self.floor_index=floor_index
    def to_dict(self):
        return {'id':self.id,'name':self.name,'room_ids':self.room_ids,
                'color':self.color,'employee_index':self.employee_index,
                'floor_index':self.floor_index}
    @classmethod
    def from_dict(cls, data):
        return cls(data['id'], data['name'], data['room_ids'],
                   tuple(data['color']), data['employee_index'],
                   data.get('floor_index', 0))

class Shift:
    def __init__(self, name: str, start_time: str, end_time: str):
        self.name = name; self.start_time = start_time; self.end_time = end_time

class CleaningTask:
    def __init__(self, room_id: int, floor_index: int, start_dt: datetime, end_dt: datetime, employee: int = 0):
        self.room_id = room_id; self.floor_index = floor_index
        self.start_dt = start_dt; self.end_dt = end_dt; self.employee = employee

class Project:
    def __init__(self, name:str="Новый проект"):
        self.name=name
        self.image_paths: List[str] = []
        self.floors: List[Floor] = [Floor(0, "Этаж 1")]
        self.current_floor_index = 0
        self.zones: List[Zone] = []
        self.employees_count = 1
        self.employee_names: List[str] = ["Сотрудник 1"]
        self.hourly_rate = 200.0
        self.total_area_m2 = 0.0
        self.calibration_line = None
        self.shifts: List[Shift] = [
            Shift("Утро", "08:00", "12:00"),
            Shift("День", "13:00", "17:00")
        ]
        self.cleaning_tasks: List[CleaningTask] = []
        self.start_date = date.today()
        self.end_date = date.today() + timedelta(days=7)
        self.weather_factor = 1.0
        self.priority_mode = "area"
        self.is_dxf_loaded = False
        self.created_date = datetime.now().isoformat()
        self.last_modified = self.created_date

    @property
    def walls(self):
        return self.current_floor.walls
    @walls.setter
    def walls(self, value):
        self.current_floor.walls = value
    @property
    def rooms(self):
        return self.current_floor.rooms
    @rooms.setter
    def rooms(self, value):
        self.current_floor.rooms = value
    @property
    def current_floor(self):
        return self.floors[self.current_floor_index]
    def all_rooms(self) -> List[Room]:
        rooms = []
        for floor in self.floors:
            rooms.extend(floor.rooms)
        return rooms

    def to_dict(self):
        return {
            'name': self.name,
            'image_paths': self.image_paths,
            'floors': [f.to_dict() for f in self.floors],
            'current_floor_index': self.current_floor_index,
            'zones': [z.to_dict() for z in self.zones],
            'employees_count': self.employees_count,
            'employee_names': self.employee_names,
            'hourly_rate': self.hourly_rate,
            'total_area_m2': self.total_area_m2,
            'calibration_line': self.calibration_line,
            'shifts': [{'name': s.name, 'start': s.start_time, 'end': s.end_time} for s in self.shifts],
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'weather_factor': self.weather_factor,
            'priority_mode': self.priority_mode,
            'is_dxf_loaded': self.is_dxf_loaded,
            'created_date': self.created_date,
            'last_modified': datetime.now().isoformat()
        }

    @classmethod
    def from_dict(cls, data):
        p = cls(data['name'])
        p.image_paths = data.get('image_paths', [])
        if 'floors' in data:
            p.floors = [Floor.from_dict(f) for f in data['floors']]
        else:
            # обратная совместимость (старые проекты)
            old_walls = [Wall.from_dict(w) for w in data.get('walls', [])]
            old_rooms = [Room.from_dict(r) for r in data.get('rooms', [])]
            p.floors = [Floor(0, "Этаж 1")]
            p.floors[0].walls = old_walls
            p.floors[0].rooms = old_rooms
        p.current_floor_index = data.get('current_floor_index', 0)
        p.zones = [Zone.from_dict(z) for z in data.get('zones', [])]
        p.employees_count = data.get('employees_count', 1)
        p.employee_names = data.get('employee_names', [f"Сотрудник {i+1}" for i in range(p.employees_count)])
        p.hourly_rate = data.get('hourly_rate', 200.0)
        p.total_area_m2 = data.get('total_area_m2', 0.0)
        p.calibration_line = data.get('calibration_line')
        if 'shifts' in data:
            p.shifts = [Shift(s['name'], s['start'], s['end']) for s in data['shifts']]
        if 'start_date' in data:
            p.start_date = date.fromisoformat(data['start_date'])
        if 'end_date' in data:
            p.end_date = date.fromisoformat(data['end_date'])
        p.weather_factor = data.get('weather_factor', 1.0)
        p.priority_mode = data.get('priority_mode', 'area')
        p.is_dxf_loaded = data.get('is_dxf_loaded', False)
        p.created_date = data.get('created_date', datetime.now().isoformat())
        p.last_modified = data.get('last_modified', p.created_date)
        return p

    def save_to_file(self, filepath: str):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls, filepath: str):
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)