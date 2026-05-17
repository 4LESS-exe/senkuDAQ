#!/usr/bin/env python3
"""
arduino_sim.py — Simula el Arduino+HX711 escribiendo en un puerto serial virtual.
 
Flujo:
    arduino_sim.py  →  /dev/pts/N  →  LectorSerial (sin cambios)  →  GUI
 
Workflow:
    1. Ejecutar este script  →  anota /dev/pts/N
    2. Ingresar el puerto en la GUI y conectar
    3. Hacer TARA en la GUI (sensor en reposo)
    4. Presionar Enter aquí para disparar un ensayo
    5. Repetir desde paso 4 cuantas veces se quiera

Actualmente no hace nada debido a que se cambio el metodo a wireless_reader.py, pero se mantiene como referencia para futuras pruebas con puerto serial.
"""
 
import argparse
import math
import os
import pty
import random
import signal
import sys
import termios
import threading
import time
 
 
# ---------------------------------------------------------------------------
# Generador de señal
# ---------------------------------------------------------------------------
 
class _GeneradorCurva:
    def __init__(self, valor_base, pico, t_rampa, t_combustion, t_caida,
                 ruido_std, flutter_std):
        self._base    = valor_base
        self._pico    = pico
        self._ruido   = ruido_std
        self._flutter = flutter_std
        # Disparo inmediato (t_ignicion = 0)
        self._t1 = t_rampa
        self._t2 = t_rampa + t_combustion
        self._t3 = t_rampa + t_combustion + t_caida
 
        self.pico         = pico
        self.t_rampa      = t_rampa
        self.t_combustion = t_combustion
        self.t_caida      = t_caida
 
    def evaluar(self, t: float) -> float:
        delta = self._pico - self._base
 
        if t < self._t1:
            frac  = t / self._t1
            señal = self._base + delta * (1.0 - math.exp(-5.0 * frac))
            señal += random.gauss(0.0, self._flutter * frac)
 
        elif t < self._t2:
            frac   = (t - self._t1) / (self._t2 - self._t1)
            ondula = 0.03 * delta * math.sin(2 * math.pi * frac * 3.1)
            señal  = self._pico + ondula
            señal += random.gauss(0.0, self._flutter)
 
        elif t < self._t3:
            frac  = (t - self._t2) / (self._t3 - self._t2)
            señal = self._base + delta * math.exp(-4.5 * frac)
            señal += random.gauss(0.0, self._flutter * (1.0 - frac))
 
        else:
            señal = self._base
 
        return señal + random.gauss(0.0, self._ruido)
 
    @property
    def duracion_total(self) -> float:
        return self._t3
 
 
def _curva_aleatoria(base, peak_min, peak_max, ruido_std=None):
    return _GeneradorCurva(
        valor_base   = base,
        pico         = random.uniform(peak_min, peak_max),
        t_rampa      = random.uniform(0.3,  1.2),
        t_combustion = random.uniform(3.0,  8.0),
        t_caida      = random.uniform(0.5,  2.0),
        ruido_std    = ruido_std if ruido_std is not None else random.uniform(20.0, 60.0),
        flutter_std  = random.uniform(abs(peak_min), abs(peak_max)) * random.uniform(0.01, 0.04),
    )
 
 
# ---------------------------------------------------------------------------
# Puerto serial virtual (PTY)
# ---------------------------------------------------------------------------
 
