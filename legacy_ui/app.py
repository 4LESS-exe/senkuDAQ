"""
app.py — Cliente PyQt6 para la API REST de SENKU DAQ.
Arquitectura Cliente/Servidor. Ya no procesa datos localmente.
"""

import time
import json
import concurrent.futures
import requests
import threading
import numpy as np
import pyqtgraph as pg
from collections import deque
from typing import Any, Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QColor, QFont, QCloseEvent
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, 
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget, QInputDialog
)

from core.config import (
    APP_VERSION, BUFFER_GRAFICO, TICK_MS, N_LECTURAS_CAL, C,
    cargar_config, guardar_config,
)
from legacy_ui.widgets import _Campo, _css_btn, _css_entry, _css_combo

# ---------------------------------------------------------------------------
# Configuración global
# ---------------------------------------------------------------------------
pg.setConfigOptions(antialias=True, background=C["plot_bg"], foreground=C["text_dim"])

API_URL = "http://127.0.0.1:8765/api/v1"

# ---------------------------------------------------------------------------
# HILO DE TELEMETRÍA (SSE CLIENT)
# ---------------------------------------------------------------------------

class SSEThread(QThread):
    """Hilo dedicado a escuchar Server-Sent Events sin bloquear la GUI."""
    muestra_recibida = pyqtSignal(float, float, int)  # t_s, empuje_n, señal_pct
    estado_cambiado = pyqtSignal(str, str)            # anterior, nuevo
    conexion_perdida = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._running = True

    def run(self):
        while self._running:
            try:
                with requests.get(f"{API_URL}/stream", stream=True, timeout=5) as r:
                    r.raise_for_status()
                    evento = ""
                    for line in r.iter_lines():
                        if not self._running:
                            break
                        if not line:
                            continue
                            
                        line = line.decode('utf-8')
                        if line.startswith('event:'):
                            evento = line.split(':', 1)[1].strip()
                        elif line.startswith('data:'):
                            data_str = line.split(':', 1)[1].strip()
                            data = json.loads(data_str.replace("'", '"'))
                            
                            if evento == 'muestra':
                                self.muestra_recibida.emit(data['t_s'], data['empuje_n'], data['señal_pct'])
                            elif evento == 'estado':
                                self.estado_cambiado.emit(data['anterior'], data['nuevo'])
            except requests.RequestException:
                if self._running:
                    self.conexion_perdida.emit()
                    time.sleep(2.0)  # Reintentar conexión SSE si se cae

    def detener(self):
        self._running = False
        self.quit()
        self.wait()

# ---------------------------------------------------------------------------
# VENTANA PRINCIPAL
# ---------------------------------------------------------------------------

