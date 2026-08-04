import sys
from PySide6.QtWidgets import QApplication
from app import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    try:
        with open("styles.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except FileNotFoundError:
        print("Файл стилей не найден, используется стандартное оформление.")
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()