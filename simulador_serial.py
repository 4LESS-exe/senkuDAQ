#!/usr/bin/env python3
"""
simulador_serial.py — Reemplazo del LectorSerial para pruebas sin HX711.

Genera una señal sintética con:
  - Línea base ruidosa (reposo del sensor)
  - Curva de empuje parametrizada: rampa → plateau → caída exponencial
  - Ruido gaussiano de cuantización superpuesto
  - Parámetros aleatorizados en cada ejecución (pico, timing, forma)

Uso como módulo (drop-in para el programa principal):
─────────────────────────────────────────────────────
    # Reemplazar en el programa principal:
    #   from serial_reader import LectorSerial
    # Por:
    from simulador_serial import SimuladorSerial as LectorSerial

    lector = LectorSerial()   # sin puerto ni baudrate reales
    lector.start()

Uso desde CMD (visualización en consola):
─────────────────────────────────────────
    python simulador_serial.py
    python simulador_serial.py --duracion 30 --sps 80 --peak-min 3000 --peak-max 5000
    python simulador_serial.py --semilla 42        # reproducible
    python simulador_serial.py --base 200 --ruido 80
"""

import argparse
import math
import queue
import random
import sys
import threading
import time

import numpy as np


# ---------------------------------------------------------------------------
# Filtro de Kalman (idéntico al de serial_reader.py)
# ---------------------------------------------------------------------------

class _KalmanScalar:
    """Filtro de Kalman escalar de velocidad cero para señales 1D."""

    def __init__(self, Q: float = 1e4, R: float = 9e6):
        self.Q = Q
        self.R = R
        self._x: float | None = None
        self._P: float = 1.0

    def reset(self):
        self._x = None
        self._P = 1.0

    def update(self, z: float) -> float:
        if self._x is None:
            self._x = z
            return z
        P_pred  = self._P + self.Q
        K       = P_pred / (P_pred + self.R)
        self._x = self._x + K * (z - self._x)
        self._P = (1.0 - K) * P_pred
        return self._x


# ---------------------------------------------------------------------------
# Generador de señal sintética
# ---------------------------------------------------------------------------

class _GeneradorCurva:
    """
    Curva de empuje parametrizada con cuatro fases:

        Pre-ignición  │ Rampa │ Plateau │ Caída  │ Post-ensayo
        ──────────────┼───────┼─────────┼────────┼─────────────
        baseline      │ exp↑  │  pico   │  exp↓  │ baseline

    El ruido de cuantización (ruido_std) siempre está presente.
    El flutter (flutter_std) escala con la amplitud de cada fase.
    """

    def __init__(
        self,
        valor_base: float,
        pico: float,
        t_ignicion: float,
        t_rampa: float,
        t_combustion: float,
        t_caida: float,
        ruido_std: float,
        flutter_std: float,
    ):
        self._base      = valor_base
        self._pico      = pico
        self._ruido     = ruido_std
        self._flutter   = flutter_std

        # Tiempos de transición absolutos
        self._t0 = t_ignicion
        self._t1 = t_ignicion + t_rampa
        self._t2 = t_ignicion + t_rampa + t_combustion
        self._t3 = t_ignicion + t_rampa + t_combustion + t_caida

        # Exponer metadatos para el log de inicio
        self.pico        = pico
        self.t_ignicion  = t_ignicion
        self.t_rampa     = t_rampa
        self.t_combustion= t_combustion
        self.t_caida     = t_caida

    def evaluar(self, t: float) -> float:
        """Valor sintético de la señal en el instante t [segundos]."""
        delta = self._pico - self._base

        if t < self._t0:
            # ── Pre-ignición: línea base con deriva lenta ──────────────
            deriva = 0.5 * math.sin(2 * math.pi * t / 7.3)  # oscilación térmica
            señal  = self._base + deriva

        elif t < self._t1:
            # ── Rampa: subida exponencial ──────────────────────────────
            frac   = (t - self._t0) / (self._t1 - self._t0)   # 0 → 1
            señal  = self._base + delta * (1.0 - math.exp(-5.0 * frac))
            señal += random.gauss(0.0, self._flutter * frac)

        elif t < self._t2:
            # ── Plateau: pico con flutter ──────────────────────────────
            # Micro-oscilación de baja frecuencia sobre el plateau
            frac   = (t - self._t1) / (self._t2 - self._t1)
            ondula = 0.03 * delta * math.sin(2 * math.pi * frac * 3.1)
            señal  = self._pico + ondula
            señal += random.gauss(0.0, self._flutter)

        elif t < self._t3:
            # ── Caída: decaimiento exponencial ────────────────────────
            frac   = (t - self._t2) / (self._t3 - self._t2)   # 0 → 1
            señal  = self._base + delta * math.exp(-4.5 * frac)
            señal += random.gauss(0.0, self._flutter * (1.0 - frac))

        else:
            # ── Post-ensayo: vuelta a la línea base ────────────────────
            señal = self._base

        # Ruido de cuantización/ADC siempre presente
        return señal + random.gauss(0.0, self._ruido)


