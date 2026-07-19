from zone_manager import manual_distribution

class ZoneService:
    @staticmethod
    def distribute(rooms, percentages):
        return manual_distribution(rooms, percentages)