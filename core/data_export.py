"""
data_export.py — Exportación de resultados del ensayo.

Genera los tres artefactos de salida agrupados en una subcarpeta única por ensayo:
  - CSV  (separador ; para Excel latinoamericano)
  - ENG  (formato OpenRocket, punto decimal)
  - PNG  (captura del PlotItem de PyQtGraph vía exportador nativo)
"""

import csv
import os
import time

import numpy as np

# Definimos la ruta base de exportación general
RUTA_EXPORTACION = os.path.join(os.path.expanduser("~"), "SenkuDAQ_Ensayos")


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
    Crea una carpeta para el ensayo actual y guarda CSV, ENG y PNG dentro.
    """
    if not datos:
        raise ValueError("No hay datos de ensayo para guardar.")

    # Generar el nombre base del ensayo
    timestamp   = time.strftime("%Y%m%d_%H%M%S")
    base_nombre = f"{nombre_motor}_{timestamp}"

    # Crear la subcarpeta específica para este ensayo
    ruta_ensayo_dir = os.path.join(RUTA_EXPORTACION, base_nombre)
    os.makedirs(ruta_ensayo_dir, exist_ok=True)

    tiempos = [t for t, _ in datos]
    empujes = [n for _, n in datos]

    # ---- CSV ----------------------------------------------------------------
    ruta_csv = os.path.join(ruta_ensayo_dir, f"{base_nombre}.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Tiempo (s)", "Empuje (N)"])
        for t, n in datos:
            w.writerow([
                f"{t:.4f}".replace(".", ","),
                f"{n:.4f}".replace(".", ","),
            ])

    # ---- ENG (OpenRocket) ---------------------------------------------------
    ruta_eng = os.path.join(ruta_ensayo_dir, f"{base_nombre}.eng")
    with open(ruta_eng, "w") as f:
        f.write(f"{nombre_motor} {diametro} {longitud} P {peso_prop} {peso_total} USACH\n")
        for t, n in datos:
            f.write(f"{t:.4f} {n:.4f}\n")
        f.write(f"{tiempos[-1] + 0.05:.4f} 0.0000\n")

    # ---- PNG (PyQtGraph ImageExporter) ------------------------------------
    ruta_png = os.path.join(ruta_ensayo_dir, f"{base_nombre}.png")
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
    impulso    = float(np.trapezoid(empujes, tiempos))
    max_empuje = max(empujes)
    duracion   = tiempos[-1] - tiempos[0]

    metricas = {
        "impulso_ns":   impulso,
        "max_empuje_n": max_empuje,
        "duracion_s":   duracion,
        "ruta_dir":     ruta_ensayo_dir,
        "ruta_csv":     ruta_csv,
        "ruta_eng":     ruta_eng,
        "ruta_png":     ruta_png,
    }

    print(f"\n[✓] Archivos guardados → {ruta_ensayo_dir}")
    print(f"    Impulso total : {impulso:.4f} N·s")
    print(f"    Empuje máximo : {max_empuje:.3f} N")
    print(f"    Duración      : {duracion:.3f} s")

    return metricas


def resumen_texto(metricas: dict, nombre_motor: str) -> str:
    """Genera el texto del mensaje de resumen para mostrar en la GUI."""
    return (
        f"Motor         : {nombre_motor}\n"
        f"Empuje máx    : {metricas['max_empuje_n']:.3f} N\n"
        f"Impulso total : {metricas['impulso_ns']:.4f} N·s\n"
        f"Duración      : {metricas['duracion_s']:.3f} s\n\n"
        f"Archivos agrupados en:\n{metricas['ruta_dir']}\n"
    )