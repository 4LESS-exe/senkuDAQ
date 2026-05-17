"""
main.py — Entry point de SENKU DAQ (PyQt6 + PyQtGraph).

Estructura del proyecto:
    senku_daq/
    ├── main.py           ← aquí estás
    ├── config.py         ← constantes, colores, persistencia de config
    ├── utils.py          ← funciones utilitarias (puertos, estadística)
    ├── serial_reader.py  ← hilo de lectura serial (LectorSerial)
    ├── state.py          ← máquina de estados del ensayo (EstadoEnsayo)
    ├── data_export.py    ← exportación CSV / ENG / PNG
    └── app.py            ← ventana principal PyQt6 + PyQtGraph (AppDAQ)

Dependencias (instalar con pip):
    PyQt6
    pyqtgraph
    numpy
    pyserial
"""

import sys
from PyQt6.QtWidgets import QApplication
from ui.app import AppDAQ

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Fuente monospace global coherente con el diseño original
    from PyQt6.QtGui import QFont
    app.setFont(QFont("Courier", 10))
    window = AppDAQ()
    window.show()
    sys.exit(app.exec())
