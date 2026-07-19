from scheduler import plan_cleaning_schedule

class ScheduleService:
    @staticmethod
    def generate(project):
        return plan_cleaning_schedule(project)