from __future__ import annotations

import logging

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

DARK = {
    "bg": "#17181C",
    "panel": "#20222A",
    "panel_alt": "#262832",
    "border": "#333642",
    "text": "#E9EAEE",
    "muted": "#9AA0AE",
    "accent": "#4F8EF7",
    "accent_hover": "#6AA1FF",
    "danger": "#E05C5C",
    "success": "#4CC38A",
    "selection": "#3B6FD4",
}

LIGHT = {
    "bg": "#F5F6F8",
    "panel": "#FFFFFF",
    "panel_alt": "#EEF0F4",
    "border": "#D9DCE3",
    "text": "#1D1F24",
    "muted": "#6B7280",
    "accent": "#2F6FE4",
    "accent_hover": "#4A84F0",
    "danger": "#C94B4B",
    "success": "#2E9E6B",
    "selection": "#C9DBFF",
}


def is_dark_theme() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return True


def colors(dark: bool | None = None) -> dict[str, str]:
    if dark is None:
        dark = is_dark_theme()
    return DARK if dark else LIGHT


def build_stylesheet(dark: bool | None = None) -> str:
    c = colors(dark)
    return f"""
    QDialog, QMainWindow, QMessageBox {{
        background-color: {c['bg']};
        color: {c['text']};
    }}
    QDialog {{
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QLabel {{
        color: {c['text']};
        background: transparent;
    }}
    QLabel#muted {{
        color: {c['muted']};
    }}
    QLabel#title {{
        font-size: 15pt;
        font-weight: 600;
    }}
    QLabel#error {{
        color: {c['danger']};
    }}
    QGroupBox {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        margin-top: 14px;
        padding: 12px 12px 10px 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
        color: {c['muted']};
        font-weight: 600;
    }}
    QPushButton {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 6px 12px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        border-color: {c['accent']};
    }}
    QPushButton:pressed {{
        background-color: {c['border']};
    }}
    QPushButton:disabled {{
        color: {c['muted']};
        background-color: {c['panel']};
    }}
    QPushButton#primaryButton {{
        background-color: {c['accent']};
        color: #FFFFFF;
        border: 1px solid {c['accent']};
        font-weight: 600;
    }}
    QPushButton#primaryButton:hover {{
        background-color: {c['accent_hover']};
    }}
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
        border-color: {c['accent']};
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
        outline: none;
    }}
    QCheckBox {{
        color: {c['text']};
        spacing: 8px;
    }}
    QProgressBar {{
        background-color: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 5px;
        text-align: center;
        color: {c['text']};
        min-height: 14px;
    }}
    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 4px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: {c['bg']};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QMenu {{
        background-color: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 22px 6px 12px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {c['selection']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {c['border']};
        margin: 4px 8px;
    }}
    QToolTip {{
        background-color: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        border-radius: 6px;
        background: {c['panel']};
    }}
    QTabBar::tab {{
        background: {c['panel_alt']};
        color: {c['muted']};
        padding: 7px 14px;
        border: 1px solid {c['border']};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        color: {c['text']};
        background: {c['panel']};
    }}
    """


def apply_theme(app: QApplication, dark: bool | None = None) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(build_stylesheet(dark))
