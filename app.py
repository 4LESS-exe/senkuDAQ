"""
app.py — Ventana principal de SENKU DAQ (PyQt6 + PyQtGraph).

"""

import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Never, cast

import numpy as np
import pyqtgraph as pg  # type: ignore
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QCloseEvent
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from config import (
    APP_VERSION, BUFFER_GRAFICO, TICK_MS, N_LECTURAS_CAL, C,
    cargar_config, guardar_config,
)
from data_export import guardar_ensayo, resumen_texto
from serial_reader import LectorSerial
from state import EstadoEnsayo
from utils import puertos_disponibles, promedio_robusto


# ---------------------------------------------------------------------------
# Configuración global de PyQtGraph
# ---------------------------------------------------------------------------
pg.setConfigOptions(antialias=True, background=C["plot_bg"], foreground=C["text_dim"])


# ---------------------------------------------------------------------------
# Clasificación NFPA 1125
# ---------------------------------------------------------------------------
_CLASES_NFPA: list[tuple[float, float, str]] = [
    (0.000,    2.5,    "1/4A"),
    (2.5,      5.0,    "1/2A"),
    (5.0,     10.0,    "A"),
    (10.0,    20.0,    "B"),
    (20.0,    40.0,    "C"),
    (40.0,    80.0,    "D"),
    (80.0,   160.0,    "E"),
    (160.0,  320.0,    "F"),
    (320.0,  640.0,    "G"),
    (640.0,  1280.0,   "H"),
    (1280.0, 2560.0,   "I"),
    (2560.0, 5120.0,   "J"),
    (5120.0, 10240.0,  "K"),
    (10240.0,20480.0,  "L"),
    (20480.0,40960.0,  "M"),
    (40960.0,81920.0,  "N"),
    (81920.0,163840.0, "O"),
]

def _clase_nfpa(impulso_ns: float) -> str:
    for lo, hi, letra in _CLASES_NFPA:
        if lo < impulso_ns <= hi:
            return letra
    return "O+" if impulso_ns > 0 else "—"


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


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------

