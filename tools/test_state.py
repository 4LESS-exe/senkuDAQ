"""
test_state.py — Tests unitarios para validar la máquina de estados.
"""

import sys
import os

# Permitir la importación del módulo 'core' desde la raíz del proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import (
    MaquinaEstado, TransicionInvalida,
    DESCONECTADO, CONECTADO, ESPERANDO, ARMADO,
    QUEMANDO, PAUSADO, RECONECTANDO, FINALIZADO
)

def correr_tests() -> None:
    # -------------------------------------------------------------------
    # Test 1: Flujo completo feliz
    # -------------------------------------------------------------------
    m = MaquinaEstado()
    assert m.estado == DESCONECTADO
    
    m.transicionar(CONECTADO)
    m.transicionar(ESPERANDO)
    m.transicionar(ARMADO)
    m.transicionar(QUEMANDO)
    m.transicionar(FINALIZADO)
    m.transicionar(DESCONECTADO)
    print("[✓] Flujo completo OK")

    # -------------------------------------------------------------------
    # Test 2: Transición inválida lanza excepción
    # -------------------------------------------------------------------
    m2 = MaquinaEstado()
    try:
        m2.transicionar(QUEMANDO)  # Inválido desde DESCONECTADO
        print("[✗] Debió lanzar excepción")
    except TransicionInvalida as e:
        print(f"[✓] Excepción correcta: {e}")

    # -------------------------------------------------------------------
    # Test 3: Flujo con RECONECTANDO
    # -------------------------------------------------------------------
    m3 = MaquinaEstado()
    m3.transicionar(CONECTADO)
    m3.transicionar(ESPERANDO)
    m3.transicionar(ARMADO)
    m3.transicionar(QUEMANDO)
    m3.transicionar(RECONECTANDO)
    m3.transicionar(QUEMANDO)      # reconexión exitosa
    m3.transicionar(FINALIZADO)
    print("[✓] Flujo RECONECTANDO OK")

    # -------------------------------------------------------------------
    # Test 4: Observer se llama correctamente
    # -------------------------------------------------------------------
    m4 = MaquinaEstado()
    historial = []
    
    # Callback para guardar el registro de transiciones
    m4.agregar_observer(lambda ant, nvo: historial.append((ant, nvo)))

    m4.transicionar(CONECTADO)
    m4.transicionar(ESPERANDO)

    assert historial[0] == (DESCONECTADO, CONECTADO)
    assert historial[1] == (CONECTADO, ESPERANDO)
    print("[✓] Observer OK")

    # -------------------------------------------------------------------
    # Test 5: Helpers de consulta
    # -------------------------------------------------------------------
    m5 = MaquinaEstado()
    assert m5.puede_tarar() is False
    assert m5.puede_armar() is False
    assert m5.es_activo() is False

    m5.transicionar(CONECTADO)
    m5.transicionar(ESPERANDO)
    assert m5.puede_tarar() is True
    assert m5.puede_armar() is True

    m5.transicionar(ARMADO)
    m5.transicionar(QUEMANDO)
    assert m5.es_activo() is True
    assert m5.requiere_datos_seguros() is True
    print("[✓] Helpers OK")

    print("\n[✓] Todos los tests pasaron")


if __name__ == "__main__":
    correr_tests()