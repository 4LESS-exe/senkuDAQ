"""
state.py — Máquina de estados del ensayo para la arquitectura inalámbrica.
Gestiona de forma centralizada las transiciones válidas y notifica a la interfaz.
"""

from typing import Callable, List

# ===========================================================================
# CONSTANTES DE ESTADO
# ===========================================================================

DESCONECTADO = "DESCONECTADO"
CONECTADO    = "CONECTADO"
ESPERANDO    = "ESPERANDO"
ARMADO       = "ARMADO"
QUEMANDO     = "QUEMANDO"
PAUSADO      = "PAUSADO"
RECONECTANDO = "RECONECTANDO"
FINALIZADO   = "FINALIZADO"


# ===========================================================================
# TABLA DE TRANSICIONES
# ===========================================================================

TRANSICIONES: dict[str, list[str]] = {
    DESCONECTADO: [CONECTADO],
    CONECTADO:    [ESPERANDO, DESCONECTADO],
    ESPERANDO:    [ARMADO, CONECTADO, DESCONECTADO],
    ARMADO:       [QUEMANDO, ESPERANDO, DESCONECTADO],
    QUEMANDO:     [PAUSADO, FINALIZADO, RECONECTANDO],
    PAUSADO:      [QUEMANDO, FINALIZADO],
    RECONECTANDO: [QUEMANDO, FINALIZADO],
    FINALIZADO:   [ESPERANDO, DESCONECTADO],
}


# ===========================================================================
# EXCEPCIÓN PERSONALIZADA
# ===========================================================================

class TransicionInvalida(Exception):
    """Excepción lanzada cuando se intenta una transición de estado no permitida."""
    def __init__(self, actual: str, pedido: str):
        validas = TRANSICIONES.get(actual, [])
        mensaje = f"No se puede ir de {actual} → {pedido}. Válidas desde {actual}: {validas}"
        super().__init__(mensaje)
        self.actual = actual
        self.pedido = pedido


# ===========================================================================
# MÁQUINA DE ESTADO
# ===========================================================================

class MaquinaEstado:
    """Clase principal que maneja el ciclo de vida del ensayo."""
    
    def __init__(self):
        self._estado: str = DESCONECTADO
        self._observers: List[Callable[[str, str], None]] = []

    @property
    def estado(self) -> str:
        return self._estado

    def transicionar(self, nuevo: str) -> None:
        if nuevo not in TRANSICIONES.get(self._estado, []):
            raise TransicionInvalida(self._estado, nuevo)

        anterior = self._estado
        self._estado = nuevo
        print(f"[Estado] {anterior} → {nuevo}")

        for callback in self._observers:
            callback(anterior, nuevo)

    def agregar_observer(self, callback: Callable[[str, str], None]) -> None:
        """
        Registra una función que será notificada en cada cambio de estado.
        La firma del callback debe ser: callback(anterior: str, nuevo: str)
        """
        self._observers.append(callback)

    # -----------------------------------------------------------------------
    # Helpers de consulta
    # -----------------------------------------------------------------------

    def es_activo(self) -> bool:
        return self._estado in [QUEMANDO, PAUSADO, RECONECTANDO]

    def puede_tarar(self) -> bool:
        return self._estado in [CONECTADO, ESPERANDO]

    def puede_armar(self) -> bool:
        return self._estado == ESPERANDO

    def puede_calibrar(self) -> bool:
        return self._estado in [CONECTADO, ESPERANDO]

    def requiere_datos_seguros(self) -> bool:
        return self._estado in [QUEMANDO, PAUSADO, RECONECTANDO]
