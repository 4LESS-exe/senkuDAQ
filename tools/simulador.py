"""
simulador.py — Servidor TCP que simula el hardware ESP32 + HX711.
"""

import argparse
import math
import os
import random
import socket
import struct
import sys
import threading
import time

# Permitir importaciones desde la raíz del proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.protocol import construir_paquete, MAGIC_ACK


# ---------------------------------------------------------------------------
# Generador de señal (Copiado de arduino_sim.py)
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
# Clase Principal del Simulador TCP
# ---------------------------------------------------------------------------

class SimuladorTCP:
    def __init__(self, host: str, puerto: int, sps: int, base: float, 
                 peak_min: float, peak_max: float, ruido: float, 
                 semilla: int, simular_desconexion: bool):
        self._host                = host
        self._puerto              = puerto
        self._sps                 = sps
        self._base                = base
        self._peak_min            = peak_min
        self._peak_max            = peak_max
        self._ruido               = ruido
        self._simular_desconexion = simular_desconexion

        self._seq      = 0
        self._t_boot   = time.perf_counter()
        self._disparar = threading.Event()
        self._running  = True

        if semilla is not None:
            random.seed(semilla)

    def run(self) -> None:
        sep = "─" * 60
        print(sep)
        print("  Simulador TCP — SENKU DAQ")
        print(f"  Host: {self._host}:{self._puerto} | SPS: {self._sps}")
        print(f"  Desconexión simulada: {'ACTIVADA' if self._simular_desconexion else 'DESACTIVADA'}")
        print(sep)
        print("\n  Workflow:")
        print("    1. Ingresar host y puerto en la GUI y conectar")
        print("    2. Hacer TARA en la GUI (señal en reposo)")
        print("    3. Presionar \033[1mEnter\033[0m aquí para disparar un ensayo")
        print("    4. Repetir paso 3 las veces que quieras")
        print("    5. Ctrl+C para salir\n")
        print(sep + "\n")

        # Iniciar hilo de lectura de teclado
        threading.Thread(target=self._loop_esperar_enter, daemon=True).start()

        # Configuración del servidor
        try:
            servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            servidor.bind((self._host, self._puerto))
            servidor.listen(1)
            print(f"[SIM] Escuchando en {self._host}:{self._puerto}")
        except OSError as e:
            print(f"[SIM] Error al iniciar servidor TCP: {e}")
            return

        try:
            while self._running:
                cliente, addr = servidor.accept()
                print(f"\n[SIM] Cliente conectado desde {addr}")
                self._manejar_cliente(cliente)
                print("\n[SIM] Cliente desconectado")
        except KeyboardInterrupt:
            self._running = False
            print("\n[i] Deteniendo simulador de forma manual.")
        finally:
            servidor.close()

    def _loop_esperar_enter(self) -> None:
        while self._running:
            try:
                input()
                self._disparar.set()
            except EOFError:
                break

    def _manejar_cliente(self, cliente: socket.socket) -> None:
        curva           = None
        t_disparo       = 0.0
        n_ensayo        = 0
        n_enviados      = 0
        t_inicio_sesion = time.perf_counter()
        intervalo       = 1.0 / self._sps
        next_tick       = time.perf_counter()

        cliente.setblocking(False)

        print("\n  \033[2mEsperando... [Enter = disparar ensayo]\033[0m\n")

        while self._running:
            # 1. Chequear si se disparó un ensayo
            if self._disparar.is_set():
                self._disparar.clear()
                n_ensayo += 1
                curva = _curva_aleatoria(self._base, self._peak_min, self._peak_max, self._ruido)
                t_disparo = time.perf_counter()
                print(f"\n  \033[1;33m▶ Ensayo #{n_ensayo}\033[0m  "
                      f"pico={curva.pico:.0f}  rampa={curva.t_rampa:.1f}s  "
                      f"combustión={curva.t_combustion:.1f}s  caída={curva.t_caida:.1f}s\n")

            # 2. Calcular valor actual
            ahora = time.perf_counter()

            if curva is not None:
                t_rel = ahora - t_disparo
                valor = curva.evaluar(t_rel)
                if t_rel > curva.duracion_total + 1.5:
                    print(f"\n  \033[2m✓ Ensayo #{n_ensayo} terminado. [Enter = nuevo ensayo]\033[0m\n")
                    curva = None
            else:
                # Reposo: línea base con deriva lenta
                valor = self._base + 0.5 * math.sin(2 * math.pi * ahora / 7.3)
                valor += random.gauss(0.0, self._ruido or 30.0)

            # 3. Empaquetar y enviar
            timestamp_us = int((ahora - self._t_boot) * 1_000_000)
            paquete = construir_paquete(self._seq, timestamp_us, valor)
            self._seq += 1

            try:
                cliente.setblocking(True)
                cliente.sendall(paquete)
            except OSError:
                return  # Cliente desconectado

            n_enviados += 1

            # 4. Leer ACK si llegó (no bloqueante)
            try:
                cliente.setblocking(False)
                ack_raw = cliente.recv(6)
                if ack_raw and ack_raw[0] == MAGIC_ACK and len(ack_raw) == 6:
                    ack_seq = struct.unpack('<I', ack_raw[1:5])[0]
                    # Descomentar para debug de ACKs:
                    # print(f"[SIM] ACK recibido hasta SEQ {ack_seq}")
            except BlockingIOError:
                pass  # Normal, no hay datos
            except OSError:
                return

            # 5. Simular desconexión
            if self._simular_desconexion:
                if (time.perf_counter() - t_inicio_sesion) > 3.0:
                    print("\n[SIM] Simulando pérdida de conexión...")
                    cliente.close()
                    return

            # 6. Indicador por consola (1 vez/segundo)
            if n_enviados % self._sps == 0:
                if curva:
                    t_rel  = ahora - t_disparo
                    estado = f"ensayo #{n_ensayo}  t={t_rel:.1f}s  val={valor:.1f}"
                else:
                    estado = f"reposo  val={valor:.1f}"
                print(f"\r  {estado:<50}", end="", flush=True)

            # 7. Mantener sample rate
            next_tick += intervalo
            dormir = next_tick - time.perf_counter()
            if dormir > 0:
                time.sleep(dormir)


# ---------------------------------------------------------------------------
# Configuración CLI (Argparse)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simula ESP32+HX711 sobre puerto TCP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host",        type=str,   default="127.0.0.1")
    parser.add_argument("--puerto",      type=int,   default=8080)
    parser.add_argument("--sps",         type=int,   default=80)
    parser.add_argument("--base",        type=float, default=-100.0, help="Valor de línea base en reposo.")
    parser.add_argument("--peak-min",    dest="peak_min", type=float, default=-5000.0)
    parser.add_argument("--peak-max",    dest="peak_max", type=float, default=-3000.0)
    parser.add_argument("--ruido",       type=float, default=None, help="σ del ruido de cuantización (None = aleatorio 20-60).")
    parser.add_argument("--semilla",     type=int,   default=None)
    parser.add_argument("--desconexion", action="store_true", help="Activa la simulación de pérdida de conexión a los 3s.")

    args = parser.parse_args()
    
    SimuladorTCP(
        host                = args.host,
        puerto              = args.puerto,
        sps                 = args.sps,
        base                = args.base,
        peak_min            = args.peak_min,
        peak_max            = args.peak_max,
        ruido               = args.ruido,
        semilla             = args.semilla,
        simular_desconexion = args.desconexion,
    ).run()