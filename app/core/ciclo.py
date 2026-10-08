"""Ciclo EBR del alumno (diseño v3, `alumno`; CU014): se calcula, no se guarda.

Si el subprograma es Alfabetización, el ciclo es III; si es Comprensión Lectora, es el
ciclo de su grado (2.º: III; 3.º y 4.º: IV; 5.º y 6.º: V), que viene del catálogo
`grado.id_ciclo`.
"""
ALFABETIZACION = "Alfabetización"
CICLO_ALFABETIZACION = "III"

# Para ordenar los ciclos en las respuestas (III, IV, V) y no alfabéticamente.
ORDEN_CICLOS = ("III", "IV", "V")


def calcular_ciclo(programa: str, ciclo_del_grado: str) -> str:
    """Ciclo de un alumno a partir de su subprograma y del ciclo de su grado."""
    if programa == ALFABETIZACION:
        return CICLO_ALFABETIZACION
    return ciclo_del_grado


def ordenar_ciclos(ciclos: set[str]) -> list[str]:
    return sorted(ciclos, key=lambda c: ORDEN_CICLOS.index(c) if c in ORDEN_CICLOS else len(ORDEN_CICLOS))
