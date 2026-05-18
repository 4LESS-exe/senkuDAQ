"""
engine.py — Motor de procesamiento de ensayos y lógica de negocio.
Maneja la conversión de señales, detección de eventos de motor y matemáticas de calibración.
Independiente de GUI y hardware.
"""

from dataclasses import dataclass
from enum import Enum
from collections import deque
from typing import List, Tuple, Optional
import numpy as np

# Importaciones locales permitidas
from core.utils import promedio_robusto
from core.config import GRAVEDAD

# ===========================================================================
# CONSTANTES
# ===========================================================================

_CLASES_NFPA: List[Tuple[float, float, str]] = [
    (0.000,      2.5, "1/4A"),
    (2.5,        5.0, "1/2A"),
    (5.0,       10.0, "A"),
    (10.0,      20.0, "B"),
    (20.0,      40.0, "C"),
    (40.0,      80.0, "D"),
    (80.0,     160.0, "E"),
    (160.0,    320.0, "F"),
    (320.0,    640.0, "G"),
    (640.0,   1280.0, "H"),
    (1280.0,  2560.0, "I"),
    (2560.0,  5120.0, "J"),
    (5120.0, 10240.0, "K"),
    (10240.0,20480.0, "L"),
    (20480.0,40960.0, "M"),
    (40960.0,81920.0, "N"),
    (81920.0,163840.0,"O"),
]

# ===========================================================================
# TIPOS DE DATOS Y ENUMS
# ===========================================================================

@dataclass
class ConfigEnsayo:
    factor_escala: float
    rango_esperado_n: float
    umbral_ignicion_pct: float
    umbral_apagado_pct: float
    tiempo_minimo_s: float
    buffer_pre_s: float

class Evento(Enum):
    NINGUNO = "ninguno"
    IGNICION = "ignicion"
    FIN_EMPUJE = "fin_empuje"

@dataclass
class MuestraProcesada:
    empuje_n: float
    t_ensayo_s: float
    evento: Evento

@dataclass
class ResultadoEnsayo:
    puntos: List[Tuple[float, float]]
    impulso_ns: float
    empuje_max_n: float
    empuje_avg_n: float
    duracion_s: float
    clase_nfpa: str

@dataclass
class ResultadoTara:
    media_adc: float
    std_adc: float
    n_muestras: int

@dataclass
class ResultadoCalibracion:
    factor_nuevo: float
    delta_adc: float
    std_adc: float
    razon_vs_anterior: float
    advertencia: Optional[str]

# ===========================================================================
# FUNCIONES PURAS
# ===========================================================================

def calcular_tara(muestras: List[float]) -> ResultadoTara:
    """Calcula la media y desviación estándar de lecturas en reposo para el cero."""
    if len(muestras) < 5:
        raise ValueError("Mínimo 5 muestras para tara")
    media, std = promedio_robusto(muestras)
    return ResultadoTara(media, std, len(muestras))

def calcular_factor_calibracion(muestras_carga: List[float], tara_adc: float, masa_patron_kg: float, factor_actual: float) -> ResultadoCalibracion:
    """Genera un nuevo factor de calibración comparando una lectura con carga contra la tara."""
    if len(muestras_carga) < 5:
        raise ValueError("Mínimo 5 muestras para calibración")
    
    media_c, std_c = promedio_robusto(muestras_carga)
    delta = abs(media_c - tara_adc)
    factor_n = delta / masa_patron_kg if delta > 0 else 0.0
    razon = factor_n / factor_actual if factor_actual > 0 else 0.0

    advertencia = None
    if delta < 500:
        advertencia = f"delta_adc={delta:.1f} menor al mínimo esperado (500). Verifica el peso."
    elif not (0.05 < razon < 20.0):
        advertencia = f"Razón factor_nuevo/factor_actual={razon:.3f} fuera de rango [0.05, 20.0]."

    return ResultadoCalibracion(factor_n, delta, std_c, razon, advertencia)