def _abrir_pty():
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(master)
    attrs[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(master, termios.TCSANOW, attrs)
    slave_name = os.ttyname(slave)
    os.close(slave)
    return master, slave_name
 
 
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
 
def main(args):
    if args.semilla is not None:
        random.seed(args.semilla)
 
    try:
        master, puerto = _abrir_pty()
    except Exception as e:
        print(f"[✗] No se pudo crear el puerto virtual: {e}", file=sys.stderr)
        sys.exit(1)
 
    sep = "─" * 58
    print(sep)
    print("  Arduino Simulator — HX711 sobre puerto serial virtual")
    print(sep)
    print(f"\n  Puerto virtual listo: \033[1;32m{puerto}\033[0m")
    print(f"\n  Workflow:")
    print(f"    1. Ingresar \033[1m{puerto}\033[0m en la GUI y conectar")
    print(f"    2. Hacer TARA en la GUI (señal en reposo)")
    print(f"    3. Presionar \033[1mEnter\033[0m aquí para disparar un ensayo")
    print(f"    4. Repetir paso 3 las veces que quieras")
    print(f"    5. Ctrl+C para salir\n")
    print(sep + "\n")
 
    running = True
    def _salir(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT,  _salir)
    signal.signal(signal.SIGTERM, _salir)
 
    _disparar = threading.Event()
 
    def _esperar_enter():
        while running:
            try:
                input()
                _disparar.set()
            except EOFError:
                break
 
    threading.Thread(target=_esperar_enter, daemon=True).start()
 
    intervalo  = 1.0 / args.sps
    next_tick  = time.perf_counter()
    n_enviados = 0
    n_ensayo   = 0
    curva      = None
    t_disparo  = 0.0
 
    print(f"  \033[2mEsperando... [Enter = disparar ensayo]\033[0m\n")
 
    while running:
        ahora = time.perf_counter()
 
        # Nuevo disparo
        if _disparar.is_set():
            _disparar.clear()
            n_ensayo += 1
            curva     = _curva_aleatoria(args.base, args.peak_min,
                                         args.peak_max, args.ruido)
            t_disparo = ahora
            print(f"\n  \033[1;33m▶ Ensayo #{n_ensayo}\033[0m  "
                  f"pico={curva.pico:.0f}  "
                  f"rampa={curva.t_rampa:.1f}s  "
                  f"combustión={curva.t_combustion:.1f}s  "
                  f"caída={curva.t_caida:.1f}s\n")
 
        # Calcular valor
        if curva is not None:
            t_rel = ahora - t_disparo
            val   = curva.evaluar(t_rel)
            if t_rel > curva.duracion_total + 1.5:
                print(f"\n  \033[2m✓ Ensayo #{n_ensayo} terminado. "
                      f"[Enter = nuevo ensayo]\033[0m\n")
                curva = None
        else:
            # Reposo: línea base con deriva térmica lenta
            val  = args.base + 0.5 * math.sin(2 * math.pi * ahora / 7.3)
            val += random.gauss(0.0, args.ruido or 30.0)
 
        # Escribir al puerto
        try:
            os.write(master, f"{val:.2f}\n".encode())
            n_enviados += 1
        except OSError:
            print("\n[i] La GUI cerró el puerto.")
            break
 
        # Indicador (1 vez por segundo)
        if n_enviados % args.sps == 0:
            if curva:
                t_rel  = ahora - t_disparo
                estado = f"ensayo #{n_ensayo}  t={t_rel:.1f}s"
            else:
                estado = "reposo"
            print(f"\r  {estado:<30}  val={val:>10.1f}",
                  end="", flush=True)
 
        next_tick += intervalo
        dormir = next_tick - time.perf_counter()
        if dormir > 0:
            time.sleep(dormir)
 
    os.close(master)
    print(f"\n\n{sep}")
    print(f"  Ensayos : {n_ensayo}   Muestras : {n_enviados}")
    print(f"  Simulación finalizada.")
    print(sep)
 
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simula Arduino+HX711 sobre puerto serial virtual (PTY).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sps",      type=int,   default=80)
    parser.add_argument("--base",     type=float, default=-100.0,
                        help="Valor de línea base en reposo.")
    parser.add_argument("--peak-min", dest="peak_min", type=float, default=-5000.0)
    parser.add_argument("--peak-max", dest="peak_max", type=float, default=-3000.0)
    parser.add_argument("--ruido",    type=float, default=None,
                        help="σ del ruido de cuantización (None = aleatorio 20-60).")
    parser.add_argument("--semilla",  type=int,   default=None)
    main(parser.parse_args())
