"""
utils.py — Funciones utilitarias sin dependencias de GUI ni hardware.
"""

import numpy as np
import serial.tools.list_ports


def puertos_disponibles() -> list[str]:
    """Retorna la lista de puertos seriales detectados en el sistema."""
    return [p.device for p in serial.tools.list_ports.comports()]


def promedio_robusto(valores: list) -> tuple[float, float]:
    """
    Calcula la media con descarte IQR (rango intercuartílico).

    Retorna:
        (media, desviación_estándar)
    """
    arr = np.array(valores, dtype=float)
    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    mask = (arr >= q1 - 1.5 * iqr) & (arr <= q3 + 1.5 * iqr)
    filtrado = arr[mask]
    if len(filtrado) < 5:
        return float(np.median(arr)), float(np.std(arr))
    return float(np.mean(filtrado)), float(np.std(filtrado))
