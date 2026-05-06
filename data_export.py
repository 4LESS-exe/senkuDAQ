"""
data_export.py — Exportación de resultados del ensayo.

Genera los tres artefactos de salida:
  - CSV  (separador ; para Excel latinoamericano)
  - ENG  (formato OpenRocket, punto decimal)
  - PNG  (captura del PlotItem de PyQtGraph vía exportador nativo)

No tiene dependencias de Qt en el nivel de módulo; recibe el PlotItem
como parámetro opcional para que sea fácil portar esta capa en el futuro.
"""

import csv
import os
import time

import numpy as np

from config import RUTA_BASE


# ---------------------------------------------------------------------------
# TIPO AUXILIAR
# ---------------------------------------------------------------------------

PuntosEnsayo = list[tuple[float, float]]   # [(tiempo_s, empuje_N), ...]


# ---------------------------------------------------------------------------
# EXPORTACIÓN
# ---------------------------------------------------------------------------

def guardar_ensayo(
    datos: PuntosEnsayo,
    nombre_motor: str,
    diametro: str,
    longitud: str,
    peso_prop: str,
    peso_total: str,
    plot_item=None,          # pyqtgraph.PlotItem (opcional, para el PNG)
    plot_bg: str = "#ffffff",
) -> dict:
    """
    Guarda CSV, ENG y PNG a partir de los datos del ensayo.

    Args:
        datos:        Lista de tuplas (tiempo_s, empuje_N).
        nombre_motor: Nombre del motor (usado en el nombre de archivo y ENG).
        diametro:     Diámetro del motor en mm (string).
        longitud:     Longitud del motor en mm (string).
        peso_prop:    Peso del propelente en kg (string).
        peso_total:   Peso total del motor en kg (string).
        plot_item:    pyqtgraph.PlotItem activo (para exportar PNG).
                      Si es None, el PNG se omite.
        plot_bg:      Color de fondo (no usado directamente; PyQtGraph
                      respeta el fondo del widget al exportar).

    Returns:
        Diccionario con métricas del ensayo:
        {'impulso_ns', 'max_empuje_n', 'duracion_s', 'ruta_csv', 'ruta_eng', 'ruta_png'}
    """
    if not datos:
        raise ValueError("No hay datos de ensayo para guardar.")

    os.makedirs(RUTA_BASE, exist_ok=True)
    timestamp   = time.strftime("%Y%m%d_%H%M%S")
    base_nombre = f"{nombre_motor}_{timestamp}"

    tiempos = [t for t, _ in datos]
    empujes = [n for _, n in datos]

    # ---- CSV ----------------------------------------------------------------
    ruta_csv = os.path.join(RUTA_BASE, f"{base_nombre}.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Tiempo (s)", "Empuje (N)"])
        for t, n in datos:
            w.writerow([
                f"{t:.4f}".replace(".", ","),
                f"{n:.4f}".replace(".", ","),
            ])

    # ---- ENG (OpenRocket) ---------------------------------------------------
    ruta_eng = os.path.join(RUTA_BASE, f"{base_nombre}.eng")
    with open(ruta_eng, "w") as f:
        f.write(f"{nombre_motor} {diametro} {longitud} P {peso_prop} {peso_total} USACH\n")
        for t, n in datos:
            f.write(f"{t:.4f} {n:.4f}\n")
        f.write(f"{tiempos[-1] + 0.05:.4f} 0.0000\n")

    # ---- PNG (PyQtGraph ImageExporter) ------------------------------------
    ruta_png = os.path.join(RUTA_BASE, f"{base_nombre}.png")
    if plot_item is not None:
        try:
            import pyqtgraph.exporters as pgexp
            exporter = pgexp.ImageExporter(plot_item)
            exporter.parameters()["width"] = 1200
            exporter.export(ruta_png)
        except Exception as e:
            print(f"[!] No se pudo exportar PNG: {e}")
            ruta_png = ""
    else:
        ruta_png = ""

    # ---- Métricas -----------------------------------------------------------
    impulso    = float(np.trapz(empujes, tiempos))
    max_empuje = max(empujes)
    duracion   = tiempos[-1] - tiempos[0]

    metricas = {
        "impulso_ns":   impulso,
        "max_empuje_n": max_empuje,
        "duracion_s":   duracion,
        "ruta_csv":     ruta_csv,
        "ruta_eng":     ruta_eng,
        "ruta_png":     ruta_png,
    }

    print(f"\n[✓] Archivos guardados → {RUTA_BASE}")
    print(f"    Impulso total : {impulso:.4f} N·s")
    print(f"    Empuje máximo : {max_empuje:.3f} N")
    print(f"    Duración      : {duracion:.3f} s")

    return metricas


def resumen_texto(metricas: dict, nombre_motor: str) -> str:
    """Genera el texto del mensaje de resumen para mostrar en la GUI."""
    png_line = f"PNG           : {metricas['ruta_png']}\n" if metricas.get("ruta_png") else ""
    return (
        f"Motor         : {nombre_motor}\n"
        f"Empuje máx    : {metricas['max_empuje_n']:.3f} N\n"
        f"Impulso total : {metricas['impulso_ns']:.4f} N·s\n"
        f"Duración      : {metricas['duracion_s']:.3f} s\n\n"
        f"Archivos guardados en:\n{RUTA_BASE}\n"
        f"{png_line}"
    )
