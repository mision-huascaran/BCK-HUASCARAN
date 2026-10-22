"""Reglas provisionales: decisiones que el equipo tomó para avanzar mientras el negocio
(Carlos) no las define. Cada una lleva la marca `# REGLA PROVISIONAL` y su código (L1,
R1...), para encontrarlas y reemplazarlas cuando se confirmen.

Sin dependencias de la app: se usan desde los modelos y los servicios.
"""
from datetime import date, datetime
from typing import Callable, Optional, TypeVar

T = TypeVar("T")
G = TypeVar("G")

# REGLA PROVISIONAL L1 (Seguimiento de Lectura, CU024): una clase puede ser la clase de
# referencia de una sola tabla de lectura por subprograma. La clase ya no es por
# subprograma (es del grupo), así que en una misma clase pueden leer los alumnos de los
# dos subprogramas, cada uno en su tabla. Se aplica con este UNIQUE en `lectura_semanal`.
UNICA_TABLA_POR_CLASE_Y_SUBPROGRAMA = ("id_clase_referencia", "id_programa")


# REGLA PROVISIONAL R1 (Rúbrica mensual, CU029): el PA de un mes es el PA de su último
# día lectivo. Un mes partido entre dos PA se evalúa (y se habilita) con el segundo.
def regla_mes_a_pa(dias_lectivos: list[date], pa_de: Callable[[date], Optional[T]]) -> Optional[T]:
    """El PA del último de los días lectivos del mes (`pa_de` lo resuelve con el
    calendario del colegio); None si el mes no tiene días lectivos."""
    return pa_de(max(dias_lectivos)) if dias_lectivos else None


# REGLA PROVISIONAL R2 (Rúbrica mensual, CU029): si un alumno cambió de grupo durante el
# mes, se lo evalúa en el grupo al que pertenecía el último día lectivo del mes en que
# estuvo en alguno.
def regla_grupo_del_mes(intervalos: list[tuple[date, Optional[date], G]], dias_lectivos: list[date]) -> Optional[G]:
    """De los intervalos [desde, hasta) de pertenencia del alumno, el grupo del último día
    lectivo del mes en que pertenecía a alguno; None si no estuvo ningún día lectivo."""
    for dia in sorted(dias_lectivos, reverse=True):
        for desde, hasta, grupo in intervalos:
            if desde <= dia and (hasta is None or hasta > dia):
                return grupo
    return None


# REGLA PROVISIONAL V1 (Registro de Vuelo, CU031): la entrada de un PA es el nivel
# colocado del PA anterior del mismo alumno y año, en cualquier grupo: la herencia sigue al
# alumno aunque haya cambiado de colegio, grado, sección o subprograma. Si en el PA
# anterior tuvo varias filas (cambió de grupo), vale la decisión más reciente.
def regla_decision_vigente(decisiones: list[tuple[datetime, Optional[T]]]) -> Optional[T]:
    """De las decisiones (fecha_registro, decisión o None si no tiene nivel colocado) del
    alumno en un PA, en cualquier grupo, la más reciente con nivel; None si no hay."""
    con_nivel = [(fecha, decision) for fecha, decision in decisiones if decision is not None]
    return max(con_nivel, key=lambda par: par[0])[1] if con_nivel else None
