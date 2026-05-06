"""
state.py — Máquina de estados del ensayo.

Define los estados posibles y las transiciones válidas.

Flujo normal:
    DESCONECTADO → CONECTADO → ESPERANDO → ARMADO → QUEMANDO → FINALIZADO
                                                          ↕
                                                       PAUSADO
"""


class EstadoEnsayo:
    DESCONECTADO = "DESCONECTADO"
    CONECTADO    = "CONECTADO"
    ESPERANDO    = "ESPERANDO"
    ARMADO       = "ARMADO"
    QUEMANDO     = "QUEMANDO"
    PAUSADO      = "PAUSADO"
    FINALIZADO   = "FINALIZADO"

    # Transiciones válidas desde cada estado
    TRANSICIONES: dict[str, list[str]] = {
        DESCONECTADO: [CONECTADO],
        CONECTADO:    [DESCONECTADO, ESPERANDO],
        ESPERANDO:    [CONECTADO, ARMADO],
        ARMADO:       [ESPERANDO, QUEMANDO],
        QUEMANDO:     [PAUSADO, FINALIZADO],
        PAUSADO:      [QUEMANDO, FINALIZADO],
        FINALIZADO:   [ESPERANDO, DESCONECTADO],
    }

    @classmethod
    def es_valida(cls, desde: str, hacia: str) -> bool:
        """Verifica si la transición *desde* → *hacia* es permitida."""
        return hacia in cls.TRANSICIONES.get(desde, [])
