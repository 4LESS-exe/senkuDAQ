"""
protocol.py — Manejo del protocolo de comunicación (Capa de Red/Enlace).
Independiente de la GUI. Opera únicamente sobre sockets y bytes.
"""

import struct
import socket
from typing import Tuple, Optional

# ===========================================================================
# CONSTANTES DEL PROTOCOLO
# ===========================================================================

MAGIC_DATA = 0xAA
MAGIC_ACK  = 0xBB
PKT_SIZE   = 14
ACK_SIZE   = 6
ACK_CADA_N = 10


# ===========================================================================
# FUNCIONES DE EMPAQUETADO Y DESEMPAQUETADO
# ===========================================================================

def calcular_checksum(data: bytes | bytearray) -> int:
    """
    Calcula el checksum mediante la operación XOR de todos los bytes del arreglo.
    """
    checksum = 0
    for b in data:
        checksum ^= b
    return checksum

def parsear_paquete(raw_bytes: bytes) -> Optional[Tuple[int, int, float]]:
    """
    Verifica y extrae la información de un paquete de datos entrante de 14 bytes.
    Retorna (seq, timestamp_us, value) o None si el paquete es inválido.
    """
    if len(raw_bytes) != PKT_SIZE:
        return None
        
    if raw_bytes[0] != MAGIC_DATA:
        return None
        
    # Validar integridad del paquete usando los primeros 13 bytes
    if calcular_checksum(raw_bytes[0:13]) != raw_bytes[13]:
        return None

    # '<I' indica formato Little-Endian, unsigned int de 32 bits
    # '<f' indica formato Little-Endian, float de 32 bits
    seq = struct.unpack('<I', raw_bytes[1:5])[0]
    timestamp_us = struct.unpack('<I', raw_bytes[5:9])[0]
    value = struct.unpack('<f', raw_bytes[9:13])[0]

    return (seq, timestamp_us, value)

def construir_ack(ack_seq: int) -> bytes:
    """
    Construye un paquete ACK de 6 bytes para acusar recibo de un número de secuencia.
    Estructura: [MAGIC_ACK (1)] + [ack_seq (4)] + [checksum (1)]
    """
    buf = bytearray()
    buf.append(MAGIC_ACK)
    buf.extend(struct.pack('<I', ack_seq))
    
    # Calcular y añadir checksum
    chk = calcular_checksum(buf)
    buf.append(chk)
    
    return bytes(buf)

def construir_paquete(seq: int, timestamp_us: int, value: float) -> bytes:
    """
    Construye un paquete de datos de 14 bytes para enviar.
    Estructura: [MAGIC_DATA (1)] + [seq (4)] + [timestamp_us (4)] + [value (4)] + [checksum (1)]
    """
    buf = bytearray()
    buf.append(MAGIC_DATA)
    
    # '<I' indica formato Little-Endian, unsigned int de 32 bits
    # '<f' indica formato Little-Endian, float de 32 bits
    buf.extend(struct.pack('<I', seq))
    buf.extend(struct.pack('<I', timestamp_us))
    buf.extend(struct.pack('<f', value))
    
    # Calcular y añadir checksum al final
    chk = calcular_checksum(buf)
    buf.append(chk)
    
    return bytes(buf)


# ===========================================================================
# FUNCIONES DE RED
# ===========================================================================

def leer_paquete_seguro(sock: socket.socket) -> Optional[bytes]:
    """
    Lee desde el socket buscando de forma segura el byte de sincronía (MAGIC_DATA),
    y luego recolecta exactamente el resto del paquete.
    Retorna el paquete completo de 14 bytes, o None si se cierra la conexión.
    """
    while True:
        try:
            # Buscar byte de sincronía en el stream
            b = sock.recv(1)
            
            # Si recibimos un string vacío, la conexión se ha cerrado
            if not b:
                return None
                
            if b[0] == MAGIC_DATA:
                # Byte mágico encontrado. Leer los 13 bytes restantes.
                # Se usa un bucle para evitar errores si el paquete llega fragmentado.
                resto = bytearray()
                bytes_faltantes = PKT_SIZE - 1
                
                while len(resto) < bytes_faltantes:
                    chunk = sock.recv(bytes_faltantes - len(resto))
                    if not chunk:
                        return None # Conexión cerrada durante la lectura
                    resto.extend(chunk)
                    
                return bytes(b) + bytes(resto)
                
        except (socket.error, socket.timeout):
            # Dependiendo de si el socket es bloqueante o no, podrías manejar
            # el timeout aquí retornando None o un paquete vacío.
            return None