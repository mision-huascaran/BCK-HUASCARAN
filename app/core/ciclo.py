"""Ciclo EBR del alumno (diseño v3, `alumno`; CU014): se calcula, no se guarda.

Si el subprograma es Alfabetización, el ciclo es III; si es Comprensión Lectora, es el
ciclo de su grado (2.º: III; 3.º y 4.º: IV; 5.º y 6.º: V), que viene del catálogo
`grado.id_ciclo`.
"""
from sqlalchemy import case, literal

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


def ciclo_sql(nombre_programa, nombre_ciclo_del_grado):
    """La misma regla que `calcular_ciclo`, como expresión SQL, para filtrar y paginar por
    ciclo en la base de datos. Recibe las columnas `programa.nombre` y `ciclo_ebr.nombre`
    (del grado)."""
    return case(
        (nombre_programa == ALFABETIZACION, literal(CICLO_ALFABETIZACION)),
        else_=nombre_ciclo_del_grado,
    )


# En 1.º grado todos los alumnos son de Alfabetización (diseño v3, `alumno`).
PRIMER_GRADO = "1.º"


def programa_permitido_en_grado(nombre_grado: str, nombre_programa: str) -> bool:
    return nombre_grado != PRIMER_GRADO or nombre_programa == ALFABETIZACION
