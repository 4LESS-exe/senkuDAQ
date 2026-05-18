"""
api/server.py — Servidor REST FastAPI para SENKU DAQ.
Implementa el control de estado, telemetría SSE y seguridad por tokens.
"""

import asyncio
import secrets
import time
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Importaciones del Core
from core.config import cargar_config, guardar_config, CONFIG_DEFAULT
from core.state import MaquinaEstado, TransicionInvalida, DESCONECTADO, CONECTADO, ESPERANDO, ARMADO, QUEMANDO, PAUSADO, FINALIZADO, RECONECTANDO
from core.engine import MotorEnsayo, ConfigEnsayo, Evento, calcular_tara, calcular_factor_calibracion
from core.wireless_reader import LectorWireless
from core.data_export import guardar_ensayo

app = FastAPI(title="SENKU DAQ API", version="2.0", root_path="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===========================================================================
# ESTADO GLOBAL DEL SERVIDOR
# ===========================================================================

class AppState:
    def __init__(self):
        self.maquina = MaquinaEstado()
        self.lector: Optional[LectorWireless] = None
        self.motor: Optional[MotorEnsayo] = None
        
        self.cfg = cargar_config()
        self.session_token: Optional[str] = None
        self.tara_actual: float = 0.0
        self.t_ultimo_cambio_estado: float = time.time()
        
        # Telemetría en tiempo real
        self.empuje_actual_n: float = 0.0
        self.sse_queues: List[asyncio.Queue] = []

state = AppState()

# Observer para detectar cambios de estado y publicarlos en SSE
def _on_cambio_estado(anterior: str, nuevo: str):
    state.t_ultimo_cambio_estado = time.time()
    broadcast_sse("estado", {"anterior": anterior, "nuevo": nuevo})

state.maquina.agregar_observer(_on_cambio_estado)

# ===========================================================================
# DEPENDENCIAS Y HELPERS DE SEGURIDAD
# ===========================================================================

def verificar_token(x_session_token: Optional[str] = Header(None)):
    if not state.session_token:
        raise HTTPException(status_code=401, detail={"error": "TOKEN_REQUERIDO", "detalle": "No hay sesión activa."})
    if x_session_token != state.session_token:
        raise HTTPException(status_code=403, detail={"error": "TOKEN_INVALIDO", "detalle": "Token incorrecto."})
    return x_session_token

def requerir_estado(estados_permitidos: List[str]):
    if state.maquina.estado not in estados_permitidos:
        raise HTTPException(status_code=409, detail={
            "error": "ESTADO_INVALIDO",
            "detalle": f"Operación no permitida en estado {state.maquina.estado}",
            "estado_actual": state.maquina.estado
        })

def transicionar_seguro(nuevo_estado: str):
    try:
        state.maquina.transicionar(nuevo_estado)
    except TransicionInvalida as e:
        raise HTTPException(status_code=409, detail={"error": "ESTADO_INVALIDO", "detalle": str(e), "estado_actual": state.maquina.estado})

# ===========================================================================
# MODELOS PYDANTIC (Requests)
# ===========================================================================

class ConexionReq(BaseModel):
    host: str = "127.0.0.1"
    puerto_tcp: int = 8080

class CargaReq(BaseModel):
    masa_patron_kg: float
    tara_adc: float

class ConfirmarReq(BaseModel):
    factor_nuevo: float

class ArmarReq(BaseModel):
    rango_esperado_n: float
    umbral_ignicion_pct: float
    umbral_apagado_pct: float
    tiempo_minimo_s: float
    buffer_pre_s: float

class GuardarReq(BaseModel):
    motor_nombre: str
    diametro_mm: float  
    longitud_mm: float
    peso_prop_kg: float
    peso_total_kg: float

# ===========================================================================
# BACKGROUND TASK: Procesamiento DAQ y SSE
# ===========================================================================

def broadcast_sse(event_name: str, data: dict):
    """Envía un mensaje a todas las colas SSE conectadas."""
    msg = f"event: {event_name}\ndata: {data}\n\n".replace("'", '"')
    for q in state.sse_queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

async def daq_loop():
    """Loop asíncrono que extrae datos del lector, procesa el motor y emite SSE."""
    while True:
        await asyncio.sleep(0.04)  # ~25 fps (40ms)
        if not state.lector or getattr(state.lector, "bloqueo_gui", False):
            continue

        nuevos = []
        while not state.lector.cola.empty():
            nuevos.append(state.lector.cola.get_nowait())

        for val_adc in nuevos:
            # Calcular empuje en crudo para telemetría libre
            factor = state.cfg.get("factor_escala", CONFIG_DEFAULT["factor_escala"])
            state.empuje_actual_n = ((state.tara_actual - val_adc) / factor) * 9.80665

            if state.motor and state.maquina.estado in [ARMADO, QUEMANDO, PAUSADO, RECONECTANDO]:
                muestra = state.motor.procesar(val_adc, time.time())
                
                # Manejo de eventos de ignición/fin empuje
                if muestra.evento == Evento.IGNICION:
                    transicionar_seguro(QUEMANDO)
                elif muestra.evento == Evento.FIN_EMPUJE:
                    transicionar_seguro(FINALIZADO)

        # Emisión de telemetría constante (a 25Hz)
        if nuevos:
            t_rel = state.motor.t_relativo if state.motor else 0.0
            broadcast_sse("muestra", {
                "t_s": round(t_rel, 4),
                "empuje_n": round(state.empuje_actual_n, 4),
                "señal_pct": state.lector.señal_conexion
            })

# Iniciamos el background loop al arrancar FastAPI
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(daq_loop())

# ===========================================================================
# ENDPOINTS
# ===========================================================================

@app.get("/estado")
async def get_estado():
    res = {
        "estado": state.maquina.estado,
        "señal_pct": state.lector.señal_conexion if state.lector else 0,
        "valor_tara": state.tara_actual,
        "t_relativo_s": state.motor.t_relativo if state.motor else 0.0,
        "empuje_actual_n": state.empuje_actual_n
    }
    if state.session_token:
        res["session_token"] = state.session_token
    return res

@app.get("/stream")
async def stream(request: Request):
    """Endpoint Server-Sent Events (SSE) para tiempo real."""
    q = asyncio.Queue(maxsize=100)
    state.sse_queues.append(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield await q.get()
        finally:
            state.sse_queues.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/conexion")
async def conectar(req: ConexionReq):
    requerir_estado([DESCONECTADO])
    if state.session_token:
        raise HTTPException(409, {"error": "Ya existe una conexión activa"})

    state.lector = LectorWireless(req.host, req.puerto_tcp)
    state.lector.start()
    
    # Pequeña espera para estabilizar
    await asyncio.sleep(2.0)
    if state.lector.error:
        err = state.lector.error
        state.lector = None
        raise HTTPException(500, {"error": "ERROR_CONEXION", "detalle": err})

    state.session_token = secrets.token_hex(16)
    transicionar_seguro(CONECTADO)
    
    return {"session_token": state.session_token, "estado": state.maquina.estado}

@app.delete("/conexion")
async def desconectar(token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    if state.maquina.es_activo() or state.maquina.estado == ARMADO:
        raise HTTPException(409, {"error": "No se puede desconectar durante un ensayo activo o armado"})

    if state.lector:
        state.lector.detener()
        state.lector = None
    
    state.session_token = None
    state.motor = None
    transicionar_seguro(DESCONECTADO)
    return {"estado": state.maquina.estado}

@app.post("/tara")
async def realizar_tara(token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([CONECTADO, ESPERANDO])
    
    try:
        # Ejecutamos en un thread para no bloquear el loop principal asíncrono
        muestras = await asyncio.to_thread(state.lector.leer_bloqueante, 30, 5.0)
        res_tara = calcular_tara(muestras)
        
        state.tara_actual = res_tara.media_adc
        if state.maquina.estado != ESPERANDO:
            transicionar_seguro(ESPERANDO)
            
        return {
            "media_adc": round(res_tara.media_adc, 2),
            "std_adc": round(res_tara.std_adc, 2),
            "n_muestras": res_tara.n_muestras,
            "estado": state.maquina.estado
        }
    except Exception as e:
        raise HTTPException(500, {"error": "TIMEOUT_MUESTRAS", "detalle": str(e)})

@app.post("/calibracion/tara")
async def calib_tara(token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([CONECTADO, ESPERANDO])
    try:
        muestras = await asyncio.to_thread(state.lector.leer_bloqueante, 150, 10.0)
        res = calcular_tara(muestras)
        return {"media_adc": res.media_adc, "std_adc": res.std_adc}
    except Exception as e:
        raise HTTPException(500, {"error": "TIMEOUT_MUESTRAS", "detalle": str(e)})

@app.post("/calibracion/carga")
async def calib_carga(req: CargaReq, token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([CONECTADO, ESPERANDO])
    try:
        muestras = await asyncio.to_thread(state.lector.leer_bloqueante, 150, 10.0)
        factor_actual = state.cfg.get("factor_escala", CONFIG_DEFAULT["factor_escala"])
        res = calcular_factor_calibracion(muestras, req.tara_adc, req.masa_patron_kg, factor_actual)
        
        if res.advertencia:
            raise HTTPException(422, {"error": "RESULTADO_SOSPECHOSO", "detalle": res.advertencia})
            
        return {
            "factor_nuevo": res.factor_nuevo,
            "factor_anterior": factor_actual,
            "delta_adc": res.delta_adc,
            "std_adc": res.std_adc,
            "razon_vs_anterior": res.razon_vs_anterior,
            "advertencia": None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, {"error": "TIMEOUT_MUESTRAS", "detalle": str(e)})

@app.post("/calibracion/confirmar")
async def calib_confirmar(req: ConfirmarReq, token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([CONECTADO, ESPERANDO])
    state.cfg["factor_escala"] = req.factor_nuevo
    guardar_config(state.cfg)
    return {"factor_escala": req.factor_nuevo}

@app.post("/ensayo/armar")
async def armar_ensayo(req: ArmarReq, token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([ESPERANDO])
    
    if state.tara_actual == 0.0:
        raise HTTPException(400, {"error": "SIN_TARA", "detalle": "Debe realizar tara antes de armar."})

    cfg_ensayo = ConfigEnsayo(
        factor_escala=state.cfg.get("factor_escala", CONFIG_DEFAULT["factor_escala"]),
        rango_esperado_n=req.rango_esperado_n,
        umbral_ignicion_pct=req.umbral_ignicion_pct,
        umbral_apagado_pct=req.umbral_apagado_pct,
        tiempo_minimo_s=req.tiempo_minimo_s,
        buffer_pre_s=req.buffer_pre_s
    )
    
    state.motor = MotorEnsayo(cfg_ensayo, state.tara_actual)
    transicionar_seguro(ARMADO)
    
    umbral_ign_n = req.rango_esperado_n * (req.umbral_ignicion_pct / 100)
    umbral_apg_n = req.rango_esperado_n * (req.umbral_apagado_pct / 100)
    
    return {"estado": state.maquina.estado, "umbral_ign_n": umbral_ign_n, "umbral_apg_n": umbral_apg_n}

@app.delete("/ensayo/armar")
async def desarmar_ensayo(token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([ARMADO])
    state.motor = None
    transicionar_seguro(ESPERANDO)
    return {"estado": state.maquina.estado}

@app.post("/ensayo/pausa")
async def toggle_pausa(token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([QUEMANDO, PAUSADO])
    
    if state.maquina.estado == QUEMANDO:
        state.motor.pausar(time.time())
        transicionar_seguro(PAUSADO)
    else:
        state.motor.reanudar(time.time())
        transicionar_seguro(QUEMANDO)
        
    return {
        "estado": state.maquina.estado,
        "t_pausa_acum_s": round(state.motor._t_pausa_acum, 2)
    }

@app.post("/ensayo/descartar")
async def descartar_datos_ensayo(x_session_token: str = Header(None, alias="X-Session-Token")):
    verificar_token(x_session_token) 
    
    if state.maquina.estado != FINALIZADO:
        raise HTTPException(
            status_code=400, 
            detail=f"No se pueden descartar datos en estado {state.maquina.estado}."
        )
    
    if state.motor:
        state.motor.puntos.clear() 
        
    transicionar_seguro(ESPERANDO)
    return {"status": "ok", "detalle": "Ensayo descartado correctamente."}

@app.post("/ensayo/guardar")
async def guardar_resultados(req: GuardarReq, token: str = Header(..., alias="X-Session-Token")):
    verificar_token(token)
    requerir_estado([FINALIZADO])
    
    try:
        res_ensayo = state.motor.cerrar()
        metricas = guardar_ensayo(
            datos=res_ensayo.puntos,
            nombre_motor=req.motor_nombre,
            diametro=req.diametro_mm,
            longitud=req.longitud_mm,
            peso_prop=req.peso_prop_kg,
            peso_total=req.peso_total_kg,
            plot_item=None # PlotItem no existe en entorno backend
        )
        transicionar_seguro(ESPERANDO)
        
        return {
            "impulso_ns": res_ensayo.impulso_ns,
            "empuje_max_n": res_ensayo.empuje_max_n,
            "empuje_avg_n": res_ensayo.empuje_avg_n,
            "duracion_s": res_ensayo.duracion_s,
            "clase_nfpa": res_ensayo.clase_nfpa,
            "ruta_dir": metricas["ruta_dir"],
            "ruta_csv": metricas["ruta_csv"],
            "ruta_eng": metricas["ruta_eng"]
        }
    except Exception as e:
        raise HTTPException(500, {"error": "ERROR_GUARDADO", "detalle": str(e)})


@app.get("/ensayo/datos")
async def get_datos_ensayo():
    if not state.motor or not state.motor.puntos:
        raise HTTPException(404, {"error": "No hay ensayo activo ni datos del último ensayo"})
    
    return {
        "estado": state.maquina.estado,
        "puntos": state.motor.puntos,
        "t_relativo_s": state.motor.t_relativo
    }