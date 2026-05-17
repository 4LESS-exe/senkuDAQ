"""
serial_reader.py — Hilo dedicado a la lectura del puerto serial.

El lector deposita floats válidos en una queue thread-safe (self.cola).
Ignora ceros (señal de error del HX711) y líneas corruptas.

Se cambio el metodo a wireless_reader.py, actualmente serial reader.py es un "dummy" para mantener compatibilidad con app.py, 
pero ya no realiza ninguna operación de lectura serial.

"""

import threading
import queue
import time
import numpy as np
import serial

class LectorSerial(threading.Thread):
    """
    Hilo de lectura serial simplificado para Senku DAQ.
    Se ha eliminado el filtro de Kalman para trabajar con datos crudos del ADC.
    """

    def __init__(self, puerto: str, baudrate: int, **kwargs):
        super().__init__(daemon=True)
        self.cola: queue.Queue[float] = queue.Queue(maxsize=2000)
        self._stop_event = threading.Event()
        self._puerto     = puerto
        self._baudrate   = baudrate
        self.ser: serial.Serial | None = None
        self.error: str   = ""
        self.bloqueo_gui  = False

    def run(self):
        print(f"[i] Iniciando hilo de lectura en {self._puerto}...", flush=True)
        try:
            self.ser = serial.Serial(self._puerto, self._baudrate, timeout=0.1)
            time.sleep(2.5) # Espera de estabilización para el reset de Arduino[cite: 6]
            self.ser.reset_input_buffer()
            print(f"[✓] Puerto {self._puerto} abierto exitosamente.", flush=True)
        except Exception as e:
            self.error = f"Error: {e}"
            print(f"[✗] {self.error}", flush=True)
            return

        while not self._stop_event.is_set():
            if self.ser is None or not self.ser.is_open:
                break
            try:
                if self.ser.in_waiting > 0:
                    linea_cruda = self.ser.readline()
                    linea = linea_cruda.decode("utf-8", errors="ignore").strip()
                    if not linea:
                        continue
                    
                    val = float(linea)
                    if not np.isfinite(val):
                        continue

                    try:
                        self.cola.put_nowait(val)
                    except queue.Full:
                        pass
                else:
                    time.sleep(0.001)
            except (ValueError, UnicodeDecodeError):
                continue
            except Exception as e:
                print(f"[!] Error serial: {e}", flush=True)
                break

    def detener(self):
        self._stop_event.set()
        if self.ser and self.ser.is_open:
            self.ser.close()

    def reset_kalman(self, *args, **kwargs):
        """
        Método 'dummy' para mantener compatibilidad con app.py.
        Ya no realiza ninguna operación de filtrado.
        """
        pass

    def leer_bloqueante(self, n: int, timeout: float = 60.0) -> list[float]:
        self.bloqueo_gui = True
        while not self.cola.empty():
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break

        valores = []
        t0 = time.time()
        print(f"\n[i] Capturando {n} muestras crudas...", flush=True)

        while len(valores) < n:
            elapsed = time.time() - t0
            if elapsed > timeout:
                print(f"[!] Timeout reached after {elapsed:.1f}s, collected {len(valores)}/{n} samples", flush=True)
                self.bloqueo_gui = False
                raise RuntimeError(f"Timeout: solo {len(valores)}/{n} muestras recibidas.")
            try:
                val = self.cola.get(timeout=1.0)
                valores.append(val)
                print(f"[i] Sample {len(valores)}: {val}", flush=True)
            except queue.Empty:
                print(f"[i] Queue empty, elapsed {elapsed:.1f}s", flush=True)

        self.bloqueo_gui = False
        print(f"[✓] Collected {len(valores)} samples successfully", flush=True)
        return valores