def clase_nfpa(impulso_ns: float) -> str:
    """Retorna la clasificación NFPA del motor cohete basándose en su impulso total."""
    if impulso_ns <= 0:
        return "—"
    for lo, hi, letra in _CLASES_NFPA:
        if lo < impulso_ns <= hi:
            return letra
    return "O+"

# ===========================================================================
# MOTOR DE ENSAYO
# ===========================================================================

class MotorEnsayo:
    """Procesa flujos de datos ADC y detecta el ciclo de vida del ensayo de forma aislada."""
    
    def __init__(self, config: ConfigEnsayo, valor_tara: float):
        """Inicializa el procesador lógico de ensayo con la configuración y cero establecidos."""
        self.config = config
        self.valor_tara = valor_tara
        
        self._fase: str = "ARMADO"
        self._puntos: List[Tuple[float, float]] = []
        self._buffer_pre: deque[Tuple[float, float]] = deque()
        self._t_ignicion: float = 0.0
        self._t_pausa_inicio: float = 0.0
        self._t_pausa_acum: float = 0.0

    @property
    def puntos(self) -> List[Tuple[float, float]]:
        """Retorna una copia de los puntos de ensayo almacenados."""
        return list(self._puntos)

    @property
    def t_relativo(self) -> float:
        """Devuelve el último tiempo de ensayo registrado, útil para streams."""
        return self._puntos[-1][0] if self._puntos else 0.0

    @property
    def fase(self) -> str:
        """Indica la fase interna actual en la que se encuentra la evaluación de datos."""
        return self._fase

    def procesar(self, valor_adc: float, t_wall: float) -> MuestraProcesada:
        """Ingesta un valor crudo, lo filtra y evalúa umbrales para emitir un evento."""
        diferencia = self.valor_tara - valor_adc
        empuje_n = (diferencia / self.config.factor_escala) * GRAVEDAD
        zona_muerta = self.config.rango_esperado_n * 0.005
        
        if abs(empuje_n) < zona_muerta:
            empuje_n = 0.0

        if self._fase == "ARMADO":
            self._buffer_pre.append((t_wall, empuje_n))
            while self._buffer_pre and self._buffer_pre[0][0] < (t_wall - self.config.buffer_pre_s):
                self._buffer_pre.popleft()

            umbral_ign = self.config.rango_esperado_n * (self.config.umbral_ignicion_pct / 100.0)
            if empuje_n >= umbral_ign:
                self._t_ignicion = self._buffer_pre[0][0]
                self._t_pausa_acum = 0.0
                
                # Volcar buffer previo a puntos finales
                for t_abs, n_val in self._buffer_pre:
                    self._puntos.append((t_abs - self._t_ignicion, n_val))
                self._buffer_pre.clear()
                
                self._fase = "QUEMANDO"
                return MuestraProcesada(empuje_n, t_ensayo_s=0.0, evento=Evento.IGNICION)
                
            return MuestraProcesada(empuje_n, t_ensayo_s=-1.0, evento=Evento.NINGUNO)

        elif self._fase == "QUEMANDO":
            t_ensayo = t_wall - self._t_ignicion - self._t_pausa_acum
            self._puntos.append((t_ensayo, empuje_n))
            
            umbral_apg = self.config.rango_esperado_n * (self.config.umbral_apagado_pct / 100.0)
            if empuje_n <= umbral_apg and t_ensayo > self.config.tiempo_minimo_s:
                self._fase = "FINALIZADO"
                return MuestraProcesada(empuje_n, t_ensayo, evento=Evento.FIN_EMPUJE)
                
            return MuestraProcesada(empuje_n, t_ensayo, evento=Evento.NINGUNO)

        else:
            return MuestraProcesada(empuje_n, t_ensayo_s=-1.0, evento=Evento.NINGUNO)

    def pausar(self, t_wall: float) -> None:
        """Suspende la recolección de puntos y almacena la marca de tiempo de inicio de pausa."""
        if self._fase != "QUEMANDO":
            raise RuntimeError(f"pausar() inválido en fase {self._fase}")
        self._t_pausa_inicio = t_wall
        self._fase = "PAUSADO"

    def reanudar(self, t_wall: float) -> None:
        """Descuenta el tiempo transcurrido durante la pausa y reanuda el ensayo."""
        if self._fase != "PAUSADO":
            raise RuntimeError(f"reanudar() inválido en fase {self._fase}")
        self._t_pausa_acum += t_wall - self._t_pausa_inicio
        self._fase = "QUEMANDO"

    def cerrar(self) -> ResultadoEnsayo:
        """Sintetiza los resultados finales integrando los vectores generados."""
        if len(self._puntos) < 2:
            raise ValueError("Ensayo sin datos suficientes")

        ts = np.array([p[0] for p in self._puntos])
        ns = np.array([p[1] for p in self._puntos])

        impulso_ns = float(np.trapezoid(ns, ts))
        empuje_max_n = float(np.max(ns))
        
        positivos = [n for n in ns if n > 0]
        empuje_avg_n = float(np.mean(positivos)) if positivos else 0.0
        
        duracion_s = float(ts[-1] - ts[0])
        clasificacion = clase_nfpa(impulso_ns)

        return ResultadoEnsayo(
            puntos=self.puntos,
            impulso_ns=impulso_ns,
            empuje_max_n=empuje_max_n,
            empuje_avg_n=empuje_avg_n,
            duracion_s=duracion_s,
            clase_nfpa=clasificacion
        )

