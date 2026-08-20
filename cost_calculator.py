"""Единый расчёт трудоёмкости и оплаты персонала."""
import copy, math
from typing import Dict, Any
from sanitarnorm import get_cleaning_time_minutes, get_frequency_per_day

def _freq(project, room):
    return max(1, int(round(get_frequency_per_day(room.room_type) * getattr(project,'weather_factor',1.0))))

def _time(project, room):
    return max(1, int(math.ceil(get_cleaning_time_minutes(room.room_type, room.area_m2, getattr(project,'weather_factor',1.0), getattr(project,'cleaning_type','поддерживающая')))))

def _shift_minutes(project):
    if not project.shifts: return 480
    s=project.shifts[0]; a=list(map(int,s.start_time.split(':'))); b=list(map(int,s.end_time.split(':')))
    start=a[0]*60+a[1]; end=b[0]*60+b[1]
    total=max(0,end-start)
    for bs,be in getattr(project,'breaks',[]) or []:
        try:
            x,y=map(int,bs.split(':')); u,v=map(int,be.split(':')); total-=max(0,min(end,u*60+v)-max(start,x*60+y))
        except: pass
    return max(1,total)

def _daily_norm_minutes(project):
    return sum(_time(project,r)*_freq(project,r) for r in project.all_rooms() if not getattr(r,'disabled',False))

def estimate_required_employees(project):
    daily=_daily_norm_minutes(project); cap=_shift_minutes(project)
    n=max(1,math.ceil(daily/(cap*0.9))) if daily else 1
    return {'employees':n,'daily_minutes':round(daily,1),'capacity_minutes':round(cap*0.9,1)}

def _room(project,fi,rid):
    if 0<=fi<len(project.floors):
        for r in project.floors[fi].rooms:
            if r.id==rid:return r
    return None

def _employee_metrics(project):
    result={i:{'tasks':[],'minutes':0.0,'overtime':0.0,'area':0.0,'rooms':set()} for i in range(project.employees_count)}
    for t in project.cleaning_tasks or []:
        m=result.setdefault(t.employee,{'tasks':[],'minutes':0.0,'overtime':0.0,'area':0.0,'rooms':set()}); m['tasks'].append(t); m['minutes']+=(t.end_dt-t.start_dt).total_seconds()/60
        m['rooms'].add((t.floor_index,t.room_id))
        if t.is_overtime:m['overtime']+=(t.end_dt-t.start_dt).total_seconds()/60
    for i,m in result.items():
        m['area']=sum((_room(project,fi,rid).area_m2 if _room(project,fi,rid) else 0) for fi,rid in m['rooms'])
    return result

def _base_pay(project,m):
    typ=getattr(project,'salary_type','hour'); value=float(getattr(project,'salary_value',getattr(project,'hourly_rate',200)))
    if typ=='fixed_shift': return value
    if typ=='per_sqm': return value*m['area']
    return value*(m['minutes']/60.0 - m['overtime']/60.0)

def calculate_cost(project):
    tasks=list(getattr(project,'cleaning_tasks',[]) or [])
    if tasks:
        cleaning_minutes=sum((t.end_dt-t.start_dt).total_seconds()/60 for t in tasks)
        overtime_minutes=sum((t.end_dt-t.start_dt).total_seconds()/60 for t in tasks if t.is_overtime)
        transit_minutes=sum(max(0,int(getattr(t,'transit_after_minutes',0))) for t in tasks)
    else:
        cleaning_minutes=_daily_norm_minutes(project); overtime_minutes=0; transit_minutes=0
    total_minutes=cleaning_minutes+transit_minutes
    shift_hours=_shift_minutes(project)/60
    empm=_employee_metrics(project) if tasks else {i:{'minutes':0,'overtime':0,'area':sum(r.area_m2 for z in project.zones if z.employee_index==i for r in project.all_rooms() if r.id in z.room_ids),'rooms':set(),'tasks':[]} for i in range(project.employees_count)}
    premium_type=getattr(project,'overtime_type','percent'); premium_value=float(getattr(project,'overtime_value',getattr(project,'overtime_premium_percent',50)))
    base_total=0; overtime_pay=0
    for i in range(project.employees_count):
        m=empm.get(i,{'minutes':0,'overtime':0,'area':0,'rooms':set(),'tasks':[]})
        base=_base_pay(project,m)
        base_total+=base
        ot_h=m['overtime']/60
        if ot_h:
            if premium_type=='per_hour': overtime_pay += ot_h*premium_value
            else:
                hourly_equiv=(base/shift_hours) if shift_hours else 0
                overtime_pay += ot_h*hourly_equiv*(1+premium_value/100.0)
    # Для почасовой оплаты базовая сумма уже не содержит overtime; для остальных — базовая часть оклада.
    if getattr(project,'salary_type','hour')=='hour':
        regular_pay=base_total
        if premium_type=='per_hour': overtime_pay += overtime_minutes/60*0
        else:
            # base_total is regular hours pay; overtime receives hourly equivalent * (1+premium)
            hourly=float(getattr(project,'salary_value',200))
            overtime_pay= overtime_minutes/60*(hourly*(1+premium_value/100.0) if premium_type=='percent' else hourly+premium_value)
        cost_with_overtime=regular_pay+overtime_pay
    else:
        cost_with_overtime=base_total+overtime_pay
    needed=estimate_required_employees(project)['employees']
    if project.salary_type=='hour':
        staff_full=project.employees_count*shift_hours*float(getattr(project,'salary_value',200))
        hire_cost=needed*shift_hours*float(getattr(project,'salary_value',200))
    elif project.salary_type=='fixed_shift':
        staff_full=project.employees_count*float(getattr(project,'salary_value',0)); hire_cost=needed*float(getattr(project,'salary_value',0))
    else:
        total_active_area=sum(r.area_m2 for r in project.all_rooms() if not getattr(r,'disabled',False)); staff_full=total_active_area*float(getattr(project,'salary_value',0)); hire_cost=staff_full
    employee_pay={}
    for i,m in empm.items():
        base=_base_pay(project,m); ot=m['overtime']/60
        if premium_type=='per_hour': extra=ot*premium_value
        else: extra=ot*(base/shift_hours if shift_hours else 0)*(1.0+premium_value/100.0)
        employee_pay[i]=round(base+extra,2)
    return {'total_time_hours':round(total_minutes/60,2),'total_minutes':round(total_minutes,1),'staff_count':project.employees_count,'staff_hours':round(project.employees_count*shift_hours,2),'overtime_hours':round(overtime_minutes/60,2),'cost_with_overtime':round(cost_with_overtime,2),'needed_employees':needed,'cost_hire':round(hire_cost,2),'recommendation':('нанять ещё %d чел.'%(needed-project.employees_count) if needed>project.employees_count else ('сократить штат до %d'%(needed) if needed<project.employees_count else 'оставить штат')),'salary_type':getattr(project,'salary_type','hour'),'salary_value':float(getattr(project,'salary_value',200)),'overtime_type':premium_type,'overtime_value':premium_value,'employee_metrics':empm,'employee_pay':employee_pay}
