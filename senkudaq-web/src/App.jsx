import { useState, useEffect, useRef, useMemo } from 'react';
import UplotChart from './UplotChart';
 
const API_URL = "http://127.0.0.1:8765/api/v1";
 
const DEFAULT_CONFIG = {
  host_wifi: "127.0.0.1", puerto_tcp: 8080,
  motor_nombre: "Senku_1", motor_diametro: 20, motor_longitud: 100,
  motor_peso_prop: 0.100, motor_peso_total: 0.150,
  rango_esperado_n: 10.0, umbral_ignicion_pct: 5.0, umbral_apagado_pct: 2.0,
  tiempo_minimo_s: 0.3, factor_escala: 109324.0
};
 
const InputField = ({ label, configKey, type = "text", cfg, updateCfg }) => (
  <div className="flex justify-between items-center bg-gray-100 p-1 rounded">
    <span className="text-xs text-gray-600 font-bold pl-2 w-32">{label}</span>
    <input
      type={type}
      value={cfg[configKey] || ""}
      onChange={(e) => updateCfg(configKey, e.target.value)}
      className="bg-white border border-gray-300 text-xs p-1 rounded w-24 text-center focus:outline-none focus:border-red-600"
    />
  </div>
);
 
const safeLocalStorage = {
  get: (key) => {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set: (key, value) => {
    try { localStorage.setItem(key, value); } catch { /* sin soporte */ }
  },
};
 
export default function App() {
  const [estado, setEstado] = useState("DESCONECTADO");
  const [token, setToken] = useState(null);
  const [empujeActual, setEmpujeActual] = useState(0.0);
 
  // Modal de confirmación al finalizar ensayo
  const [modalFin, setModalFin] = useState({ open: false, impulso: null, clase: null });
 
  const tokenRef = useRef(null);
  useEffect(() => { tokenRef.current = token; }, [token]);
 
  const estadoRef = useRef("DESCONECTADO");
  useEffect(() => { estadoRef.current = estado; }, [estado]);
 
  const [cfg, setCfg] = useState(() => {
    const saved = safeLocalStorage.get('senkudaq_config');
    return saved ? JSON.parse(saved) : DEFAULT_CONFIG;
  });
 
  const updateCfg = (key, value) => {
    setCfg(prev => {
      const newCfg = { ...prev, [key]: value };
      safeLocalStorage.set('senkudaq_config', JSON.stringify(newCfg));
      return newCfg;
    });
  };
 
  // Umbrales calculados localmente en todo momento
  const umbralIgnN = useMemo(() => {
    const rango = parseFloat(cfg.rango_esperado_n);
    const pct   = parseFloat(cfg.umbral_ignicion_pct);
    return isNaN(rango) || isNaN(pct) ? null : rango * (pct / 100);
  }, [cfg.rango_esperado_n, cfg.umbral_ignicion_pct]);
 
  const umbralApgN = useMemo(() => {
    const rango = parseFloat(cfg.rango_esperado_n);
    const pct   = parseFloat(cfg.umbral_apagado_pct);
    return isNaN(rango) || isNaN(pct) ? null : rango * (pct / 100);
  }, [cfg.rango_esperado_n, cfg.umbral_apagado_pct]);
 
  const [chartData, setChartData] = useState([[], []]);
  const xData = useRef([]);
  const yData = useRef([]);
  const tickCounter = useRef(0);
 
  const [calib, setCalib] = useState({ open: false, step: 0, taraAdc: 0, masaKg: 1.0, factorNuevo: 0, msg: "" });
 
  // RECUPERACIÓN DE SESIÓN AL INICIAR
  useEffect(() => {
    const sincronizarEstado = async () => {
      try {
        const res = await fetch(`${API_URL}/estado`);
        if (res.ok) {
          const data = await res.json();
          setEstado(data.estado);
          if (data.session_token) {
            setToken(data.session_token);
            console.log("Sesión previa recuperada automáticamente.");
          }
        }
      } catch (err) {
        console.warn("Backend no disponible al inicio.", err);
      }
    };
    sincronizarEstado();
  }, []);
 
  const req = async (method, endpoint, body = null) => {
    try {
      const headers = { "Content-Type": "application/json" };
      const currentToken = tokenRef.current;
      if (currentToken) headers["X-Session-Token"] = currentToken;
 
      const res = await fetch(`${API_URL}${endpoint}`, {
        method, headers, body: body ? JSON.stringify(body) : null
      });
 
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detalle || err.error || "Error desconocido");
      }
      return await res.json();
    } catch (error) {
      alert(`Error API (${endpoint}): ` + error.message);
      return null;
    }
  };
 
  const descargarDatosEnsayo = async () => {
    const res = await req("GET", "/ensayo/datos");
    if (res && res.puntos) {
      const ts = res.puntos.map(p => p[0]);
      const ns = res.puntos.map(p => p[1]);
      setChartData([ts, ns]);
 
      // Calcular métricas para mostrar en el modal
      const empujeMax = Math.max(...ns).toFixed(3);
      const impulso   = ns.reduce((acc, n, i) => {
        if (i === 0) return 0;
        return acc + n * (ts[i] - ts[i - 1]);
      }, 0).toFixed(2);
 
      setModalFin({ open: true, impulso, empujeMax });
    }
  };
  const descargarDatosEnsayoRef = useRef(descargarDatosEnsayo);
  useEffect(() => { descargarDatosEnsayoRef.current = descargarDatosEnsayo; });
 
  // CONEXIÓN AL STREAM SSE
  useEffect(() => {
    const sse = new EventSource(`${API_URL}/stream`);
 
    sse.addEventListener("estado", (e) => {
      const data = JSON.parse(e.data);
      setEstado(data.nuevo);
      if (data.nuevo === "FINALIZADO") {
        descargarDatosEnsayoRef.current();
      }
    });
 
    sse.addEventListener("muestra", (e) => {
      const data = JSON.parse(e.data);
      setEmpujeActual(data.empuje_n);
 
      if (estadoRef.current !== "FINALIZADO") {
        tickCounter.current = Number((tickCounter.current + 0.04).toFixed(3));
        xData.current.push(tickCounter.current);
        yData.current.push(data.empuje_n);
 
        if (xData.current.length > 500) {
          xData.current.shift();
          yData.current.shift();
        }
 
        setChartData([[...xData.current], [...yData.current]]);
      }
    });
 
    return () => sse.close();
  }, []);
 
  const handleConectar = async () => {
    if (estado !== "DESCONECTADO") {
      await req("DELETE", "/conexion");
      tokenRef.current = null;
      setToken(null);
      setEstado("DESCONECTADO");
      xData.current = [];
      yData.current = [];
      tickCounter.current = 0;
      setChartData([[], []]);
      return;
    }
    const res = await req("POST", "/conexion", { host: cfg.host_wifi, puerto_tcp: Number(cfg.puerto_tcp) });
    if (res) {
      setToken(res.session_token);
      setTimeout(() => req("POST", "/tara"), 500);
    }
  };
 
  const handleTaraManual = async () => {
    if (!tokenRef.current) return;
    const res = await req("POST", "/tara");
    if (res) alert(`Cero establecido en ${res.media_adc} ADC.`);
  };
 
  const handleArmar = async () => {
    if (estado === "ARMADO") {
      await req("DELETE", "/ensayo/armar");
      return;
    }
    const res = await req("POST", "/ensayo/armar", {
      rango_esperado_n:    Number(cfg.rango_esperado_n),
      umbral_ignicion_pct: Number(cfg.umbral_ignicion_pct),
      umbral_apagado_pct:  Number(cfg.umbral_apagado_pct),
      tiempo_minimo_s:     Number(cfg.tiempo_minimo_s),
      buffer_pre_s: 1.0
    });
    if (res) {
      xData.current = [];
      yData.current = [];
      tickCounter.current = 0;
      setChartData([[], []]);
    }
  };
 
  const handlePausa = async () => req("POST", "/ensayo/pausa");
 
  // Guardar: llamado desde el modal de confirmación
  const handleGuardar = async () => {
    const res = await req("POST", "/ensayo/guardar", {
      motor_nombre: cfg.motor_nombre,
      diametro_mm:  Number(cfg.motor_diametro),
      longitud_mm:  Number(cfg.motor_longitud),
      peso_prop_kg: Number(cfg.motor_peso_prop),
      peso_total_kg: Number(cfg.motor_peso_total),
    });
    setModalFin({ open: false, impulso: null, empujeMax: null });
    if (res) alert(`Ensayo guardado.\nImpulso: ${res.impulso_ns.toFixed(2)} N·s\nClase NFPA: ${res.clase_nfpa}\nRuta: ${res.ruta_dir}`);
  };
 
  // Rechazar: descarta el ensayo y vuelve a ESPERANDO sin guardar nada
  const handleRechazar = async () => {
    if (!window.confirm("¿Seguro que deseas descartar las curvas de este ensayo? Los datos se perderán.")) return;
    
    // Cambiamos el método a "POST" y la ruta a "/ensayo/descartar"
    const res = await req("POST", "/ensayo/descartar");
    
    if (res) {
      setModalFin({ open: false, impulso: null, empujeMax: null });
      xData.current = [];
      yData.current = [];
      tickCounter.current = 0;
      setChartData([[], []]);
      console.log("Ensayo descartado con éxito vía POST.");
    }
  };
 
  const iniciarCalibracion = () => setCalib({
    open: true, step: 0, taraAdc: 0, masaKg: 1.0, factorNuevo: 0,
    msg: "Asegúrate de que la celda esté VACÍA."
  });
 
  const pasoCalibracion = async () => {
    if (calib.step === 0) {
      setCalib(prev => ({ ...prev, msg: "Midiendo tara..." }));
      const res = await req("POST", "/calibracion/tara");
      if (res) setCalib(prev => ({
        ...prev, step: 1, taraAdc: res.media_adc,
        msg: `Tara OK: ${res.media_adc.toFixed(1)}. Ingresa el peso patrón y colócalo.`
      }));
    } else if (calib.step === 1) {
      setCalib(prev => ({ ...prev, msg: `Midiendo con ${calib.masaKg}kg...` }));
      const res = await req("POST", "/calibracion/carga", {
        masa_patron_kg: Number(calib.masaKg), tara_adc: calib.taraAdc
      });
      if (res) {
        const warn = res.advertencia ? `\n⚠️ ${res.advertencia}` : "";
        setCalib(prev => ({
          ...prev, step: 2, factorNuevo: res.factor_nuevo,
          msg: `Factor anterior: ${cfg.factor_escala}\nNuevo factor: ${res.factor_nuevo.toFixed(1)}${warn}`
        }));
      }
    } else if (calib.step === 2) {
      const res = await req("POST", "/calibracion/confirmar", { factor_nuevo: calib.factorNuevo });
      if (res) {
        updateCfg("factor_escala", calib.factorNuevo);
        setCalib({ open: false, step: 0, msg: "" });
        alert("Factor guardado. Recuerda establecer la TARA nuevamente.");
      }
    }
  };
 
  const empujeColor = umbralIgnN !== null && empujeActual >= umbralIgnN
    ? '#1e8449'
    : '#c0392b';
 
  return (
    <div className="min-h-screen p-2 md:p-4 flex flex-col gap-4 max-w-6xl mx-auto font-mono text-gray-800">
 
      <header className="bg-[#e0e0e0] p-3 rounded shadow-sm border border-[#b0b0b0] flex justify-between items-center">
        <div>
          <h1 className="text-xl font-bold text-[#c0392b]">SENKU DAQ</h1>
          <p className="text-xs text-gray-600">v2.1 · PWA Client · USACH</p>
        </div>
        <div className="text-right flex items-center gap-4">
          <div
            className="text-2xl font-bold bg-white px-3 py-1 border border-gray-300 rounded shadow-inner transition-colors duration-100"
            style={{ color: empujeColor }}
          >
            {empujeActual.toFixed(3)} N
          </div>
        </div>
      </header>
 
      <div className="flex flex-col md:flex-row gap-4 items-start">
        <aside className="bg-[#e0e0e0] w-full md:w-80 p-3 rounded shadow-sm border border-[#b0b0b0] flex flex-col gap-2 shrink-0">
 
          <div className="text-center py-2 bg-white rounded border border-gray-300 mb-2">
            <span className="font-bold text-lg" style={{
              color: estado === "CONECTADO"  ? '#1a5276'
                   : estado === "ESPERANDO" ? '#1e8449'
                   : estado === "QUEMANDO"  ? '#c0392b'
                   : estado === "FINALIZADO"? '#7d3c98'
                   : '#555'
            }}>
              {estado} {estado === "QUEMANDO" ? "🔥" : estado === "FINALIZADO" ? "✓" : ""}
            </span>
          </div>
 
          <button onClick={handleConectar} className="w-full bg-[#1a5276] hover:bg-blue-800 text-white font-bold py-2 rounded text-sm transition-colors mb-2">
            {estado !== "DESCONECTADO" ? "DESCONECTAR" : "CONECTAR"}
          </button>
 
          <div className="text-[10px] font-bold text-gray-500 mb-1 border-b border-gray-300 uppercase">Datos del Motor</div>
          <InputField label="Nombre"         configKey="motor_nombre"    cfg={cfg} updateCfg={updateCfg} />
          <InputField label="Diámetro (mm)"  configKey="motor_diametro"  cfg={cfg} updateCfg={updateCfg} />
          <InputField label="Longitud (mm)"  configKey="motor_longitud"  cfg={cfg} updateCfg={updateCfg} />
          <InputField label="Peso prop (kg)" configKey="motor_peso_prop" cfg={cfg} updateCfg={updateCfg} />
 
          <div className="text-[10px] font-bold text-gray-500 mb-1 mt-2 border-b border-gray-300 uppercase">Parámetros de Ensayo</div>
          <InputField label="Rango máx (N)"  configKey="rango_esperado_n" cfg={cfg} updateCfg={updateCfg} />
 
          {/* Umbral ignición con valor N al costado */}
          <div className="flex justify-between items-center bg-gray-100 p-1 rounded">
            <span className="text-xs text-gray-600 font-bold pl-2 w-32">Umbral ign (%)</span>
            <div className="flex items-center gap-1">
              {umbralIgnN !== null && (
                <span className="text-[10px] font-bold font-mono" style={{ color: "#e67e22" }}>
                  {umbralIgnN.toFixed(2)}N
                </span>
              )}
              <input
                type="text"
                value={cfg.umbral_ignicion_pct || ""}
                onChange={(e) => updateCfg("umbral_ignicion_pct", e.target.value)}
                className="bg-white border border-gray-300 text-xs p-1 rounded w-24 text-center focus:outline-none focus:border-red-600"
              />
            </div>
          </div>
 
          {/* Umbral apagado con valor N al costado */}
          <div className="flex justify-between items-center bg-gray-100 p-1 rounded">
            <span className="text-xs text-gray-600 font-bold pl-2 w-32">Umbral apg (%)</span>
            <div className="flex items-center gap-1">
              {umbralApgN !== null && (
                <span className="text-[10px] font-bold font-mono" style={{ color: "#7f8c8d" }}>
                  {umbralApgN.toFixed(2)}N
                </span>
              )}
              <input
                type="text"
                value={cfg.umbral_apagado_pct || ""}
                onChange={(e) => updateCfg("umbral_apagado_pct", e.target.value)}
                className="bg-white border border-gray-300 text-xs p-1 rounded w-24 text-center focus:outline-none focus:border-red-600"
              />
            </div>
          </div>
 
          <InputField label="Factor escala"  configKey="factor_escala" cfg={cfg} updateCfg={updateCfg} />
 
          <div className="text-[10px] font-bold text-gray-500 mb-1 mt-2 border-b border-gray-300 uppercase">Control</div>
          <button onClick={handleTaraManual} disabled={!["CONECTADO", "ESPERANDO"].includes(estado)} className="w-full bg-gray-500 hover:bg-gray-600 disabled:opacity-50 text-white font-bold py-1.5 rounded text-sm transition-colors mb-1">
            ESTABLECER TARA
          </button>
          <button onClick={iniciarCalibracion} disabled={!["CONECTADO", "ESPERANDO"].includes(estado)} className="w-full bg-[#1a5276] hover:bg-blue-800 disabled:opacity-50 text-white font-bold py-1.5 rounded text-sm transition-colors mb-1">
            CALIBRAR
          </button>
          <button onClick={handleArmar} disabled={!["ESPERANDO", "ARMADO"].includes(estado)} className="w-full bg-[#d68910] hover:bg-yellow-600 disabled:opacity-50 text-white font-bold py-2 rounded text-sm transition-colors mt-2">
            {estado === "ARMADO" ? "DESARMAR" : "ARMAR ENSAYO"}
          </button>
          <button onClick={handlePausa} disabled={!["QUEMANDO", "PAUSADO"].includes(estado)} className="w-full bg-[#d68910] hover:bg-yellow-600 disabled:opacity-50 text-white font-bold py-1.5 rounded text-sm transition-colors mt-1">
            {estado === "PAUSADO" ? "REANUDAR" : "PAUSAR"}
          </button>
        </aside>
 
        <main className="flex-1 min-w-0 bg-white rounded shadow-sm border border-gray-300 p-2 relative">
          <div className="absolute top-4 left-4 z-10 text-xs font-bold text-gray-500">
            EMPUJE EN TIEMPO REAL · {cfg.motor_nombre}
          </div>
          <UplotChart
            data={chartData}
            height={500}
            umbralIgnN={umbralIgnN}
            umbralApgN={umbralApgN}
            rangoMaxN={parseFloat(cfg.rango_esperado_n) || 10}
          />
        </main>
      </div>
 
      {/* MODAL: Fin de ensayo — guardar o rechazar */}
      {modalFin.open && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[#e0e0e0] p-6 rounded shadow-xl border border-gray-400 w-96 flex flex-col gap-4">
            <h2 className="text-[#1e8449] font-bold text-lg border-b border-gray-400 pb-2">
              ENSAYO FINALIZADO ✓
            </h2>
 
            <div className="bg-white border border-gray-300 rounded p-3 flex flex-col gap-1 text-sm font-mono">
              <div className="flex justify-between">
                <span className="text-gray-500">Motor</span>
                <span className="font-bold">{cfg.motor_nombre}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Empuje máx.</span>
                <span className="font-bold text-[#c0392b]">{modalFin.empujeMax} N</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Impulso total</span>
                <span className="font-bold text-[#1a5276]">{modalFin.impulso} N·s</span>
              </div>
            </div>
 
            <p className="text-xs text-gray-600">
              ¿Deseas guardar este ensayo en disco o descartarlo y volver a ESPERANDO?
            </p>
 
            <div className="flex gap-3 mt-2">
              <button
                onClick={handleRechazar}
                className="flex-1 px-4 py-2 bg-gray-400 hover:bg-gray-500 text-white font-bold rounded transition-colors"
              >
                DESCARTAR
              </button>
              <button
                onClick={handleGuardar}
                className="flex-1 px-4 py-2 bg-[#1e8449] hover:bg-green-700 text-white font-bold rounded transition-colors"
              >
                GUARDAR
              </button>
            </div>
          </div>
        </div>
      )}
 
      {/* MODAL: Calibración */}
      {calib.open && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[#e0e0e0] p-6 rounded shadow-xl border border-gray-400 w-96 flex flex-col gap-4">
            <h2 className="text-[#c0392b] font-bold text-lg border-b border-gray-400 pb-2">CALIBRACIÓN DE CELDA</h2>
            <p className="text-sm whitespace-pre-wrap">{calib.msg}</p>
 
            {calib.step === 1 && (
              <input
                type="number"
                value={calib.masaKg}
                onChange={(e) => setCalib({ ...calib, masaKg: e.target.value })}
                className="border p-2 rounded text-center"
                placeholder="Masa (kg)"
              />
            )}
 
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setCalib({ open: false })} className="px-4 py-2 bg-gray-400 text-white font-bold rounded">Cancelar</button>
              <button onClick={pasoCalibracion} className="px-4 py-2 bg-[#1a5276] text-white font-bold rounded">
                {calib.step === 0 ? "Iniciar" : calib.step === 1 ? "Confirmar Peso" : "Guardar Factor"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}