"""
widgets.py — Utilidades de presentación y componentes UI personalizados.
"""

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QLineEdit
from core.config import C

# ---------------------------------------------------------------------------
# Helpers de estilo Qt
# ---------------------------------------------------------------------------

def _css_btn(bg: str, fg: str = "#f0f0f0") -> str:
    hover: str = QColor(bg).darker(120).name()
    return (
        f"QPushButton {{"
        f"  background:{bg}; color:{fg}; border:none;"
        f"  font-family:Courier; font-size:10pt; font-weight:bold;"
        f"  padding:6px 10px; border-radius:3px;"
        f"}}"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:disabled {{ background:#aaaaaa; color:#dddddd; }}"
    )

def _css_entry() -> str:
    return (
        f"QLineEdit {{"
        f"  background:{C['bg']}; color:{C['text']};"
        f"  border:1px solid {C['border']}; border-radius:2px;"
        f"  font-family:Courier; font-size:10pt; padding:2px 4px;"
        f"}}"
        f"QLineEdit:focus {{ border-color:{C['accent']}; }}"
    )

def _css_combo() -> str:
    return (
        f"QComboBox {{"
        f"  background:{C['bg']}; color:{C['text']};"
        f"  border:1px solid {C['border']}; border-radius:2px;"
        f"  font-family:Courier; font-size:10pt; padding:2px 4px;"
        f"}}"
    )

# ---------------------------------------------------------------------------
# Widget campo etiqueta + entrada
# ---------------------------------------------------------------------------

class _Campo(QWidget):
    def __init__(self, etiqueta: str, valor: str, solo_lectura: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 2, 14, 2)
        
        lbl = QLabel(etiqueta)
        lbl.setFixedWidth(160)
        lbl.setStyleSheet(f"color:{C['text_dim']}; font-family:Courier; font-size:9pt;")
        
        self.entry = QLineEdit(valor)
        self.entry.setStyleSheet(_css_entry())
        self.entry.setFixedWidth(110)
        
        if solo_lectura:
            self.entry.setReadOnly(True)
            
        lay.addWidget(lbl)
        lay.addWidget(self.entry)
        lay.addStretch()

    def get(self) -> str:
        return self.entry.text()

    def set(self, valor: str) -> None:
        self.entry.setText(valor)