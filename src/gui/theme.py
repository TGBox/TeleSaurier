"""Light / dark appearance, remembered between sessions."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication

SYSTEM, LIGHT, DARK = "system", "light", "dark"
LABELS = {SYSTEM: "Wie System", LIGHT: "Hell", DARK: "Dunkel"}
_SCHEMES = {
    SYSTEM: Qt.ColorScheme.Unknown,  # Unknown = follow the operating system
    LIGHT: Qt.ColorScheme.Light,
    DARK: Qt.ColorScheme.Dark,
}
_KEY = "appearance"


def saved_appearance() -> str:
    value = QSettings().value(_KEY, SYSTEM)
    return value if value in _SCHEMES else SYSTEM


def apply_appearance(appearance: str, *, remember: bool = True) -> None:
    QGuiApplication.styleHints().setColorScheme(_SCHEMES[appearance])
    if remember:
        QSettings().setValue(_KEY, appearance)