def _curva_aleatoria(
    base: float,
    peak_min: float,
    peak_max: float,
    ruido_std: float | None = None,
) -> _GeneradorCurva:
    """Construye un generador con parámetros aleatorios dentro de rangos razonables."""
    pico         = random.uniform(peak_min, peak_max)
    t_ignicion   = random.uniform(2.0,  5.0)
    t_rampa      = random.uniform(0.3,  1.2)
    t_combustion = random.uniform(3.0,  8.0)
    t_caida      = random.uniform(0.5,  2.0)
    ruido        = ruido_std if ruido_std is not None else random.uniform(20.0, 60.0)
    flutter      = pico * random.uniform(0.01, 0.04)   # 1 – 4 % del pico

    return _GeneradorCurva(
        valor_base   = base,
        pico         = pico,
        t_ignicion   = t_ignicion,
        t_rampa      = t_rampa,
        t_combustion = t_combustion,
        t_caida      = t_caida,
        ruido_std    = ruido,
        flutter_std  = flutter,
    )


# ---------------------------------------------------------------------------
# SimuladorSerial — drop-in para LectorSerial
# ---------------------------------------------------------------------------

class SimuladorSerial(threading.Thread):
    """
    Reemplazo de LectorSerial para pruebas sin hardware HX711.

    Interfaz idéntica a LectorSerial
    ──────────────────────────────────
    Atributos públicos:
        cola          queue.Queue[float]  ← consumir igual que en el programa real
        error         str
        bloqueo_gui   bool
        ser           None               ← no hay puerto real

    Métodos públicos:
        start()                          ← heredado de Thread
        detener()
        reset_kalman()
        leer_bloqueante(n, timeout)

    Parámetros
    ──────────
    sps : int
        Muestras por segundo simuladas (default 80 = HX711 en modo rápido).
    valor_base : float
        Valor de línea base en reposo (default 100).
    peak_min, peak_max : float
        Rango del pico aleatorio en las mismas unidades que usa el programa.
    ruido_std : float | None
        Desviación estándar del ruido de cuantización.
        None → valor aleatorio entre 20 y 60.
    kalman_Q, kalman_R : float
        Parámetros del filtro de Kalman (mismos defaults que LectorSerial).
    semilla : int | None
        Semilla del RNG. None = totalmente aleatorio cada ejecución.
    """

    def __init__(
        self,
        # LectorSerial recibe puerto y baudrate; aquí se ignoran para compatibilidad
        puerto:    str   = "SIM",
        baudrate:  int   = 115200,
        # Parámetros propios del simulador
        sps:       int   = 80,
        valor_base:float = 100.0,
        peak_min:  float = 3000.0,
        peak_max:  float = 5000.0,
        ruido_std: float | None = None,
        kalman_Q:  float = 1e4,   # alias: kalman_q
        kalman_R:  float = 9e6,   # alias: kalman_r
        kalman_q:  float | None = None,
        kalman_r:  float | None = None,
        semilla:   int | None = None,
    ):
        super().__init__(daemon=True)

        # Atributos públicos que el programa principal puede consultar
        self.cola:        queue.Queue[float] = queue.Queue(maxsize=2000)
        self._stop_event: threading.Event    = threading.Event()
        self.error:       str                = ""
        self.bloqueo_gui: bool               = False
        self.ser                             = None  # Sin puerto real

        self._sps       = sps
        self._intervalo = 1.0 / sps
        self._base      = valor_base
        self._peak_min  = peak_min
        self._peak_max  = peak_max
        self._ruido_std = ruido_std
        # Aceptar tanto mayúsculas (kalman_Q/R) como minúsculas (kalman_q/r)
        q = kalman_q if kalman_q is not None else kalman_Q
        r = kalman_r if kalman_r is not None else kalman_R
        self._kalman    = _KalmanScalar(Q=q, R=r)

        if semilla is not None:
            random.seed(semilla)
            np.random.seed(semilla)

    # ------------------------------------------------------------------
    # HILO PRINCIPAL
    # ------------------------------------------------------------------

    def run(self):
        print(f"[i] SimuladorSerial iniciado ({self._sps} SPS, sin HX711).", flush=True)

        curva = _curva_aleatoria(
            base      = self._base,
            peak_min  = self._peak_min,
            peak_max  = self._peak_max,
            ruido_std = self._ruido_std,
        )
        print(
            f"    Pico aleatorio : {curva.pico:.1f}\n"
            f"    Ignición en    : t = {curva.t_ignicion:.2f} s\n"
            f"    Rampa          : {curva.t_rampa:.2f} s\n"
            f"    Combustión     : {curva.t_combustion:.2f} s\n"
            f"    Caída          : {curva.t_caida:.2f} s\n",
            flush=True,
        )

        self._kalman.reset()
        t_inicio = time.perf_counter()
        next_tick = t_inicio

        while not self._stop_event.is_set():
            t        = time.perf_counter() - t_inicio
            val_crudo = curva.evaluar(t)

            if not self.bloqueo_gui:
                val = self._kalman.update(val_crudo)
            else:
                val = val_crudo   # muestras crudas para leer_bloqueante

            try:
                self.cola.put_nowait(val)
            except queue.Full:
                pass   # Igual que LectorSerial: descarta si la cola está llena

            # Espera precisa para mantener el SPS objetivo
            next_tick += self._intervalo
            dormir = next_tick - time.perf_counter()
            if dormir > 0:
                time.sleep(dormir)

        print("[i] SimuladorSerial detenido.", flush=True)

    # ------------------------------------------------------------------
    # CONTROL
    # ------------------------------------------------------------------

    def detener(self):
        """Señala al hilo que debe detenerse (equivalente a cerrar el puerto)."""
        self._stop_event.set()

    def reset_kalman(self):
        """Reinicia el estado del filtro de Kalman."""
        self._kalman.reset()

    # ------------------------------------------------------------------
    # LECTURA BLOQUEANTE (tara y calibración) — muestras CRUDAS
    # ------------------------------------------------------------------

    def leer_bloqueante(self, n: int, timeout: float = 60.0) -> list[float]:
        """
        Captura exactamente *n* muestras crudas (sin Kalman).
        Interfaz idéntica a LectorSerial.leer_bloqueante.
        """
        self.bloqueo_gui = True

        # Vaciar cola antes de capturar
        while not self.cola.empty():
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break
        self._kalman.reset()

        valores: list[float] = []
        t0 = time.time()
        print(f"\n[i] Capturando {n} muestras crudas (simuladas, Kalman OFF)...", flush=True)

        while len(valores) < n:
            if time.time() - t0 > timeout:
                self.bloqueo_gui = False
                raise RuntimeError(
                    f"Timeout: solo {len(valores)}/{n} muestras recibidas."
                )
            try:
                val = self.cola.get(timeout=1.0)
                valores.append(val)
                if len(valores) % 5 == 0:
                    print(f"    > Recolectadas: {len(valores)}/{n}", flush=True)
            except queue.Empty:
                pass

        self.bloqueo_gui = False
        arr = np.array(valores)
        print(f"[✓] Media: {arr.mean():.1f} | σ: {arr.std():.1f}\n", flush=True)
        return valores