class AppDAQ(QMainWindow):
    tara_ok = pyqtSignal(float, float)
    tara_error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SENKU DAQ v{APP_VERSION}")
        self.setMinimumSize(1100, 680)
        self.setStyleSheet(f"QMainWindow {{ background:{C['bg']}; }}")

        self.cfg = cargar_config()

        # Estado
        self.estado: str                             = EstadoEnsayo.DESCONECTADO
        self.lector: LectorSerial | None             = None
        self.valor_cero: float                       = 0.0
        self.datos_ensayo: list[tuple[float, float]] = []
        self.tiempo_ignicion: float                  = 0.0
        self.buffer_pre: deque[tuple[float, float]] = deque()
        self.t_pausa_inicio: float                   = 0.0
        self.t_pausa_acum: float                     = 0.0
        self._tara_en_progreso: bool                = False

        # Buffer gráfico en tiempo real
        self._y_buf: deque[float] = deque([0.0] * BUFFER_GRAFICO, maxlen=BUFFER_GRAFICO)
        self._t_relativo: float = 0.0

        self._construir_ui()
        self._actualizar_estado_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._timer_tara_timeout = QTimer(self)
        self._timer_tara_timeout.setSingleShot(True)
        self._timer_tara_timeout.timeout.connect(self._reset_tara_button)

        self.tara_ok.connect(self._tara_ok)
        self.tara_error.connect(self._tara_error)

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

    # -----------------------------------------------------------------------
    # Helpers panel izquierdo
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Panel izquierdo
    # -----------------------------------------------------------------------

    def _construir_panel_izq(self) -> None:
        p: QVBoxLayout = self._panel_izq_lay

        titulo = QLabel("SENKU DAQ")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titulo.setStyleSheet(
            f"color:{C['accent']}; font-family:Courier; font-size:18pt; font-weight:bold;"
            f"padding-top:18px; background:{C['panel']};"
        )
        p.addWidget(titulo)
        subtitulo = QLabel(f"v{APP_VERSION} · USACH")
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitulo.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt; background:{C['panel']};"
        )
        p.addWidget(subtitulo)

        # ---- Conexión ----
        self._seccion("CONEXIÓN SERIAL")

        puerto_w = QWidget()
        puerto_w.setStyleSheet(f"background:{C['panel']};")
        pl = QHBoxLayout(puerto_w)
        pl.setContentsMargins(14, 2, 14, 2)
        lp = QLabel("Puerto")
        lp.setFixedWidth(160)
        lp.setStyleSheet(f"color:{C['text_dim']}; font-family:Courier; font-size:9pt;")
        self._combo_puerto = QComboBox()
        self._combo_puerto.setStyleSheet(_css_combo())
        self._combo_puerto.setFixedWidth(110)
        self._combo_puerto.setEditable(True)
        self._combo_puerto.addItems(puertos_disponibles())
        if self.cfg.get("puerto"):
            self._combo_puerto.setCurrentText(self.cfg["puerto"])
        pl.addWidget(lp); pl.addWidget(self._combo_puerto); pl.addStretch()
        p.addWidget(puerto_w)

        baud_w = QWidget()
        baud_w.setStyleSheet(f"background:{C['panel']};")
        bl = QHBoxLayout(baud_w)
        bl.setContentsMargins(14, 2, 14, 2)
        lb = QLabel("Baudrate")
        lb.setFixedWidth(160)
        lb.setStyleSheet(f"color:{C['text_dim']}; font-family:Courier; font-size:9pt;")
        self._combo_baud = QComboBox()
        self._combo_baud.setStyleSheet(_css_combo())
        self._combo_baud.setFixedWidth(110)
        for b in ["9600", "57600", "115200", "230400", "500000"]:
            self._combo_baud.addItem(b)
        self._combo_baud.setCurrentText(str(self.cfg.get("baudrate", 115200)))
        bl.addWidget(lb); bl.addWidget(self._combo_baud); bl.addStretch()
        p.addWidget(baud_w)

        self._btn_conectar: QPushButton = self._boton("CONECTAR", self._accion_conectar, C["blue"])

        # ---- Motor ----
        self._seccion("DATOS DEL MOTOR")
        self._f_nombre   = _Campo("Nombre",          self.cfg["motor_nombre"])
        self._f_diam     = _Campo("Diámetro (mm)",   self.cfg["motor_diametro"])
        self._f_longitud = _Campo("Longitud (mm)",   self.cfg["motor_longitud"])
        self._f_pesopr   = _Campo("Peso prop. (kg)", self.cfg["motor_peso_prop"])
        self._f_pesoto   = _Campo("Peso total (kg)", self.cfg["motor_peso_total"])
        for w in [self._f_nombre, self._f_diam, self._f_longitud,
                  self._f_pesopr, self._f_pesoto]:
            p.addWidget(w)

        # ---- Parámetros de ensayo ----
        self._seccion("PARÁMETROS DE ENSAYO")
        self._f_rango   = _Campo("Rango máx (N)",    str(self.cfg["rango_esperado_n"]))
        self._f_ign_pct = _Campo("Umbral ign. (%)",  str(self.cfg["umbral_ignicion_pct"]))
        self._f_apg_pct = _Campo("Umbral apag. (%)", str(self.cfg["umbral_apagado_pct"]))
        self._f_tmin    = _Campo("T mínimo (s)",     str(self.cfg["tiempo_minimo_s"]))
        self._f_factor  = _Campo("Factor escala",    str(self.cfg["factor_escala"]))
        for w in [self._f_rango, self._f_ign_pct, self._f_apg_pct,
                  self._f_tmin, self._f_factor]:
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

    # -----------------------------------------------------------------------
    # Gráfico PyQtGraph
    # -----------------------------------------------------------------------

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
        self._lbl_lectura_live.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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

        # Curvas principales
        self._curve_live = self._plot_widget.plot(pen=pg.mkPen(C["plot_line"], width=2))
        self._curve_rec  = self._plot_widget.plot(pen=pg.mkPen(C["green"], width=2.5))

        # Umbrales
        self._line_ign_thr = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(C["accent2"], width=1, style=Qt.PenStyle.DashLine))
        self._line_apg_thr = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(C["text_dim"], width=1, style=Qt.PenStyle.DotLine))
        self._plot_widget.addItem(self._line_ign_thr)
        self._plot_widget.addItem(self._line_apg_thr)

        # --- Anotaciones post-ensayo ---

        # Área sombreada bajo la curva de ensayo
        self._curve_fill_base = self._plot_widget.plot([0], [0], pen=pg.mkPen(None))
        fill_color = QColor(C["green"])
        fill_color.setAlpha(45)
        self._fill_impulso = pg.FillBetweenItem(
            self._curve_fill_base, self._curve_rec,
            brush=pg.mkBrush(fill_color))
        self._plot_widget.addItem(self._fill_impulso)

        # Línea vertical de ignición (t = ts[0], que típicamente es negativo
        # si hay pre-buffer, pero en datos_ensayo ya está referenciado a 0)
        self._vline_ignicion = pg.InfiniteLine(
            pos=0, angle=90,
            pen=pg.mkPen(C["accent"], width=1.5, style=Qt.PenStyle.DashLine),
            label="Ignición",
            labelOpts={"color": C["accent"], "fill": C["plot_bg"],
                       "movable": False, "position": 0.93})
        self._plot_widget.addItem(self._vline_ignicion)

        # Línea vertical de fin de empuje
        self._vline_fin = pg.InfiniteLine(
            pos=1, angle=90,
            pen=pg.mkPen(C["blue"], width=1.5, style=Qt.PenStyle.DashLine),
            label="Fin empuje",
            labelOpts={"color": C["blue"], "fill": C["plot_bg"],
                       "movable": False, "position": 0.80})
        self._plot_widget.addItem(self._vline_fin)

        # Cuadro de métricas flotante (anclado esquina superior derecha del gráfico)
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
    # TICK PRINCIPAL
    # =======================================================================

    def _tick(self) -> None:
        if self.lector is None:
            return
        if getattr(self.lector, "bloqueo_gui", False):
            return

        nuevos = []
        while True:
            try:
                nuevos.append(self.lector.cola.get_nowait())
            except queue.Empty:
                break

        for val_crudo in nuevos:
            self._procesar_muestra(val_crudo)

        if nuevos:
            ultimo = list(self._y_buf)[-1]
            color: str  = C["accent"] if abs(ultimo) > 0.01 else C["text_dim"]
            self._lbl_lectura_live.setText(f"{ultimo:+.3f} N")
            self._lbl_lectura_live.setStyleSheet(
                f"color:{color}; font-family:Courier; font-size:11pt; font-weight:bold;")

        self._actualizar_grafico()

    # =======================================================================
    # PROCESAMIENTO DE MUESTRAS
    # =======================================================================

    def _procesar_muestra(self, val_filtrado: float) -> None:
        """
        Convierte la señal filtrada y gestiona la lógica de grabación automática.
        """
        factor: float     = self._factor()
        diferencia: float = self.valor_cero - val_filtrado
        empuje_n: float   = (diferencia / factor) * 9.80665

        if abs(empuje_n) < (self._rango() * 0.005):
            empuje_n = 0.0

        t_ahora: float = time.time()
        self._y_buf.append(empuje_n)

        umbral_ign: float = self._rango() * (self._ign_pct() / 100.0)
        umbral_apg: float = self._rango() * (self._apg_pct() / 100.0)

        if self.estado == EstadoEnsayo.ARMADO:
            self.buffer_pre.append((t_ahora, empuje_n))
            while self.buffer_pre and (t_ahora - self.buffer_pre[0][0]) > 1.0:
                self.buffer_pre.popleft()
            if empuje_n >= umbral_ign:
                self._ignicion_detectada(t_ahora)

        elif self.estado == EstadoEnsayo.QUEMANDO:
            t_corr: float = t_ahora - self.tiempo_ignicion - self.t_pausa_acum
            self.datos_ensayo.append((t_corr, empuje_n))
            self._t_relativo = t_corr
            if empuje_n <= umbral_apg and t_corr > self._tmin():
                self._fin_quemado_detectado(t_corr)

    def _ignicion_detectada(self, t_ahora: float) -> None:
        self.tiempo_ignicion = self.buffer_pre[0][0] if self.buffer_pre else t_ahora
        self.t_pausa_acum    = 0.0
        for t_abs, n_val in self.buffer_pre:
            self.datos_ensayo.append((t_abs - self.tiempo_ignicion, n_val))
        self.buffer_pre.clear()
        self._set_anotaciones_visibles(False)
        self._set_estado(EstadoEnsayo.QUEMANDO)
        print(f"[🔥] IGNICIÓN a {time.strftime('%H:%M:%S')}")

    def _fin_quemado_detectado(self, t_corr: float) -> None:
        print(f"[🛑] Fin de empuje a los {t_corr:.3f}s")
        self._set_estado(EstadoEnsayo.FINALIZADO)
        self._actualizar_grafico(forzar_ensayo=True)
        self._dibujar_anotaciones_post_ensayo()
        resp: QMessageBox.StandardButton = QMessageBox.question(
            self, "Ensayo finalizado",
            f"Fin de empuje detectado a los {t_corr:.3f}s\n\n"
            "¿Guardar archivos CSV y .ENG ahora?",
        )
        if resp == QMessageBox.StandardButton.Yes:
            self._guardar_archivos()

    # =======================================================================
    # ANOTACIONES POST-ENSAYO
    # =======================================================================

    def _dibujar_anotaciones_post_ensayo(self) -> None:
        if not self.datos_ensayo:
            return

        ts: np.ndarray[tuple[Any, ...], np.dtype[Any]] = np.array([t for t, _ in self.datos_ensayo])
        ns: np.ndarray[tuple[Any, ...], np.dtype[Any]] = np.array([n for _, n in self.datos_ensayo])

        impulso    = float(np.trapezoid(ns, ts))
        max_empuje = float(ns.max())
        empuje_avg: float = float(ns[ns > 0].mean()) if np.any(ns > 0) else 0.0
        duracion   = float(ts[-1] - ts[0])
        clase: str      = _clase_nfpa(impulso)
        t_fin      = float(ts[-1])

        # Posicionar líneas de evento
        self._vline_ignicion.setValue(float(ts[0]))
        self._vline_fin.setValue(t_fin)

        # Base plana en y=0 para el FillBetweenItem
        self._curve_fill_base.setData(ts, np.zeros_like(ts))

        # Texto de métricas en coordenadas del gráfico
        x_pos: float = t_fin + (t_fin - float(ts[0])) * 0.03
        y_pos: float = self._rango() * 1.10
        self._text_metricas.setText(
            f" Clase NFPA : {clase}\n"
            f" Impulso    : {impulso:.3f} N·s\n"
            f" Empuje máx : {max_empuje:.2f} N\n"
            f" Empuje avg : {empuje_avg:.2f} N\n"
            f" Duración   : {duracion:.3f} s"
        )
        self._text_metricas.setPos(x_pos, y_pos)

        self._set_anotaciones_visibles(True)
        self._lbl_grafico_titulo.setText(
            f"RESULTADO — {self._f_nombre.get()} · Clase {clase}")

    # =======================================================================
    # GRÁFICO (actualización en tiempo real)
    # =======================================================================

    def _actualizar_grafico(self, forzar_ensayo: bool = False) -> None:
        rango: float      = self._rango()
        umbral_ign: float = rango * (self._ign_pct() / 100.0)
        umbral_apg: float = rango * (self._apg_pct() / 100.0)

        self._line_ign_thr.setValue(umbral_ign)
        self._line_apg_thr.setValue(umbral_apg)

        if self.estado in (EstadoEnsayo.QUEMANDO, EstadoEnsayo.PAUSADO) or forzar_ensayo:
            if self.datos_ensayo:
                ts: np.ndarray[tuple[Any, ...], np.dtype[Any]] = np.array([t for t, _ in self.datos_ensayo])
                ns: np.ndarray[tuple[Any, ...], np.dtype[Any]] = np.array([n for _, n in self.datos_ensayo])
                self._curve_rec.setData(ts, ns)
                self._curve_live.setData([], [])
                t_max = ts[-1]
                self._plot_widget.setRange('x', -0.2, max(t_max * 1.15, 1.0), padding=0.0)
        else:
            buf = np.array(self._y_buf)
            n: int   = len(buf)
            xs: np.ndarray[tuple[Any, ...], np.dtype[np.float64]]  = np.linspace(-n * TICK_MS / 1000.0, 0, n)
            self._curve_live.setData(xs, buf)
            self._curve_rec.setData([], [])
            self._plot_widget.setRange('x', xs[0], 0.2, padding=0.0)

        self._plot_widget.setRange('y', -rango * 0.05, rango * 1.15, padding=0.0)

    # =======================================================================
    # ACCIONES DE BOTONES
    # =======================================================================

    def _accion_conectar(self) -> None:
        if self.estado != EstadoEnsayo.DESCONECTADO:
            if self.lector:
                self.lector.detener()  # type: ignore
                self.lector = None
            self._set_estado(EstadoEnsayo.DESCONECTADO)
            return

        puerto: str = self._combo_puerto.currentText().strip()
        if not puerto:
            QMessageBox.warning(self, "Puerto vacío", "Selecciona un puerto serial.")
            return
        try:
            baud = int(self._combo_baud.currentText())
        except ValueError:
            QMessageBox.critical(self, "Baudrate inválido",
                                 "El baudrate debe ser un número entero.")
            return

        self._btn_conectar.setText("Conectando...")
        self._btn_conectar.setEnabled(False)

        # Cola para pasar el lector desde el hilo de fondo al principal
        _resultado: queue.Queue[Any] = queue.Queue()

        def _conectar() -> None:
            lector = LectorSerial(puerto, baud)
            lector.start()
            time.sleep(3.2)
            _resultado.put(lector)   # ← en vez de QTimer.singleShot

        def _conectar_ok(lector: LectorSerial) -> None:
            if lector.error:
                QMessageBox.critical(self, "Error de conexión", lector.error)
                self._btn_conectar.setText("CONECTAR")
                self._btn_conectar.setEnabled(True)
                return
            self.lector = lector
            self._set_estado(EstadoEnsayo.CONECTADO)
            self._guardar_cfg_actual()
            QMessageBox.information(
                self, "Conectado",
                f"Puerto {puerto} abierto a {baud} bps.\n\n"
                "Establece la TARA antes de armar el ensayo.",
            )

        # QTimer en el hilo principal que revisa la cola cada 100 ms
        self._timer_conexion = QTimer(self)
        def _poll() -> None:
            try:
                lector = _resultado.get_nowait()
                self._timer_conexion.stop()
                _conectar_ok(lector)
            except queue.Empty:
                pass
        self._timer_conexion.timeout.connect(_poll)
        self._timer_conexion.start(100)

        threading.Thread(target=_conectar, daemon=True).start()

    def _accion_tara(self) -> None:
        if self.estado not in (EstadoEnsayo.CONECTADO, EstadoEnsayo.ESPERANDO):
            QMessageBox.warning(self, "No disponible",
                                "Solo se puede establecer tara en CONECTADO o ESPERANDO.")
            return
        self._btn_tara.setText("Midiendo...")
        self._btn_tara.setEnabled(False)

        def _hacer_tara() -> None:
            if self.lector is None:
                self.tara_error.emit("El lector serial no está activo.")
                return
            try:
                # Wait up to 5 seconds for initial data to ensure serial is working
                t0: float = time.time()
                while self.lector.cola.empty() and time.time() - t0 < 5.0:
                    time.sleep(0.1)
                if self.lector.cola.empty():
                    raise RuntimeError("No se recibió data del puerto serial. Verifica la conexión y que el Arduino esté enviando datos.")
                vals: list[float] = self.lector.leer_bloqueante(30, timeout=5.0)
                media, std = promedio_robusto(vals)
                self.valor_cero = media
                self.tara_ok.emit(media, std)
            except RuntimeError as e:
                self.tara_error.emit(str(e))

        threading.Thread(target=_hacer_tara, daemon=True).start()

        # Fallback: reset button after 10 seconds if thread hangs
        self._tara_en_progreso = True
        self._timer_tara_timeout.start(10000)

    def _reset_tara_button(self) -> None:
        if self._tara_en_progreso and self._btn_tara.text() == "Midiendo...":
            self._btn_tara.setText("ESTABLECER TARA")
            self._btn_tara.setEnabled(True)
            self._tara_en_progreso = False

    def _finalizar_tara(self) -> None:
        self._tara_en_progreso = False
        if self._timer_tara_timeout.isActive():
            self._timer_tara_timeout.stop()

    def _tara_error(self, msg: str) -> None:
        self._finalizar_tara()
        QMessageBox.critical(self, "Error tara", msg)
        self._btn_tara.setText("ESTABLECER TARA")
        self._btn_tara.setEnabled(True)

    def _tara_ok(self, media: float, std: float) -> None:
        self._finalizar_tara()
        self._btn_tara.setText("ESTABLECER TARA")
        self._btn_tara.setEnabled(True)
        self._set_estado(EstadoEnsayo.ESPERANDO)
        print(f"[✓] Tara establecida: {media:.1f}  (σ={std:.1f})")
        self._lbl_estado_live.setText(f"TARA: {media:.0f}")

    def _accion_calibrar(self) -> None:
        if self.estado not in (EstadoEnsayo.CONECTADO, EstadoEnsayo.ESPERANDO):
            QMessageBox.warning(self, "No disponible",
                                "Conecta el sensor y establece la tara antes de calibrar.")
            return
        if self.estado == EstadoEnsayo.QUEMANDO:
            QMessageBox.warning(self, "Ensayo activo",
                                "No se puede calibrar durante un ensayo.")
            return
        self._ventana_calibracion()

    def _accion_armar(self) -> None:
        if self.estado == EstadoEnsayo.ESPERANDO:
            try:
                rango = float(self._f_rango.get())
                ign   = float(self._f_ign_pct.get())
                apg   = float(self._f_apg_pct.get())
                tmin  = float(self._f_tmin.get())
                assert rango > 0 and 0 < ign <= 100 and 0 < apg <= 100 and tmin >= 0
            except (ValueError, AssertionError):
                QMessageBox.critical(self, "Parámetros inválidos",
                                     "Revisa Rango máx, umbrales (%) y tiempo mínimo.")
                return
            self.datos_ensayo.clear()
            self.buffer_pre.clear()
            self.t_pausa_acum = 0.0
            self._set_anotaciones_visibles(False)
            self._lbl_grafico_titulo.setText("EMPUJE EN TIEMPO REAL")
            self._guardar_cfg_actual()
            self._set_estado(EstadoEnsayo.ARMADO)
            ign_n: float = rango * (ign / 100.0)
            apg_n: float = rango * (apg / 100.0)
            print(f"[✓] ARMADO — Umbral ignición: {ign_n:.3f}N  |  Apagado: {apg_n:.3f}N")
        elif self.estado == EstadoEnsayo.ARMADO:
            self._set_estado(EstadoEnsayo.ESPERANDO)

    def _accion_pausa(self) -> None:
        if self.estado == EstadoEnsayo.QUEMANDO:
            self.t_pausa_inicio = time.time()
            self._set_estado(EstadoEnsayo.PAUSADO)
            print("[⏸] ENSAYO PAUSADO")
        elif self.estado == EstadoEnsayo.PAUSADO:
            self.t_pausa_acum += time.time() - self.t_pausa_inicio
            self._set_estado(EstadoEnsayo.QUEMANDO)
            print(f"[▶] ENSAYO REANUDADO (pausa acumulada: {self.t_pausa_acum:.2f}s)")

    def _accion_guardar(self) -> None:
        if not self.datos_ensayo:
            QMessageBox.warning(self, "Sin datos", "No hay datos de ensayo para guardar.")
            return
        self._guardar_archivos()

    # =======================================================================
    # GUARDAR ARCHIVOS
    # =======================================================================

    def _guardar_archivos(self) -> None:
        try:
            metricas = guardar_ensayo(
                datos        = self.datos_ensayo,
                plot_item    = self._plot_widget.getPlotItem(),
                nombre_motor = self._f_nombre.get(),
                diametro     = self._f_diam.get(),
                longitud     = self._f_longitud.get(),
                peso_prop    = self._f_pesopr.get(),
                peso_total   = self._f_pesoto.get(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al guardar", str(e))
            return
        QMessageBox.information(self, "Ensayo guardado",
                                resumen_texto(metricas, self._f_nombre.get()))
        self._set_estado(EstadoEnsayo.ESPERANDO)

    # =======================================================================
    # VENTANA DE CALIBRACIÓN
    # =======================================================================

    def _ventana_calibracion(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Calibración — SENKU DAQ")
        dlg.setStyleSheet(f"QDialog {{ background:{C['bg']}; }}")
        dlg.setFixedWidth(480)
        dlg.setModal(True)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(8)

        titulo = QLabel("CALIBRACIÓN DE CELDA DE CARGA")
        titulo.setStyleSheet(
            f"color:{C['accent']}; font-family:Courier; font-size:13pt; font-weight:bold;")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(titulo)

        lbl_instr = QLabel("Paso 1: Asegúrate de que la celda esté COMPLETAMENTE VACÍA.")
        lbl_instr.setWordWrap(True)
        lbl_instr.setStyleSheet(f"color:{C['text']}; font-family:Courier; font-size:10pt;")
        lay.addWidget(lbl_instr)

        lbl_estado = QLabel("Listo para iniciar.")
        lbl_estado.setStyleSheet(
            f"color:{C['text_dim']}; font-family:Courier; font-size:9pt;")
        lay.addWidget(lbl_estado)

        barra = QProgressBar()
        barra.setRange(0, 100)
        barra.setValue(0)
        barra.setStyleSheet(
            f"QProgressBar {{ border:1px solid {C['border']}; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{C['accent']}; }}")
        lay.addWidget(barra)

        lbl_result = QLabel("")
        lbl_result.setWordWrap(True)
        lbl_result.setStyleSheet(
            f"color:{C['green']}; font-family:Courier; font-size:10pt;")
        lay.addWidget(lbl_result)

        estado_cal: dict[str, int | float | None] = {"paso": 0, "tara": None}
        btn = QPushButton("Iniciar (celda vacía) →")
        btn.setStyleSheet(_css_btn(C["blue"]))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(btn)

        def avanzar() -> None:
            btn.setEnabled(False)
            if estado_cal["paso"] == 0:
                lbl_instr.setText("Midiendo tara — no muevas el banco...")
                lbl_estado.setText(f"Leyendo {N_LECTURAS_CAL} muestras...")
                lbl_estado.setStyleSheet("color:#f0a500; font-family:Courier; font-size:9pt;")

                def _tara() -> None:
                    assert self.lector is not None
                    try:
                        vals: list[float] = self.lector.leer_bloqueante(N_LECTURAS_CAL, timeout=60)
                        media, std = promedio_robusto(vals)
                        estado_cal["tara"] = media
                        barra.setValue(50)
                        QTimer.singleShot(0, lambda: _tara_ok(media, std))
                    except RuntimeError as e:
                        QTimer.singleShot(0, lambda: _error(str(e)))

                def _tara_ok(media: float, std: float) -> None:
                    lbl_estado.setText(
                        f"Tara: {media:.1f}  (σ={std:.1f}, n={N_LECTURAS_CAL})")
                    lbl_estado.setStyleSheet(
                        f"color:{C['green']}; font-family:Courier; font-size:9pt;")
                    lbl_instr.setText(
                        "Paso 2: Ingresa la masa del peso patrón y colócalo sobre la celda.")
                    estado_cal["paso"] = 1
                    btn.setEnabled(True)
                    btn.setText("Confirmar peso colocado →")

                threading.Thread(target=_tara, daemon=True).start()

            elif estado_cal["paso"] == 1:
                masa_dlg = QDialog(dlg)
                masa_dlg.setWindowTitle("Masa del peso patrón")
                masa_dlg.setStyleSheet(f"QDialog {{ background:{C['bg']}; }}")
                masa_dlg.setModal(True)
                m_lay = QVBoxLayout(masa_dlg)
                m_lay.setContentsMargins(20, 16, 20, 16)
                m_lay.addWidget(QLabel("Masa del peso patrón (kg):"))
                m_entry = QLineEdit("1.000")
                m_entry.setStyleSheet(_css_entry())
                m_entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
                m_lay.addWidget(m_entry)
                masa_result: list[Any] = [None]

                def confirmar_masa() -> None:
                    try:
                        v = float(m_entry.text().replace(",", "."))
                        assert v > 0
                        masa_result[0] = v
                        masa_dlg.accept()
                    except (ValueError, AssertionError):
                        m_entry.setStyleSheet(_css_entry() + "border-color:red;")

                ok_btn = QPushButton("Aceptar")
                ok_btn.setStyleSheet(_css_btn(C["blue"]))
                ok_btn.clicked.connect(confirmar_masa)
                m_entry.returnPressed.connect(confirmar_masa)
                m_lay.addWidget(ok_btn)
                masa_dlg.exec()

                if masa_result[0] is None:
                    btn.setEnabled(True)
                    return

                masa_kg: Never = masa_result[0]
                lbl_instr.setText(f"Midiendo con {masa_kg:.4f} kg sobre la celda...")
                lbl_estado.setText(f"Leyendo {N_LECTURAS_CAL} muestras...")
                lbl_estado.setStyleSheet("color:#f0a500; font-family:Courier; font-size:9pt;")

                def _carga() -> None:
                    assert self.lector is not None
                    try:
                        vals: list[float] = self.lector.leer_bloqueante(N_LECTURAS_CAL, timeout=60)
                        media_c, std_c = promedio_robusto(vals)
                        tara   = cast(float, estado_cal["tara"])
                        delta  = abs(media_c - tara)
                        factor_tent: float = delta / masa_kg if delta > 0 else 0.0
                        razon: float | int  = factor_tent / self._factor() if self._factor() else 1
                        print(f"\n  [DIAGNÓSTICO CALIBRACIÓN]")
                        print(f"    Tara cruda     : {tara:.1f}")
                        print(f"    Cargado crudo  : {media_c:.1f}")
                        print(f"    Delta crudo    : {delta:.1f}")
                        print(f"    Masa (kg)      : {masa_kg:.4f}")
                        print(f"    Factor tent.   : {factor_tent:.2f}")
                        print(f"    Factor actual  : {self._factor():.2f}")
                        if delta < 500 or not (0.05 < razon < 20.0):
                            msg: str = (
                                f"Resultado sospechoso.\n\nΔ crudo = {delta:.1f}\n"
                                f"Factor tentativo = {factor_tent:.1f}\n"
                                f"Factor actual = {self._factor():.1f}\n\n"
                                f"Verifica que el peso esté correctamente colocado.")
                            QTimer.singleShot(0, lambda m=msg: _advertencia(m))
                            return
                        QTimer.singleShot(
                            0, lambda f=factor_tent, s=std_c, d=delta: _carga_ok(f, s, d))
                    except RuntimeError as e:
                        QTimer.singleShot(0, lambda: _error(str(e)))

                def _carga_ok(nuevo_factor: float, std_c: float, delta: float) -> None:
                    self._f_factor.set(f"{nuevo_factor:.2f}")
                    self.cfg["factor_escala"] = nuevo_factor
                    guardar_config(self.cfg)
                    barra.setValue(100)
                    lbl_result.setText(
                        f"✓  Nuevo factor: {nuevo_factor:.2f}\n"
                        f"   Δ crudo={delta:.0f}  σ={std_c:.1f}")
                    lbl_estado.setText("Calibración completada.")
                    lbl_estado.setStyleSheet(
                        f"color:{C['green']}; font-family:Courier; font-size:9pt;")
                    lbl_instr.setText("Puedes cerrar esta ventana.")
                    estado_cal["paso"] = 2
                    btn.setEnabled(True)
                    btn.setText("Cerrar")
                    QMessageBox.information(
                        dlg, "Calibración OK",
                        f"Factor actualizado: {nuevo_factor:.2f}\n\n"
                        "Recuerda establecer la TARA nuevamente\n"
                        "con la celda vacía antes del ensayo.")

                threading.Thread(target=_carga, daemon=True).start()

            elif estado_cal["paso"] == 2:
                dlg.accept()

        def _advertencia(msg: str) -> None:
            btn.setEnabled(True)
            barra.setValue(50)
            lbl_estado.setText("Reintenta — revisa consola.")
            lbl_estado.setStyleSheet(
                f"color:{C['accent']}; font-family:Courier; font-size:9pt;")
            QMessageBox.warning(dlg, "Advertencia", msg)

        def _error(msg: str) -> None:
            btn.setEnabled(True)
            lbl_estado.setText(f"Error: {msg}")
            lbl_estado.setStyleSheet(
                f"color:{C['accent']}; font-family:Courier; font-size:9pt;")

        btn.clicked.connect(avanzar)
        dlg.exec()

    # =======================================================================
    # GESTIÓN DE ESTADO
    # =======================================================================

    def _set_estado(self, nuevo: str) -> None:
        self.estado = nuevo
        self._actualizar_estado_ui()

    def _actualizar_estado_ui(self) -> None:
        s: str = self.estado
        color_map: dict[str, tuple[str, str]] = {
            EstadoEnsayo.DESCONECTADO: (C["text_dim"],  "DESCONECTADO"),
            EstadoEnsayo.CONECTADO:    (C["blue"],       "CONECTADO"),
            EstadoEnsayo.ESPERANDO:    (C["green"],      "ESPERANDO"),
            EstadoEnsayo.ARMADO:       (C["accent2"],    "ARMADO"),
            EstadoEnsayo.QUEMANDO:     (C["accent"],     "QUEMANDO 🔥"),
            EstadoEnsayo.PAUSADO:      (C["accent2"],    "PAUSADO ⏸"),
            EstadoEnsayo.FINALIZADO:   (C["green"],      "FINALIZADO"),
        }
        color, texto = color_map.get(s, (C["text_dim"], s))
        self._lbl_estado_live.setText(texto)
        self._lbl_estado_live.setStyleSheet(
            f"color:{color}; font-family:Courier; font-size:11pt; font-weight:bold;"
            f"padding:15px 0 25px 0; background:{C['panel']};"
        )

        def st(btn: QPushButton, activo: bool) -> None: btn.setEnabled(activo)

        st(self._btn_conectar,  True)
        st(self._btn_tara,      s in (EstadoEnsayo.CONECTADO, EstadoEnsayo.ESPERANDO))
        st(self._btn_calibrar,  s in (EstadoEnsayo.CONECTADO, EstadoEnsayo.ESPERANDO))
        st(self._btn_armar,     s in (EstadoEnsayo.ESPERANDO, EstadoEnsayo.ARMADO))
        st(self._btn_pausa,     s in (EstadoEnsayo.QUEMANDO,  EstadoEnsayo.PAUSADO))
        st(self._btn_guardar,   s in (EstadoEnsayo.QUEMANDO,  EstadoEnsayo.PAUSADO,
                                      EstadoEnsayo.FINALIZADO))

        self._btn_conectar.setText(
            "DESCONECTAR" if s != EstadoEnsayo.DESCONECTADO else "CONECTAR")
        self._btn_armar.setText(
            "DESARMAR" if s == EstadoEnsayo.ARMADO else "ARMAR ENSAYO")
        self._btn_pausa.setText(
            "REANUDAR" if s == EstadoEnsayo.PAUSADO else "PAUSAR")

        self._lbl_titulo_motor.setText(
            f"{self._f_nombre.get()} · {self._f_diam.get()}mm")

    # =======================================================================
    # HELPERS DE PARÁMETROS
    # =======================================================================

    def _factor(self) -> float:
        try:    return max(1.0, float(self._f_factor.get()))
        except: return cast(float, self.cfg["factor_escala"])

    def _rango(self) -> float:
        try:    return max(0.01, float(self._f_rango.get()))
        except: return cast(float, self.cfg["rango_esperado_n"])

    def _ign_pct(self) -> float:
        try:    return max(0.1, float(self._f_ign_pct.get()))
        except: return cast(float, self.cfg["umbral_ignicion_pct"])

    def _apg_pct(self) -> float:
        try:    return max(0.1, float(self._f_apg_pct.get()))
        except: return cast(float, self.cfg["umbral_apagado_pct"])

    def _tmin(self) -> float:
        try:    return max(0.0, float(self._f_tmin.get()))
        except: return cast(float, self.cfg["tiempo_minimo_s"])

    def _guardar_cfg_actual(self) -> None:
        self.cfg.update({
            "puerto":              self._combo_puerto.currentText(),
            "baudrate":            int(self._combo_baud.currentText() or 115200),
            "factor_escala":       self._factor(),
            "motor_nombre":        self._f_nombre.get(),
            "motor_diametro":      self._f_diam.get(),
            "motor_longitud":      self._f_longitud.get(),
            "motor_peso_prop":     self._f_pesopr.get(),
            "motor_peso_total":    self._f_pesoto.get(),
            "rango_esperado_n":    self._rango(),
            "umbral_ignicion_pct": self._ign_pct(),
            "umbral_apagado_pct":  self._apg_pct(),
            "tiempo_minimo_s":     self._tmin(),
        })
        guardar_config(self.cfg)

    # =======================================================================
    # CIERRE
    # =======================================================================

    def closeEvent(self, event: QCloseEvent | None) -> None:
        assert event is not None
        if self.estado in (EstadoEnsayo.QUEMANDO, EstadoEnsayo.ARMADO):
            resp: QMessageBox.StandardButton = QMessageBox.question(
                self, "Ensayo activo",
                "Hay un ensayo en curso.\n¿Salir de todos modos?",
            )
            if resp != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._guardar_cfg_actual()
        if self.lector:
            self.lector.detener()  # type: ignore
        self._timer.stop()
        event.accept()
