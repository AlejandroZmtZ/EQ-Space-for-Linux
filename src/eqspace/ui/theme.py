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
"""
