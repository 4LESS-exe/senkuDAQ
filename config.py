"""
config.py — Constantes globales, paleta de colores y persistencia de configuración.
"""

import os
import json

# ---------------------------------------------------------------------------
# VERSIÓN Y RUTAS
# ---------------------------------------------------------------------------

APP_VERSION  = "2.0"
_DIR_SCRIPT  = os.path.dirname(os.path.abspath(__file__))
RUTA_BASE    = os.path.join(_DIR_SCRIPT, "ensayos_senku")
RUTA_CONFIG  = os.path.join(RUTA_BASE, "config.json")

# ---------------------------------------------------------------------------
# PARÁMETROS FÍSICOS Y DE MUESTREO
# ---------------------------------------------------------------------------

GRAVEDAD         = 9.80665
TICK_MS          = 40    # ms entre actualizaciones de GUI (~25 fps)
N_LECTURAS_CAL   = 150   # lecturas para promedio en calibración
BUFFER_GRAFICO   = 500   # puntos visibles en el gráfico en tiempo real

# ---------------------------------------------------------------------------
# CONFIGURACIÓN POR DEFECTO
# ---------------------------------------------------------------------------

CONFIG_DEFAULT = {
    "puerto":               "",
    "baudrate":             115200,
    "factor_escala":        109324.0,
    # Motor
    "motor_nombre":         "Senku_1",
    "motor_diametro":       "20",
    "motor_longitud":       "100",
    "motor_peso_prop":      "0.100",
    "motor_peso_total":     "0.150",
    # Umbrales (como % del rango esperado)
    "rango_esperado_n":     10.0,   # Fuerza máxima esperada [N]
    "umbral_ignicion_pct":  5.0,    # % del rango para detectar ignición
    "umbral_apagado_pct":   2.0,    # % del rango para detectar fin de empuje
    "tiempo_minimo_s":      0.3,    # s mínimos de quemado (evita falsos positivos)
    "buffer_pre_s":         1.0,    # s de pre-ignición a conservar
}

# ---------------------------------------------------------------------------
# PALETA DE COLORES (tema claro industrial)
# ---------------------------------------------------------------------------

C = {
    "bg":        "#f0f0f0",
    "panel":     "#e0e0e0",
    "border":    "#b0b0b0",
    "accent":    "#c0392b",
    "accent2":   "#d68910",
    "green":     "#1e8449",
    "blue":      "#1a5276",
    "text":      "#1a1a1a",
    "text_dim":  "#555555",
    "plot_bg":   "#ffffff",
    "plot_grid": "#dddddd",
    "plot_line": "#c0392b",
    "plot_pre":  "#cccccc",
}

# ---------------------------------------------------------------------------
# FUNCIONES DE PERSISTENCIA
# ---------------------------------------------------------------------------

def cargar_config() -> dict:
    """Carga config.json y fusiona con CONFIG_DEFAULT para campos nuevos."""
    os.makedirs(RUTA_BASE, exist_ok=True)
    if os.path.exists(RUTA_CONFIG):
        try:
            with open(RUTA_CONFIG) as f:
                data = json.load(f)
            merged = CONFIG_DEFAULT.copy()
            merged.update(data)
            return merged
        except Exception:
            pass
    return CONFIG_DEFAULT.copy()


def guardar_config(cfg: dict):
    """Persiste el diccionario de configuración en config.json."""
    os.makedirs(RUTA_BASE, exist_ok=True)
    with open(RUTA_CONFIG, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