# ===========================================================================
# VERIFICACIÓN INTERNA
# ===========================================================================

if __name__ == "__main__":
    import random

    print("=== VERIFICACIÓN MOTOR DE ENSAYO ===\n")
    
    cfg = ConfigEnsayo(
        factor_escala=109324.0, 
        rango_esperado_n=10.0,
        umbral_ignicion_pct=5.0, 
        umbral_apagado_pct=2.0,
        tiempo_minimo_s=0.3, 
        buffer_pre_s=1.0
    )
    
    # Simulación de Tara
    tara = calcular_tara([-100.1, -99.8, -100.3, -100.0, -99.9, -100.2])
    print(f"[i] Tara calculada: {tara.media_adc:.2f} (σ={tara.std_adc:.2f})\n")

    # Inicializar motor
    motor = MotorEnsayo(cfg, tara.media_adc)
    t_wall = 0.0
    
    # Loop de simulación (200 muestras)
    print("[i] Simulando 200 muestras (80 SPS)...\n")
    for i in range(200):
        # Muestras 0-19: Reposo (ruido σ=30)
        if i < 20:
            val_adc = tara.media_adc + random.gauss(0, 30)
        # Muestras 20-179: Quemado (~0.5 N, delta = -5500, ruido σ=50)
        elif i < 180:
            val_adc = tara.media_adc - 5500 + random.gauss(0, 50)
        # Muestras 180-199: Reposo (ruido σ=30)
        else:
            val_adc = tara.media_adc + random.gauss(0, 30)

        muestra = motor.procesar(val_adc, t_wall)

        if muestra.evento == Evento.IGNICION:
            print(f"🔥 Muestra {i}: ¡IGNICIÓN DETECTADA! (t_wall={t_wall:.3f}s | empuje={muestra.empuje_n:.3f}N)")
        elif muestra.evento == Evento.FIN_EMPUJE:
            print(f"🛑 Muestra {i}: ¡FIN DE EMPUJE DETECTADO! (t_wall={t_wall:.3f}s | empuje={muestra.empuje_n:.3f}N)")
            
        t_wall += 0.0125  # Avance por ciclo a 80 SPS

    # Cierre de ensayo y métricas
    res = motor.cerrar()
    print("\n=== RESULTADO DEL ENSAYO ===")
    print(f" Impulso Total : {res.impulso_ns:.4f} N·s")
    print(f" Empuje Máximo : {res.empuje_max_n:.4f} N")
    print(f" Empuje Medio  : {res.empuje_avg_n:.4f} N")
    print(f" Duración      : {res.duracion_s:.4f} s")
    print(f" Clase NFPA    : {res.clase_nfpa}")
    print(f" Datos Capt.   : {len(res.puntos)} muestras")