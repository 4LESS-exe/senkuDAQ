"""
wireless_reader.py — Lector y simulador de datos inalámbricos vía TCP.
Mantiene la misma interfaz pública que LectorSerial.
"""

import queue
import socket
import struct
import threading
import time
import random
from collections import deque
from typing import List, Optional

# Dependencias del proyecto
import core.protocol as protocol

# ===========================================================================
# CONSTANTES DEL MÓDULO
# ===========================================================================

PKT_SIZE        = 14        # bytes por paquete de datos
MAGIC_DATA      = 0xAA
MAGIC_ACK       = 0xBB
ACK_CADA_N      = 10        # enviar ACK cada N paquetes recibidos
TIMEOUT_SOCKET  = 2.0       # segundos antes de considerar socket muerto
BACKOFF_INICIAL = 0.5       # segundos entre reintentos de conexión
BACKOFF_MAX     = 5.0       # techo del backoff exponencial
VENTANA_SEÑAL   = 50        # últimos N paquetes para medir calidad

# Asumimos 80 SPS por defecto si no está definido explícitamente en config
SAMPLE_RATE_EXPECTED = 80  


# ===========================================================================
# CLASE PRINCIPAL: LectorWireless
# ===========================================================================

class LectorWireless(threading.Thread):
    """
    Cliente TCP que recibe lecturas del ESP32.
    Interfaz pública idéntica a LectorSerial.
    """

    def __init__(self, host: str, puerto: int, sample_rate: int = SAMPLE_RATE_EXPECTED):
        super().__init__(daemon=True)
        
        # Atributos Públicos
        self.cola: queue.Queue[float] = queue.Queue(maxsize=5000)
        self.error: str = ""
        self.bloqueo_gui: bool = False
        self.señal_conexion: int = 0
        
        # Atributos Privados
        self._host = host
        self._puerto = puerto
        self._sample_rate = sample_rate
        self._stop_event = threading.Event()
        self._socket: Optional[socket.socket] = None
        self._ultimo_seq_ack: int = 0
        self._seq_esperado: int = 0
        self._contador_ack: int = 0
        self._timestamps_recientes: deque[float] = deque(maxlen=VENTANA_SEÑAL)

    def run(self) -> None:
        """Loop externo: maneja el ciclo de vida y reconexiones."""
        print(f"[i] Iniciando hilo LectorWireless hacia {self._host}:{self._puerto}...")
        
        while not self._stop_event.is_set():
            éxito = self._intentar_conectar()
            
            if not éxito:
                if self.error:
                    print(f"[✗] {self.error}")
                    return  # Error fatal, terminar hilo
                continue    # Reintentar (backoff ya aplicado)
                
            # Conexión establecida: entrar al loop de recepción
            self._loop_recepcion()
            
            # Si llegamos aquí, la conexión se perdió
            self.señal_conexion = 0
            if self._stop_event.is_set():
                return
                
            print("[!] Conexión perdida. Reconectando...")

    def _intentar_conectar(self) -> bool:
        """Intenta establecer conexión TCP con backoff exponencial."""
        backoff = BACKOFF_INICIAL
        max_reintentos = 10
        intentos = 0
        
        while intentos < max_reintentos and not self._stop_event.is_set():
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(TIMEOUT_SOCKET)
                self._socket.connect((self._host, self._puerto))
                
                # Enviar ACK inmediato con el último seq conocido para resincronizar
                self._enviar_ack(self._ultimo_seq_ack)
                
                print(f"[✓] Conectado a {self._host}:{self._puerto}")
                self.señal_conexion = 100
                return True
                
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                print(f"[!] Fallo al conectar: {e}. Reintentando en {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                intentos += 1

        self.error = f"No se pudo conectar a {self._host}:{self._puerto} tras {max_reintentos} intentos"
        return False

    def _loop_recepcion(self) -> None:
        """Loop interno de lectura y parseo de paquetes."""
        while not self._stop_event.is_set():
            raw = self._leer_paquete_del_socket()
            
            if raw is None:
                return # Socket cerrado o timeout
                
            resultado = protocol.parsear_paquete(raw)
            if resultado is None:
                continue # Checksum malo o magic incorrecto
                
            seq, timestamp_us, value = resultado
            
            # Detectar gap en secuencia
            if seq != self._seq_esperado and self._seq_esperado != 0:
                gap = seq - self._seq_esperado
                print(f"[!] Gap de {gap} paquetes entre SEQ {self._seq_esperado} y {seq}")
                
            self._seq_esperado = seq + 1
            
            # Poner en cola (descartando más viejo si está llena)
            try:
                self.cola.put_nowait(value)
            except queue.Full:
                try:
                    self.cola.get_nowait()
                except queue.Empty:
                    pass
                self.cola.put_nowait(value)

            # Actualizar métricas
            self._timestamps_recientes.append(time.monotonic())
            self._actualizar_señal_conexion()
            
            # ACK periódico
            self._contador_ack += 1
            self._ultimo_seq_ack = seq
            if self._contador_ack >= ACK_CADA_N:
                self._enviar_ack(seq)
                self._contador_ack = 0

    def _leer_paquete_del_socket(self) -> Optional[bytes]:
        """Lee exactamente PKT_SIZE bytes con resincronización automática."""
        if not self._socket:
            return None
            
        try:
            # Reutiliza la función robusta del protocolo
            return protocol.leer_paquete_seguro(self._socket)
        except socket.timeout:
            self.señal_conexion = 0
            return None
        except OSError:
            return None

    def _enviar_ack(self, seq: int) -> None:
        """Envía paquete de acuse de recibo."""
        if not self._socket:
            return
            
        paquete_ack = protocol.construir_ack(seq)
        try:
            self._socket.sendall(paquete_ack)
        except OSError:
            pass # El fallo se detectará en la próxima lectura

    def _actualizar_señal_conexion(self) -> None:
        """Calcula señal (0-100) basado en tasa real vs esperada."""
        if len(self._timestamps_recientes) < 2:
            self.señal_conexion = 100
            return
            
        t_inicio = self._timestamps_recientes[0]
        t_fin = self._timestamps_recientes[-1]
        n = len(self._timestamps_recientes)
        duracion = t_fin - t_inicio
        
        if duracion <= 0:
            return
            
        tasa_real = (n - 1) / duracion
        ratio = tasa_real / self._sample_rate
        
        # Clamp entre 0 y 100
        self.señal_conexion = int(max(0, min(100, ratio * 100)))

    def detener(self) -> None:
        """Cierra el hilo y el socket de forma limpia."""
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass

    def leer_bloqueante(self, n: int, timeout: float = 60.0) -> List[float]:
        """Extrae 'n' muestras bloqueando la GUI temporalmente (igual que serial)."""
        self.bloqueo_gui = True
        
        # Vaciar cola completamente
        while not self.cola.empty():
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break
                
        valores = []
        t0 = time.monotonic()
        print(f"\n[i] Capturando {n} muestras inalámbricas...", flush=True)
        
        while len(valores) < n:
            if (time.monotonic() - t0) > timeout:
                self.bloqueo_gui = False
                raise RuntimeError(f"Timeout: {len(valores)}/{n} muestras")
                
            try:
                val = self.cola.get(timeout=1.0)
                valores.append(val)
            except queue.Empty:
                continue
                
        self.bloqueo_gui = False
        return valores


# ===========================================================================
# CLASE DE PRUEBAS: SimuladorESP32
# ===========================================================================

class SimuladorESP32(threading.Thread):
    """
    Servidor TCP que imita al hardware ESP32 para desarrollar sin placa.
    """

    def __init__(self, puerto_tcp: int, sample_rate: int = SAMPLE_RATE_EXPECTED, simular_desconexion: bool = False):
        super().__init__(daemon=True)
        self._puerto = puerto_tcp
        self._sample_rate = sample_rate
        self._simular_desconexion = simular_desconexion
        self._seq = 0
        self._t_inicio = time.monotonic()

    def run(self) -> None:
        try:
            servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            servidor.bind(("127.0.0.1", self._puerto))
            servidor.listen(1)
            print(f"[SIM] Escuchando en 127.0.0.1:{self._puerto}")
        except OSError as e:
            print(f"[SIM] Error al iniciar servidor: {e}")
            return

        while True:
            try:
                cliente, addr = servidor.accept()
                print(f"[SIM] Cliente conectado desde {addr}")
                self._manejar_cliente(cliente)
            except OSError:
                break

    def _manejar_cliente(self, cliente: socket.socket) -> None:
        t_inicio_cliente = time.monotonic()
        intervalo = 1.0 / self._sample_rate
        
        while True:
            t_ciclo = time.monotonic()
            t_ensayo = t_ciclo - self._t_inicio
            
            # Generar valor sintético
            if 2.0 < t_ensayo < 5.0:
                value = 8000.0 + random.gauss(0, 50)  # "Motor quemando"
            else:
                value = random.gauss(0, 30)           # "En reposo"
                
            timestamp_us = int((t_ciclo - self._t_inicio) * 1_000_000)
            paquete = self._construir_paquete_datos(self._seq, timestamp_us, value)
            self._seq += 1
            
            try:
                cliente.sendall(paquete)
            except OSError:
                print("[SIM] Cliente desconectado")
                cliente.close()
                return

            # Leer ACK si llegó (no bloqueante)
            cliente.setblocking(False)
            try:
                ack_raw = cliente.recv(protocol.ACK_SIZE)
                if ack_raw and ack_raw[0] == MAGIC_ACK and len(ack_raw) == protocol.ACK_SIZE:
                    # Validar checksum básico y extraer seq
                    if protocol.calcular_checksum(ack_raw[0:5]) == ack_raw[5]:
                        ack_seq = struct.unpack('<I', ack_raw[1:5])[0]
                        print(f"[SIM] ACK recibido hasta SEQ {ack_seq}")
            except BlockingIOError:
                pass # No hay datos
            except OSError:
                pass
            finally:
                cliente.setblocking(True)
                
            # Simular pérdida de conexión controlada
            if self._simular_desconexion and (time.monotonic() - t_inicio_cliente) > 3.0:
                print("[SIM] Simulando pérdida de conexión...")
                cliente.close()
                return
                
            # Mantener sample rate estable
            tiempo_transcurrido = time.monotonic() - t_ciclo
            if tiempo_transcurrido < intervalo:
                time.sleep(intervalo - tiempo_transcurrido)

    def _construir_paquete_datos(self, seq: int, ts_us: int, val: float) -> bytes:
        """Helper local para empaquetar datos desde el simulador."""
        buf = bytearray()
        buf.append(MAGIC_DATA)
        buf.extend(struct.pack('<I', seq))
        buf.extend(struct.pack('<I', ts_us))
        buf.extend(struct.pack('<f', val))
        
        chk = protocol.calcular_checksum(buf)
        buf.append(chk)
        return bytes(buf)