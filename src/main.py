"""Entry point of the desktop application."""

import sys


def main() -> None:
    # Imported here so the non-GUI parts stay usable without a display.
    from PySide6.QtWidgets import QApplication

    from src.config import build_service, load_settings
    from src.gui.main_window import MainWindow
    from src.zammad.client import ZammadClient

    app = QApplication(sys.argv)
    settings = load_settings()
    zammad = ZammadClient(settings.zammad.url, settings.zammad.token) if settings.zammad else None
    window = MainWindow(settings, build_service(settings), zammad)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