class ApiWorker(QThread):
    """Hilo trabajador nativo de PyQt para peticiones REST sin bloquear la GUI."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, req_func, method, endpoint, json_data=None):
        super().__init__()
        self.req_func = req_func
        self.method = method
        self.endpoint = endpoint
        self.json_data = json_data

    def run(self):
        try:
            res = self.req_func(self.method, self.endpoint, self.json_data)
            self.finished.emit(res)
        except Exception as e:
            self.error.emit(str(e))
            
class AppDAQ(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SENKU DAQ v{APP_VERSION} (API Client)")
        self.setMinimumSize(1100, 680)
        self.setStyleSheet(f"QMainWindow {{ background:{C['bg']}; }}")

        self.cfg = cargar_config()
        self.session_token = None
        self.estado_actual = "DESCONECTADO"

        self._y_buf = deque([0.0] * BUFFER_GRAFICO, maxlen=BUFFER_GRAFICO)
        self.datos_ensayo = []

        self._construir_ui()
        self._actualizar_botones()

        # Iniciar hilo SSE
        self.sse_thread = SSEThread()
        self.sse_thread.muestra_recibida.connect(self._on_muestra)
        self.sse_thread.estado_cambiado.connect(self._on_cambio_estado)
        self.sse_thread.start()

    # =======================================================================
    # CONSTRUCCIÓN DE LA INTERFAZ
    # =======================================================================

    def _construir_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QHBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(310)
        scroll.setStyleSheet(
            f"QScrollArea {{ background:{C['panel']}; border:none; }}"
            f"QScrollBar:vertical {{ width:8px; background:{C['panel']}; }}"
            f"QScrollBar::handle:vertical {{ background:{C['border']}; border-radius:4px; }}"
        )
        self._panel_izq_widget = QWidget()
        self._panel_izq_widget.setStyleSheet(f"background:{C['panel']};")
        self._panel_izq_lay = QVBoxLayout(self._panel_izq_widget)
        self._panel_izq_lay.setContentsMargins(0, 0, 0, 20)
        self._panel_izq_lay.setSpacing(0)
        scroll.setWidget(self._panel_izq_widget)

        self._panel_der = QWidget()
        self._panel_der.setStyleSheet(f"background:{C['bg']};")
        der_lay = QVBoxLayout(self._panel_der)
        der_lay.setContentsMargins(4, 4, 4, 4)
        der_lay.setSpacing(0)

        root_lay.addWidget(scroll)
        root_lay.addWidget(self._panel_der, stretch=1)

        self._construir_panel_izq()
        self._construir_grafico(der_lay)

    def _seccion(self, texto: str) -> None:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C['border']};")
        self._panel_izq_lay.addWidget(sep)
        lbl = QLabel(texto)
        lbl.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt; font-weight:bold;"
            f"padding:4px 14px 2px 14px;"
        )
        self._panel_izq_lay.addWidget(lbl)

    def _boton(self, texto: str, callback: Callable[[], None], color: str | None = None) -> QPushButton:
        color = color or C["accent"]
        btn = QPushButton(texto)
        btn.setStyleSheet(_css_btn(color))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background:{C['panel']};")
        lay = QHBoxLayout(wrapper)
        lay.setContentsMargins(14, 3, 14, 3)
        lay.addWidget(btn)
        self._panel_izq_lay.addWidget(wrapper)
        return btn

    def _construir_panel_izq(self) -> None:
        p: QVBoxLayout = self._panel_izq_lay

        titulo = QLabel("SENKU DAQ")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titulo.setStyleSheet(
            f"color:{C['accent']}; font-family:Courier; font-size:18pt; font-weight:bold;"
            f"padding-top:18px; background:{C['panel']};"
        )
        p.addWidget(titulo)
        subtitulo = QLabel(f"v{APP_VERSION} · USACH (Client)")
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitulo.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt; background:{C['panel']};"
        )
        p.addWidget(subtitulo)

        # ---- Conexión ----
        self._seccion("CONEXIÓN WIRELESS")
        self._f_host = _Campo("Host / IP", self.cfg.get("host_wifi", "127.0.0.1"))
        self._f_port = _Campo("Puerto TCP", str(self.cfg.get("puerto_tcp", 8080)))
        p.addWidget(self._f_host)
        p.addWidget(self._f_port)
        self._btn_conectar: QPushButton = self._boton("CONECTAR", self._accion_conectar, C["blue"])

        # ---- Datos del Motor ----
        self._seccion("DATOS DEL MOTOR")
        self._f_nombre   = _Campo("Nombre",          self.cfg.get("motor_nombre", "Senku"))
        self._f_diam     = _Campo("Diámetro (mm)",   self.cfg.get("motor_diametro", "20"))
        self._f_longitud = _Campo("Longitud (mm)",   self.cfg.get("motor_longitud", "100"))
        self._f_pesopr   = _Campo("Peso prop. (kg)", self.cfg.get("motor_peso_prop", "0.100"))
        self._f_pesoto   = _Campo("Peso total (kg)", self.cfg.get("motor_peso_total", "0.150"))
        for w in [self._f_nombre, self._f_diam, self._f_longitud, self._f_pesopr, self._f_pesoto]:
            p.addWidget(w)

        # ---- Parámetros de ensayo ----
        self._seccion("PARÁMETROS DE ENSAYO")
        self._f_rango   = _Campo("Rango máx (N)",    str(self.cfg.get("rango_esperado_n", 10.0)))
        self._f_ign_pct = _Campo("Umbral ign. (%)",  str(self.cfg.get("umbral_ignicion_pct", 5.0)))
        self._f_apg_pct = _Campo("Umbral apag. (%)", str(self.cfg.get("umbral_apagado_pct", 2.0)))
        self._f_tmin    = _Campo("T mínimo (s)",     str(self.cfg.get("tiempo_minimo_s", 0.3)))
        self._f_factor  = _Campo("Factor escala",    str(self.cfg.get("factor_escala", 109324.0)))
        for w in [self._f_rango, self._f_ign_pct, self._f_apg_pct, self._f_tmin, self._f_factor]:
            p.addWidget(w)

        # ---- Control ----
        self._seccion("CONTROL")
        self._btn_tara: QPushButton     = self._boton("ESTABLECER TARA",  self._accion_tara,     C["text_dim"])
        self._btn_calibrar: QPushButton = self._boton("CALIBRAR",         self._accion_calibrar, C["blue"])
        self._btn_armar: QPushButton    = self._boton("ARMAR ENSAYO",     self._accion_armar,    C["accent2"])
        self._btn_pausa: QPushButton    = self._boton("PAUSAR",           self._accion_pausa,    C["accent2"])
        self._btn_guardar: QPushButton  = self._boton("GUARDAR Y CERRAR", self._accion_guardar,  C["green"])

        self._lbl_estado_live = QLabel("DESCONECTADO")
        self._lbl_estado_live.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_estado_live.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:11pt; font-weight:bold;"
            f"padding:15px 0 25px 0; background:{C['panel']};"
        )
        p.addWidget(self._lbl_estado_live)
        p.addStretch()

    def _construir_grafico(self, lay: QVBoxLayout) -> None:
        barra = QWidget()
        barra.setFixedHeight(38)
        barra.setStyleSheet(f"background:{C['panel']};")
        barra_lay = QHBoxLayout(barra)
        barra_lay.setContentsMargins(14, 0, 14, 0)

        self._lbl_grafico_titulo = QLabel("EMPUJE EN TIEMPO REAL")
        self._lbl_grafico_titulo.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt; font-weight:bold;")
        barra_lay.addWidget(self._lbl_grafico_titulo)
        barra_lay.addStretch()

        lbl_lectura_txt = QLabel("Lectura:")
        lbl_lectura_txt.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt;")
        self._lbl_lectura_live = QLabel("— N")
        self._lbl_lectura_live.setFixedWidth(100)
        self._lbl_lectura_live.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_lectura_live.setStyleSheet(
            f"color:{C['accent']}; font-family:Courier; font-size:11pt; font-weight:bold;")
        self._lbl_titulo_motor = QLabel("")
        self._lbl_titulo_motor.setStyleSheet(
            f"color:{C['text']}; font-family:Courier; font-size:9pt; padding-left:14px;")
        barra_lay.addWidget(lbl_lectura_txt)
        barra_lay.addWidget(self._lbl_lectura_live)
        barra_lay.addWidget(self._lbl_titulo_motor)
        lay.addWidget(barra)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(C["plot_bg"])
        self._plot_widget.showGrid(x=True, y=True, alpha=0.4)
        self._plot_widget.setLabel("left",   "Empuje (N)",  color=C["text_dim"], size="10pt")
        self._plot_widget.setLabel("bottom", "Tiempo (s)",  color=C["text_dim"], size="10pt")
        self._plot_widget.getAxis("left").setPen(pg.mkPen(C["border"]))
        self._plot_widget.getAxis("bottom").setPen(pg.mkPen(C["border"]))

        self._curve_live = self._plot_widget.plot(pen=pg.mkPen(C["plot_line"], width=2))
        self._curve_rec  = self._plot_widget.plot(pen=pg.mkPen(C["green"], width=2.5))

        self._line_ign_thr = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(C["accent2"], width=1, style=Qt.PenStyle.DashLine))
        self._line_apg_thr = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(C["text_dim"], width=1, style=Qt.PenStyle.DotLine))
        self._plot_widget.addItem(self._line_ign_thr)
        self._plot_widget.addItem(self._line_apg_thr)

        self._curve_fill_base = self._plot_widget.plot([0], [0], pen=pg.mkPen(None))
        fill_color = QColor(C["green"])
        fill_color.setAlpha(45)
        self._fill_impulso = pg.FillBetweenItem(
            self._curve_fill_base, self._curve_rec,
            brush=pg.mkBrush(fill_color))
        self._plot_widget.addItem(self._fill_impulso)

        self._vline_ignicion = pg.InfiniteLine(
            pos=0, angle=90,
            pen=pg.mkPen(C["accent"], width=1.5, style=Qt.PenStyle.DashLine),
            label="Ignición",
            labelOpts={"color": C["accent"], "fill": C["plot_bg"], "movable": False, "position": 0.93})
        self._plot_widget.addItem(self._vline_ignicion)

        self._vline_fin = pg.InfiniteLine(
            pos=1, angle=90,
            pen=pg.mkPen(C["blue"], width=1.5, style=Qt.PenStyle.DashLine),
            label="Fin empuje",
            labelOpts={"color": C["blue"], "fill": C["plot_bg"], "movable": False, "position": 0.80})
        self._plot_widget.addItem(self._vline_fin)

        self._text_metricas = pg.TextItem(
            text="", anchor=(1, 0), color=C["text"],
            fill=pg.mkBrush(QColor(C["plot_bg"]).darker(103)))
        self._text_metricas.setFont(QFont("Courier", 9))
        self._plot_widget.addItem(self._text_metricas)

        self._set_anotaciones_visibles(False)
        lay.addWidget(self._plot_widget, stretch=1)

    def _set_anotaciones_visibles(self, visible: bool) -> None:
        for item in [self._fill_impulso, self._vline_ignicion,
                     self._vline_fin, self._text_metricas]:
            item.setVisible(visible)

    # =======================================================================
    # CAPA DE RED (LLAMADAS REST)
    # =======================================================================

    def headers(self):
        return {"X-Session-Token": self.session_token} if self.session_token else {}

    def _req(self, method: str, endpoint: str, json_data=None):
        """Ejecuta llamadas REST. Lanza excepciones para ser thread-safe."""
        url = f"{API_URL}{endpoint}"
        try:
            if method == "GET":
                res = requests.get(url, headers=self.headers(), timeout=5)
            elif method == "POST":
                res = requests.post(url, json=json_data, headers=self.headers(), timeout=35) 
            elif method == "DELETE":
                res = requests.delete(url, headers=self.headers(), timeout=5)
                
            if not res.ok:
                try:
                    err = res.json()
                    mensaje = err.get('detalle', str(err))
                    # Parseo especial para errores 422 de FastAPI (falta de token/campos)
                    if res.status_code == 422 and isinstance(err.get("detail"), list):
                        mensaje = f"Error de validación (Falta Token o datos): {err['detail'][0]['msg']}"
                except Exception:
                    mensaje = res.text
                raise RuntimeError(mensaje)
            return res.json()
        except requests.exceptions.ConnectionError:
            raise RuntimeError("No se pudo conectar con el Backend (API). Verifica que esté corriendo.")
        except Exception as e:
            raise RuntimeError(str(e))

    # =======================================================================
    # RESPUESTAS DEL HILO SSE
    # =======================================================================

    def _on_cambio_estado(self, anterior: str, nuevo: str):
        self.estado_actual = nuevo
        self._actualizar_botones()
        
        color_map = {
            "DESCONECTADO": C["text_dim"], "CONECTADO": C["blue"], "ESPERANDO": C["green"],
            "ARMADO": C["accent2"], "QUEMANDO": C["accent"], "PAUSADO": C["accent2"],
            "FINALIZADO": C["green"]
        }
        color = color_map.get(nuevo, C["text_dim"])
        lbl_texto = f"{nuevo} 🔥" if nuevo == "QUEMANDO" else (f"{nuevo} ⏸" if nuevo == "PAUSADO" else nuevo)
        
        self._lbl_estado_live.setText(lbl_texto)
        self._lbl_estado_live.setStyleSheet(
            f"color:{color}; font-family:Courier; font-size:11pt; font-weight:bold;"
            f"padding:15px 0 25px 0; background:{C['panel']};"
        )
        
        if nuevo == "FINALIZADO":
            self._descargar_datos_ensayo()

    def _on_muestra(self, t_s: float, empuje_n: float, señal_pct: int):
        self._y_buf.append(empuje_n)
        
        if self.estado_actual in ["QUEMANDO", "PAUSADO"]:
            self.datos_ensayo.append((t_s, empuje_n))

        color = C["accent"] if abs(empuje_n) > 0.01 else C["text_dim"]
        self._lbl_lectura_live.setText(f"{empuje_n:+.3f} N")
        self._lbl_lectura_live.setStyleSheet(f"color:{color}; font-weight:bold;")

        self._actualizar_grafico()

    # =======================================================================
    # ACCIONES DE BOTONES
    # =======================================================================

    def _actualizar_botones(self):
        est = self.estado_actual
        self._btn_conectar.setText("DESCONECTAR" if est != "DESCONECTADO" else "CONECTAR")
        self._btn_tara.setEnabled(est in ["CONECTADO", "ESPERANDO"])
        self._btn_calibrar.setEnabled(est in ["CONECTADO", "ESPERANDO"])
        self._btn_armar.setEnabled(est in ["ESPERANDO", "ARMADO"])
        self._btn_armar.setText("DESARMAR" if est == "ARMADO" else "ARMAR ENSAYO")
        self._btn_pausa.setEnabled(est in ["QUEMANDO", "PAUSADO"])
        self._btn_guardar.setEnabled(est == "FINALIZADO")

        self._lbl_titulo_motor.setText(f"{self._f_nombre.get()} · {self._f_diam.get()}mm")

    def _accion_conectar(self):
        if self.estado_actual != "DESCONECTADO":
            try:
                self._req("DELETE", "/conexion")
            except Exception:
                pass
            self.session_token = None
            self._lbl_estado_live.setText("DESCONECTADO")
            return

        self._btn_conectar.setText("Conectando...")
        self._btn_conectar.setEnabled(False)

        host = self._f_host.get().strip()
        puerto = int(self._f_port.get().strip())

        self.worker_conn = ApiWorker(self._req, "POST", "/conexion", {"host": host, "puerto_tcp": puerto})
        
        def al_terminar(res):
            self._btn_conectar.setEnabled(True)
            self._btn_conectar.setText("CONECTAR")
            if res:
                self.session_token = res["session_token"]
                self.estado_actual = "CONECTADO"
                self._actualizar_botones()
                self._guardar_cfg_actual()
                
                QTimer.singleShot(300, lambda: self._accion_tara(automatica=True))

        def al_error(err):
            self._btn_conectar.setEnabled(True)
            self._btn_conectar.setText("CONECTAR")
            QMessageBox.critical(self, "Error de Conexión", err)

        self.worker_conn.finished.connect(al_terminar)
        self.worker_conn.error.connect(al_error)
        self.worker_conn.start()

    def _accion_tara(self, automatica=False):
        # Validar la existencia del token de sesión
        if not self.session_token:
            if not automatica:
                QMessageBox.warning(self, "Aviso", "Aún recibiendo token de seguridad. Intenta en un par de segundos.")
            return

        self._btn_tara.setText("Midiendo...")
        self._btn_tara.setEnabled(False)
        
        self.worker_tara = ApiWorker(self._req, "POST", "/tara")
            
        def al_terminar(res):
            self._btn_tara.setText("ESTABLECER TARA")
            self._btn_tara.setEnabled(True)
            if res:
                media = res["media_adc"]
                # Si es automática, notificamos discretamente sin bloquear con un Pop-up
                if automatica:
                    print(f"[Auto-Tara] Cero inicial establecido con éxito: {media} ADC.")
                else:
                    QMessageBox.information(self, "Tara Lista", f"Cero establecido en {media} unidades ADC.")

        def al_error(err):
            self._btn_tara.setText("ESTABLECER TARA")
            self._btn_tara.setEnabled(True)
            # Los errores críticos siempre se informan visualmente
            QMessageBox.critical(self, "Error API (Tara)", f"No se pudo inicializar la tara automática:\n{err}")
                
        self.worker_tara.finished.connect(al_terminar)
        self.worker_tara.error.connect(al_error)
        self.worker_tara.start()

    def _accion_calibrar(self):
        # Escudo contra la condición de carrera
        if not self.session_token:
            QMessageBox.warning(self, "Aviso", "Aún recibiendo token de seguridad. Intenta en un segundo.")
            return

        QMessageBox.information(self, "Calibración", "Asegúrate de que la celda esté COMPLETAMENTE VACÍA.\nSe medirán 150 muestras.")
        
        try:
            # Paso 1
            res_tara = self._req("POST", "/calibracion/tara")
            if not res_tara: return
            tara_adc = res_tara["media_adc"]
            
            # Paso 2
            masa_kg, ok = QInputDialog.getDouble(self, "Peso Patrón", f"Tara OK ({tara_adc:.1f}).\nIngresa la masa del peso patrón (kg):", 1.0, 0.01, 1000.0, 3)
            if not ok: return
            
            QMessageBox.information(self, "Calibración", f"Coloca el peso de {masa_kg}kg sobre la celda y presiona OK para medir.")
            
            # Paso 3
            res_carga = self._req("POST", "/calibracion/carga", {
                "masa_patron_kg": masa_kg,
                "tara_adc": tara_adc
            })
            if not res_carga: return
            
            factor_nuevo = res_carga["factor_nuevo"]
            msg = f"Calibración exitosa.\n\nFactor Anterior: {res_carga['factor_anterior']:.1f}\nFactor Nuevo: {factor_nuevo:.1f}\n\n¿Guardar nuevo factor?"
            
            if res_carga.get("advertencia"):
                msg = f"ADVERTENCIA: {res_carga['advertencia']}\n\n" + msg
                
            resp = QMessageBox.question(self, "Confirmar Calibración", msg)
            if resp == QMessageBox.StandardButton.Yes:
                res_conf = self._req("POST", "/calibracion/confirmar", {"factor_nuevo": factor_nuevo})
                if res_conf:
                    self._f_factor.set(f"{factor_nuevo:.2f}")
                    self._guardar_cfg_actual()
                    QMessageBox.information(self, "Guardado", "El factor ha sido actualizado. Recuerda volver a hacer la TARA.")

        except Exception as e:
            QMessageBox.critical(self, "Error API", str(e))

    def _accion_armar(self):
        if not self.session_token:
            QMessageBox.warning(self, "Aviso", "Aún recibiendo token de seguridad.")
            return

        try:
            if self.estado_actual == "ESPERANDO":
                res = self._req("POST", "/ensayo/armar", {
                    "rango_esperado_n": float(self._f_rango.get()),
                    "umbral_ignicion_pct": float(self._f_ign_pct.get()),
                    "umbral_apagado_pct": float(self._f_apg_pct.get()),
                    "tiempo_minimo_s": float(self._f_tmin.get()),
                    "buffer_pre_s": 1.0
                })
                if res:
                    self.datos_ensayo.clear()
                    self._set_anotaciones_visibles(False)
                    self._lbl_grafico_titulo.setText("EMPUJE EN TIEMPO REAL")
                    self._guardar_cfg_actual()
            elif self.estado_actual == "ARMADO":
                self._req("DELETE", "/ensayo/armar")
        except Exception as e:
            QMessageBox.critical(self, "Error API", str(e))

    def _accion_pausa(self):
        if not self.session_token: return
        try:
            self._req("POST", "/ensayo/pausa")
        except Exception as e:
            QMessageBox.critical(self, "Error API", str(e))

    def _accion_guardar(self):
        if not self.session_token: return
        try:
            res = self._req("POST", "/ensayo/guardar", {
                "motor_nombre": self._f_nombre.get(),
                "diametro_mm": self._f_diam.get(),
                "longitud_mm": self._f_longitud.get(),
                "peso_prop_kg": self._f_pesopr.get(),
                "peso_total_kg": self._f_pesoto.get()
            })
            if res:
                self._dibujar_anotaciones_post_ensayo(res)
                msg = f"Impulso: {res['impulso_ns']:.2f} N·s\nClase NFPA: {res['clase_nfpa']}\n\nArchivos guardados en:\n{res['ruta_dir']}"
                QMessageBox.information(self, "Ensayo Guardado", msg)
        except Exception as e:
            QMessageBox.critical(self, "Error API", str(e))

    # =======================================================================
    # GRAFICADO Y DIBUJO
    # =======================================================================

    def _descargar_datos_ensayo(self):
        res = self._req("GET", "/ensayo/datos")
        if res and "puntos" in res:
            self.datos_ensayo = res["puntos"]
            self._actualizar_grafico(forzar_ensayo=True)

    def _dibujar_anotaciones_post_ensayo(self, metricas: dict) -> None:
        if not self.datos_ensayo: return

        ts = np.array([t for t, _ in self.datos_ensayo])
        self._vline_ignicion.setValue(float(ts[0]))
        self._vline_fin.setValue(float(ts[-1]))
        self._curve_fill_base.setData(ts, np.zeros_like(ts))

        x_pos = float(ts[-1]) + (float(ts[-1]) - float(ts[0])) * 0.03
        y_pos = float(self._f_rango.get() or 10.0) * 1.10
        
        self._text_metricas.setText(
            f" Clase NFPA : {metricas['clase_nfpa']}\n"
            f" Impulso    : {metricas['impulso_ns']:.3f} N·s\n"
            f" Empuje máx : {metricas['empuje_max_n']:.2f} N\n"
            f" Empuje avg : {metricas['empuje_avg_n']:.2f} N\n"
            f" Duración   : {metricas['duracion_s']:.3f} s"
        )
        self._text_metricas.setPos(x_pos, y_pos)
        self._set_anotaciones_visibles(True)
        self._lbl_grafico_titulo.setText(f"RESULTADO — {self._f_nombre.get()} · Clase {metricas['clase_nfpa']}")

    def _actualizar_grafico(self, forzar_ensayo=False):
        rango = float(self._f_rango.get() or 10.0)
        ign_pct = float(self._f_ign_pct.get() or 5.0)
        apg_pct = float(self._f_apg_pct.get() or 2.0)

        self._line_ign_thr.setValue(rango * (ign_pct / 100.0))
        self._line_apg_thr.setValue(rango * (apg_pct / 100.0))

        if self.estado_actual in ["QUEMANDO", "PAUSADO", "FINALIZADO"] or forzar_ensayo:
            if self.datos_ensayo:
                ts = np.array([t for t, _ in self.datos_ensayo])
                ns = np.array([n for _, n in self.datos_ensayo])
                self._curve_rec.setData(ts, ns)
                self._curve_live.setData([], [])
                self._plot_widget.setXRange(-0.2, max(ts[-1] * 1.15, 1.0), padding=0.0)
        else:
            buf = np.array(self._y_buf)
            n = len(buf)
            xs = np.linspace(-n * TICK_MS / 1000.0, 0, n)
            self._curve_live.setData(xs, buf)
            self._curve_rec.setData([], [])
            self._plot_widget.setXRange(xs[0], 0.2, padding=0.0)

        self._plot_widget.setYRange(-rango * 0.05, rango * 1.15, padding=0.0)

    # =======================================================================
    # HELPERS Y CIERRE
    # =======================================================================

    def _guardar_cfg_actual(self) -> None:
        self.cfg.update({
            "host_wifi":           self._f_host.get().strip(),
            "puerto_tcp":          int(self._f_port.get().strip() or 8080),
            "factor_escala":       float(self._f_factor.get() or 1.0),
            "motor_nombre":        self._f_nombre.get(),
            "motor_diametro":      self._f_diam.get(),
            "motor_longitud":      self._f_longitud.get(),
            "motor_peso_prop":     self._f_pesopr.get(),
            "motor_peso_total":    self._f_pesoto.get(),
            "rango_esperado_n":    float(self._f_rango.get() or 10.0),
            "umbral_ignicion_pct": float(self._f_ign_pct.get() or 5.0),
            "umbral_apagado_pct":  float(self._f_apg_pct.get() or 2.0),
            "tiempo_minimo_s":     float(self._f_tmin.get() or 0.3),
        })
        guardar_config(self.cfg)

    def closeEvent(self, event: QCloseEvent):
        if self.estado_actual in ["QUEMANDO", "ARMADO"]:
            resp = QMessageBox.question(self, "Ensayo activo", "Hay un ensayo en curso.\n¿Salir de todos modos?")
            if resp != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
                
        self._guardar_cfg_actual()
        if self.session_token:
            # Llamada síncrona final para desconectar en el backend
            try: requests.delete(f"{API_URL}/conexion", headers=self.headers(), timeout=1)
            except: pass
            
        self.sse_thread.detener()
        event.accept()