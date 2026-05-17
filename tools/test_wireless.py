"""
test_wireless.py — Script para probar el LectorWireless y su resiliencia.
"""

import time
from core.wireless_reader import LectorWireless

def main():
    print("==================================================")
    print("  Iniciando prueba del LectorWireless             ")
    print("==================================================\n")

    lector = LectorWireless(host="127.0.0.1", puerto=8080)
    lector.start()

    # Esperamos 1 segundo para dar tiempo a la conexión y recolección de paquetes
    time.sleep(1.0)

    print("\n--- Extrayendo 20 valores de la cola ---")
    extraidos = 0
    while extraidos < 20:
        if not lector.cola.empty():
            val = lector.cola.get()
            print(f"[{extraidos+1:02d}] Valor en bruto: {val:.2f}")
            extraidos += 1
        else:
            time.sleep(0.01)

    print(f"\n[?] Señal de conexión post-lectura: {lector.señal_conexion}% (Debería estar ~100%)")

    print("\n--- Monitoreando evento de pérdida de conexión y reconexión ---")
    print("El simulador forzará la desconexión a los 3s de iniciada la sesión.")
    
    # Monitoreamos segundo a segundo durante 15 segundos
    for i in range(15):
        estado = f"Señal: {lector.señal_conexion:3d}% | Elementos en buffer: {lector.cola.qsize():4d}"
        if lector.señal_conexion == 0:
            estado += "  <-- ENLACE CAÍDO"
        elif lector.señal_conexion == 100 and i > 5:
            estado += "  <-- ENLACE RECUPERADO"
            
        print(f"[t={i:02d}s] {estado}")
        time.sleep(1.0)

    print("\n[i] Prueba finalizada. Limpiando recursos...")
    lector.detener()

if __name__ == "__main__":
    main()