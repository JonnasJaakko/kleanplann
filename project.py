import json, os
from datetime import datetime, date, timedelta
from typing import List, Tuple

class Wall:
    def __init__(self, x1: float, y1: float, x2: float, y2: float):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
    def to_dict(self): return [self.x1, self.y1, self.x2, self.y2]
    @classmethod
    def from_dict(cls, data): return cls(*data)

class Room:
    def __init__(self, room_id, polygon_points, area_m2=0.0, traffic=10,
                 color=(255,0,0,50), room_type="", name="", floor_index=0):
        self.id = room_id
        self.points = polygon_points
        self.area_m2 = area_m2
        self.traffic = traffic
        self.color = color
        self.room_type = room_type
        self.name = name if name else f"Комната {room_id+1}"
        self.floor_index = int(floor_index)
        self.priority = False
        self.disabled = False
    def to_dict(self):
        return {'id':self.id, 'points':self.points, 'area_m2':self.area_m2,
                'traffic':self.traffic, 'color':self.color, 'room_type':self.room_type,
                'name':self.name, 'floor_index':self.floor_index,
                'priority':self.priority, 'disabled':self.disabled}
    @classmethod
    def from_dict(cls, data):
        r = cls(data['id'], data['points'], data.get('area_m2', 0.0), data.get('traffic', 10),
                tuple(data.get('color', (255,0,0,50))), data.get('room_type', ""),
                data.get('name', ""), data.get('floor_index', 0))
        r.priority = data.get('priority', False)
        r.disabled = data.get('disabled', False)
        return r

class Floor:
    def __init__(self, index=0, name="Этаж 1"):
        self.index = index
        self.name = name
        self.walls = []
        self.rooms = []
        self.total_area_m2 = 0.0
        self.image_path = ""
    def to_dict(self):
        return {'index': self.index, 'name': self.name,
                'walls':[w.to_dict() for w in self.walls],
                'rooms':[r.to_dict() for r in self.rooms],
                'total_area_m2':self.total_area_m2, 'image_path':self.image_path}
    @classmethod
    def from_dict(cls, data):
        f = cls(data.get('index',0), data.get('name','Этаж 1'))
        f.walls = [Wall.from_dict(w) for w in data.get('walls', [])]
        f.rooms = [Room.from_dict(r) for r in data.get('rooms', [])]
        for r in f.rooms: r.floor_index = f.index
        f.total_area_m2 = float(data.get('total_area_m2', 0.0))
        f.image_path = data.get('image_path', '')
        return f

class Zone:
    def __init__(self, zone_id, name, room_ids, color=(255,0,0,100), employee_index=0, floor_index=0):
        self.id = zone_id; self.name = name; self.room_ids = list(room_ids or [])
        self.color = color; self.employee_index = employee_index; self.floor_index = floor_index
    def to_dict(self):
        return {'id':self.id,'name':self.name,'room_ids':self.room_ids,
                'color':self.color,'employee_index':self.employee_index,'floor_index':self.floor_index}
    @classmethod
    def from_dict(cls, data):
        return cls(data.get('id',0), data.get('name',''), data.get('room_ids',[]),
                   tuple(data.get('color',(255,0,0,100))), data.get('employee_index',0),
                   data.get('floor_index',0))

class Shift:
    def __init__(self, name, start_time, end_time):
        self.name=name; self.start_time=start_time; self.end_time=end_time

class CleaningTask:
    def __init__(self, room_id, floor_index, start_dt, end_dt, employee=0,
                 is_overtime=False, transit_after_minutes=0, priority=False,
                 occurrence=0, fixed=False, user_preferred_start=None, release_minute=0):
        self.room_id=room_id; self.floor_index=floor_index
        self.start_dt=start_dt; self.end_dt=end_dt; self.employee=employee
        self.is_overtime=bool(is_overtime)
        self.transit_after_minutes=int(max(0, transit_after_minutes))
        self.transit_before_minutes=0
        self.priority=bool(priority)
        self.occurrence=int(occurrence)
        self.fixed=bool(fixed)
        self.user_preferred_start=user_preferred_start
        self.release_minute=int(release_minute or 0)

