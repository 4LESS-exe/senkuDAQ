"""
main.py — Entry point de SENKU DAQ (PyQt6 + PyQtGraph).
Dependencias (instalar con pip):
    PyQt6
    pyqtgraph
    numpy
    pyserial

main.py — Entry point de SENKU DAQ.
Ahora actúa como orquestador: levanta el backend FastAPI en un proceso 
paralelo y luego inicia el cliente PyQt6.
"""

import sys
import multiprocessing
import uvicorn
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

def run_backend():
    """Función que ejecuta el servidor FastAPI."""
    # Deshabilita los logs de acceso de uvicorn para no ensuciar la consola de la GUI
    uvicorn.run("api.server:app", host="0.0.0.0", port=8765, log_level="warning")

if __name__ == "__main__":
    # Necesario en Windows para evitar loops infinitos al usar multiprocessing
    multiprocessing.freeze_support()

    print("[i] Iniciando Backend REST (localhost:8765)...")
    api_process = multiprocessing.Process(target=run_backend, daemon=True)
    api_process.start()

    # Iniciar la aplicación PyQt6
    app = QApplication(sys.argv)
    app.setFont(QFont("Courier", 10))
    
    from legacy_ui.app import AppDAQ
    window = AppDAQ()
    window.show()

    # Ejecutar loop de la GUI y al salir, matar el backend
    exit_code = app.exec()
    print("[i] Cerrando aplicación y deteniendo backend...")
    api_process.terminate()
    sys.exit(exit_code)
