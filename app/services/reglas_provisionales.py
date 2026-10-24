"""Reglas de negocio con código (L1, R1...). Las provisionales son decisiones que el
equipo tomó para avanzar mientras el negocio (Carlos) no las define: llevan la marca
`# REGLA PROVISIONAL`, para encontrarlas y reemplazarlas cuando se confirmen. Las ya
confirmadas por el analista llevan `# REGLA CONFIRMADA`.

Sin dependencias de la app: se usan desde los modelos y los servicios.
"""
from collections import Counter
from datetime import date, datetime
from typing import Callable, Optional, TypeVar

T = TypeVar("T")
G = TypeVar("G")

# REGLA PROVISIONAL L1 (Seguimiento de Lectura, CU024): una clase puede ser la clase de
# referencia de una sola tabla de lectura por subprograma. La clase ya no es por
# subprograma (es del grupo), así que en una misma clase pueden leer los alumnos de los
# dos subprogramas, cada uno en su tabla. Se aplica con este UNIQUE en `lectura_semanal`.
UNICA_TABLA_POR_CLASE_Y_SUBPROGRAMA = ("id_clase_referencia", "id_programa")


# REGLA CONFIRMADA R1 (Rúbrica mensual, CU029): cada mes pertenece a un solo PA, el que
# tiene más días lectivos dentro del mes según el calendario del colegio del grupo; si dos
# empatan, gana el que contiene el último día lectivo del mes. Recesos y días no lectivos
# no cuentan.
def regla_mes_a_pa(dias_lectivos: list[date], pa_de: Callable[[date], Optional[T]]) -> Optional[T]:
    """El PA con más días lectivos del mes (`pa_de` resuelve el de cada día con el
    calendario del colegio; se comparan por `id_periodo_academico`) y, en empate, el del
    último día lectivo; None si el mes no tiene días lectivos en ningún PA."""
    pas = {}
    conteo: Counter = Counter()
    ultimo_dia = {}
    for dia in sorted(dias_lectivos):
        pa = pa_de(dia)
        if pa is None:
            continue
        clave = pa.id_periodo_academico
        pas[clave] = pa
        conteo[clave] += 1
        ultimo_dia[clave] = dia
    if not conteo:
        return None
    return pas[max(conteo, key=lambda clave: (conteo[clave], ultimo_dia[clave]))]


# REGLA CONFIRMADA R2 (Rúbrica mensual, CU029, CU030): mientras el alumno no tenga una
# evaluación del mes, le corresponde el grupo de su última pertenencia válida en un día
# lectivo del mes (si el mes está en curso, solo los días ya transcurridos: los filtra el
# llamador). Una evaluación ya guardada conserva su grupo (lo resuelve rubrica_service).
def regla_grupo_del_mes(intervalos: list[tuple[date, Optional[date], G]], dias_lectivos: list[date]) -> Optional[G]:
    """De los intervalos [desde, hasta) de pertenencia del alumno, el grupo del último de
    esos días lectivos en que pertenecía a alguno; None si no estuvo ningún día."""
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
