"""Application-wide dark theme stylesheet."""

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1f24;
    color: #d8dade;
    font-size: 13px;
}
QTabWidget::pane { border: 1px solid #33353c; }
QTabBar::tab {
    background: #26272d;
    color: #a8abb3;
    padding: 6px 18px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #33353c; color: #ffffff; }
QTabBar::tab:hover:!selected { background: #2c2d33; }
QPushButton {
    background-color: #3a3d46;
    border: 1px solid #4a4d57;
    border-radius: 4px;
    padding: 5px 14px;
}
QPushButton:hover { background-color: #454854; }
QPushButton:pressed { background-color: #2f323a; }
QPushButton:disabled { color: #6a6d75; }
QSlider::groove:horizontal {
    height: 4px;
    background: #33353c;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: #5b9dff;
}
QComboBox, QDoubleSpinBox, QSpinBox {
    background-color: #26272d;
    border: 1px solid #3a3d46;
    border-radius: 3px;
    padding: 3px 6px;
}
QComboBox QAbstractItemView {
    background-color: #26272d;
    selection-background-color: #3a3d46;
}
QTableWidget {
    background-color: #232429;
    gridline-color: #33353c;
    border: 1px solid #33353c;
}
QHeaderView::section {
    background-color: #26272d;
    border: none;
    border-right: 1px solid #33353c;
    padding: 4px;
}
QLabel#sectionHeader {
    font-size: 14px;
    font-weight: bold;
    color: #ffffff;
}
QLabel#tabSubtitle {
    font-size: 12px;
    color: #94a3b8;
    padding: 0 0 4px 0;
}
QFrame#quickStartBanner {
    background-color: #1c2333;
    border: 1px solid #3b82f6;
    border-radius: 8px;
    padding: 10px 14px;
}
QLabel#quickStartHeader {
    font-size: 14px;
    font-weight: bold;
    color: #93c5fd;
}
QPushButton#quickStartDismissButton {
    background-color: transparent;
    border: 1px solid transparent;
    color: #94a3b8;
    font-size: 13px;
    font-weight: bold;
    padding: 2px 6px;
    border-radius: 4px;
}
QPushButton#quickStartDismissButton:hover {
    background-color: #2e3a50;
    border-color: #475569;
    color: #ffffff;
}
QFrame#quickStartStepCard {
    background-color: #141a27;
    border: 1px solid #233047;
    border-radius: 6px;
    padding: 8px 10px;
}
QLabel#quickStartStepTitle {
    font-size: 12px;
    font-weight: bold;
    color: #60a5fa;
}
QLabel#quickStartStepDesc {
    font-size: 11px;
    color: #cbd5e1;
}
QFrame#routingCard {
    background-color: #232834;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 6px;
}
QLabel#statusBadgeActive {
    background-color: #064e3b;
    color: #10b981;
    border: 1px solid #10b981;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: bold;
}
QLabel#statusBadgeInactive {
    background-color: #1f2937;
    color: #9ca3af;
    border: 1px solid #6b7280;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
}
QPushButton#statusBarHelpButton {
    background-color: #26272d;
    border: 1px solid #3a3d46;
    border-radius: 3px;
    padding: 2px 8px;
    font-size: 11px;
    color: #93c5fd;
}
QPushButton#statusBarHelpButton:hover {
    background-color: #33353c;
    border-color: #3b82f6;
    color: #ffffff;
}
"""