class Project:
    def __init__(self, name="Новый проект"):
        self.name=name
        self.image_paths=[]
        self.floors=[Floor(0,"Этаж 1")]
        self.current_floor_index=0
        self.zones=[]
        self.manual_assignments={}
        self.employees_count=1
        self.employee_names=["Сотрудник 1"]
        # Новая модель оплаты.
        self.salary_type="hour"       # fixed_shift | per_sqm | hour
        self.salary_value=200.0
        self.hourly_rate=200.0         # legacy compatibility
        self.overtime_type="percent"  # percent | per_hour
        self.overtime_value=50.0
        self.overtime_premium_percent=50.0
        self.overtime_limit="23:00"
        self.cleaning_type="поддерживающая"
        self.total_area_m2=0.0
        self.calibration_line=None
        self.shifts=[Shift("Основная","08:00","17:00")]
        self.breaks=[("12:00","13:00")]
        self.cleaning_tasks=[]
        # Настройки ручных правок расписания. Ключ: "floor:room:occurrence".
        # Значения сохраняются в проекте, чтобы пользовательские предпочтения
        # переживали повторный пересчёт расписания.
        self.schedule_locks={}
        self.start_date=date.today(); self.end_date=date.today()+timedelta(days=7)
        self.weather_factor=1.0
        self.priority_mode="balanced"
        self.is_dxf_loaded=False
        self.created_date=datetime.now().isoformat(); self.last_modified=self.created_date
        self._project_dir = ""
    @property
    def walls(self): return self.current_floor.walls
    @walls.setter
    def walls(self,v): self.current_floor.walls=v
    @property
    def rooms(self): return self.current_floor.rooms
    @rooms.setter
    def rooms(self,v): self.current_floor.rooms=v
    @property
    def current_floor(self): return self.floors[self.current_floor_index]
    def all_rooms(self):
        result=[]
        for fi,f in enumerate(self.floors):
            f.index=fi
            for r in f.rooms: r.floor_index=fi
            result.extend(f.rooms)
        return result
    def to_dict(self):
        return {'name':self.name,'image_paths':self.image_paths,
                'floors':[f.to_dict() for f in self.floors],
                'current_floor_index':self.current_floor_index,
                'zones':[z.to_dict() for z in self.zones], 'manual_assignments':self.manual_assignments,
                'employees_count':self.employees_count,'employee_names':self.employee_names,
                'salary_type':self.salary_type,'salary_value':self.salary_value,
                'hourly_rate':self.hourly_rate,'overtime_type':self.overtime_type,
                'overtime_value':self.overtime_value,'overtime_premium_percent':self.overtime_premium_percent,
                'overtime_limit':self.overtime_limit,
                'cleaning_type':self.cleaning_type,'total_area_m2':self.total_area_m2,
                'schedule_locks':self.schedule_locks,
                'calibration_line':self.calibration_line,
                'shifts':[{'name':s.name,'start':s.start_time,'end':s.end_time} for s in self.shifts],
                'breaks':[list(b) for b in self.breaks],
                'start_date':self.start_date.isoformat(),'end_date':self.end_date.isoformat(),
                'weather_factor':self.weather_factor,'priority_mode':self.priority_mode,
                'is_dxf_loaded':self.is_dxf_loaded,'created_date':self.created_date,
                'last_modified':datetime.now().isoformat()}
    @classmethod
    def from_dict(cls,data):
        p=cls(data.get('name','Новый проект')); p.image_paths=data.get('image_paths',[])
        if data.get('floors'):
            p.floors=[Floor.from_dict(f) for f in data['floors']]
        else:
            f=Floor(0,'Этаж 1'); f.walls=[Wall.from_dict(w) for w in data.get('walls',[])]; f.rooms=[Room.from_dict(r) for r in data.get('rooms',[])]; p.floors=[f]
        for fi,f in enumerate(p.floors):
            f.index=fi
            for r in f.rooms: r.floor_index=fi
        p.current_floor_index=min(max(0,data.get('current_floor_index',0)),max(0,len(p.floors)-1))
        p.zones=[Zone.from_dict(z) for z in data.get('zones',[])]
        p.manual_assignments={str(k):int(v) for k,v in data.get('manual_assignments',{}).items()}
        p.employees_count=int(data.get('employees_count',1)); p.employee_names=data.get('employee_names',[f'Сотрудник {i+1}' for i in range(p.employees_count)])
        p.salary_type=data.get('salary_type','hour'); p.salary_value=float(data.get('salary_value',data.get('hourly_rate',200.0)))
        p.hourly_rate=float(data.get('hourly_rate',p.salary_value))
        p.overtime_type=data.get('overtime_type','percent'); p.overtime_value=float(data.get('overtime_value',data.get('overtime_premium_percent',50.0)))
        p.overtime_premium_percent=float(data.get('overtime_premium_percent',p.overtime_value if p.overtime_type=='percent' else 0.0))
        p.overtime_limit=str(data.get('overtime_limit','23:00'))
        p.cleaning_type=data.get('cleaning_type','поддерживающая'); p.total_area_m2=float(data.get('total_area_m2',sum(f.total_area_m2 for f in p.floors)))
        raw_locks=data.get('schedule_locks',{}) or {}
        p.schedule_locks={}
        if isinstance(raw_locks, dict):
            for key, value in raw_locks.items():
                if not isinstance(value, dict):
                    continue
                item=dict(value)
                item['employee']=int(item.get('employee',0))
                item['fixed']=bool(item.get('fixed',False))
                if item.get('start') is not None:
                    item['start']=str(item.get('start'))
                p.schedule_locks[str(key)]=item
        p.calibration_line=data.get('calibration_line')
        if data.get('shifts'): p.shifts=[Shift(s.get('name','Основная'),s.get('start','08:00'),s.get('end','17:00')) for s in data['shifts']]
        p.breaks=[(str(b[0]),str(b[1])) for b in data.get('breaks',[('12:00','13:00')])]
        try: p.start_date=date.fromisoformat(data.get('start_date',date.today().isoformat()))
        except: p.start_date=date.today()
        try: p.end_date=date.fromisoformat(data.get('end_date',(date.today()+timedelta(days=7)).isoformat()))
        except: p.end_date=date.today()+timedelta(days=7)
        p.weather_factor=float(data.get('weather_factor',1.0)); p.priority_mode=data.get('priority_mode','balanced'); p.is_dxf_loaded=data.get('is_dxf_loaded',False)
        p.created_date=data.get('created_date',datetime.now().isoformat()); p.last_modified=data.get('last_modified',p.created_date)
        return p
    def save_to_file(self,filepath):
        with open(filepath,'w',encoding='utf-8') as f: json.dump(self.to_dict(),f,ensure_ascii=False,indent=2)
        self._project_dir = os.path.dirname(os.path.abspath(filepath))
    @classmethod
    def load_from_file(cls,filepath):
        with open(filepath,'r',encoding='utf-8') as f:
            project = cls.from_dict(json.load(f))
        project._project_dir = os.path.dirname(os.path.abspath(filepath))
        return project
