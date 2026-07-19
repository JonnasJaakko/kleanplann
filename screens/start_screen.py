import os, glob
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QInputDialog
from PySide6.QtCore import Qt
from project import Project

PROJECTS_DIR = "projects"

class StartScreen(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Добро пожаловать в KleanPlann"))
        layout.addWidget(QLabel("Недавние проекты:"))
        self.project_list = QListWidget()
        layout.addWidget(self.project_list)
        btn_new = QPushButton("Новый проект")
        btn_new.clicked.connect(self.new_project)
        btn_open = QPushButton("Открыть проект")
        btn_open.clicked.connect(self.open_project)
        btn_norms = QPushButton("Нормативы СанПиН")
        btn_norms.clicked.connect(lambda: self.main_window.navigate(4))
        btn_inspect = QPushButton("Инспектор DXF")
        btn_inspect.clicked.connect(self.inspect_dxf_file)
        layout.addWidget(btn_new)
        layout.addWidget(btn_open)
        layout.addWidget(btn_norms)
        layout.addWidget(btn_inspect)
        self.refresh_project_list()

    def refresh_project_list(self):
        self.project_list.clear()
        files = glob.glob(os.path.join(PROJECTS_DIR, "*.json"))
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:10]:
            name = os.path.basename(f).replace('.json', '')
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, f)
            self.project_list.addItem(item)

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Название помещения:")
        if ok and name:
            self.main_window.project = Project(name)
            self.main_window.current_project_path = None
            self.main_window.plan_screen.load_plan_screen()
            self.main_window.navigate(1)

    def open_project(self):
        item = self.project_list.currentItem()
        if item:
            path = item.data(Qt.UserRole)
            self.main_window.project = Project.load_from_file(path)
            self.main_window.current_project_path = path
            self.main_window.plan_screen.load_plan_screen()
            self.main_window.navigate(1)
        else:
            QMessageBox.warning(self, "Ошибка", "Выберите проект из списка.")

    def inspect_dxf_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите DXF для анализа", "", "DXF Files (*.dxf)")
        if not path: return
        try:
            import ezdxf
            from collections import Counter
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            layers = set()
            type_counts = Counter()
            for e in msp:
                layers.add(e.dxf.layer)
                type_counts[e.dxftype()] += 1
            insunits = doc.header.get("$INSUNITS", 0)
            units_map = {0: "не заданы", 1: "дюймы", 4: "мм", 5: "см", 6: "м"}
            units_str = units_map.get(insunits, f"код {insunits}")
            info = f"<h3>Инспекция DXF: {os.path.basename(path)}</h3>"
            info += f"<p><b>Единицы чертежа (INSUNITS):</b> {units_str}</p>"
            info += "<h4>Слои:</h4><ul>" + "".join(f"<li>{layer}</li>" for layer in sorted(layers)) + "</ul>"
            info += "<h4>Типы объектов:</h4><ul>"
            for t, cnt in type_counts.most_common():
                info += f"<li>{t}: {cnt}</li>"
            info += "</ul>"
            info += "<p>На основе этих данных настройте параметры в config загрузки DXF.</p>"
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
            dlg = QDialog(self)
            dlg.setWindowTitle("Инспектор DXF")
            dlg.resize(500, 600)
            layout = QVBoxLayout(dlg)
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setHtml(info)
            layout.addWidget(text_edit)
            btn_close = QPushButton("Закрыть")
            btn_close.clicked.connect(dlg.close)
            layout.addWidget(btn_close)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать DXF: {e}")