# ---------------------------------------------------------------------------
# Modo CMD — visualización en consola sin GUI
# ---------------------------------------------------------------------------

_ANCHO_BARRA = 48   # caracteres de la barra ASCII


def _barra(valor: float, base: float, pico_max: float) -> str:
    """Barra ASCII proporcional al valor."""
    rango = max(pico_max - base, 1.0)
    frac  = max(0.0, min(1.0, (valor - base) / rango))
    llenos = int(frac * _ANCHO_BARRA)
    return "█" * llenos + "░" * (_ANCHO_BARRA - llenos)


def _modo_cmd(args: argparse.Namespace) -> None:
    """Ejecuta el simulador y muestra la señal en tiempo real en la consola."""
    sep = "─" * 62
    print(sep)
    print("  SimuladorSerial HX711 — visualización CMD")
    print(f"  SPS: {args.sps}  |  Duración: {args.duracion} s  |  Ctrl+C para salir")
    print(sep)

    sim = SimuladorSerial(
        sps        = args.sps,
        valor_base = args.base,
        peak_min   = args.peak_min,
        peak_max   = args.peak_max,
        ruido_std  = args.ruido,
        semilla    = args.semilla,
    )
    sim.start()

    t_fin      = time.time() + args.duracion
    val_max    = args.base   # máximo observado para escalar la barra

    print("\n  Valor actual         Señal\n")
    try:
        while time.time() < t_fin and not sim._stop_event.is_set():
            try:
                val = sim.cola.get(timeout=0.1)
            except queue.Empty:
                continue

            val_max = max(val_max, val)
            barra   = _barra(val, args.base, args.peak_max)
            t_el    = args.duracion - (t_fin - time.time())

            print(
                f"\r  [{t_el:6.2f}s]  {val:>10.1f}  |{barra}|",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        pass
    finally:
        sim.detener()
        print(f"\n\n{sep}")
        print(f"  Pico máximo observado: {val_max:.1f}")
        print(f"  Simulación finalizada.")
        print(sep)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simulador de señal HX711 — pruebas sin hardware.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sps", type=int, default=80,
        help="Muestras por segundo simuladas (igual al HX711 en modo rápido).",
    )
    parser.add_argument(
        "--duracion", type=float, default=20.0,
        help="Duración total de la visualización en segundos.",
    )
    parser.add_argument(
        "--base", type=float, default=100.0,
        help="Valor de línea base (reposo del sensor).",
    )
    parser.add_argument(
        "--peak-min", dest="peak_min", type=float, default=3000.0,
        help="Límite inferior del pico aleatorio.",
    )
    parser.add_argument(
        "--peak-max", dest="peak_max", type=float, default=5000.0,
        help="Límite superior del pico aleatorio.",
    )
    parser.add_argument(
        "--ruido", type=float, default=None,
        help="Desviación estándar del ruido de cuantización. "
             "Si no se indica, se elige aleatoriamente entre 20 y 60.",
    )
    parser.add_argument(
        "--semilla", type=int, default=None,
        help="Semilla RNG para reproducibilidad. Sin semilla → aleatorio.",
    )

    _modo_cmd(parser.parse_args())
