from dxf_importer import import_dxf

class DxfService:
    @staticmethod
    def import_dxf(filepath, progress_callback=None):
        return import_dxf(filepath, progress_callback)