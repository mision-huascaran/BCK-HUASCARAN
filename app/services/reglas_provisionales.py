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

# REGLA CONFIRMADA L1 (Seguimiento de Lectura, CU024): una clase puede ser la clase de
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


# REGLA PROVISIONAL DP-1 (Rúbrica mensual, CU029, D88). Decisión provisional del equipo,
# pendiente de validar con el analista después de la exposición del 29/10. Un mes partido
# entre dos PA (octubre de 2026: el PA3 termina el 16 y el PA4 empieza el 19) sigue
# asociado al PA de R1 (`rubrica_mensual.id_periodo_academico`, que usan el Registro de
# Vuelo y el Dashboard), pero:
# - lo puede evaluar un Docente con habilitación de escritura para el grupo en CUALQUIERA
#   de los PA con días lectivos en el mes (antes, solo en el de R1);
# - su obligación se exige (y vence el día 1 del mes siguiente) en todo PA con días
#   lectivos en el mes, aunque R1 lo asocie al anterior: octubre figura vencido el 01/11
#   con el PA4 en curso.
# Para revertirla: que `regla_pas_que_evaluan_el_mes` devuelva solo `[PA de R1]` y que
# `regla_mes_exigible_en_pa` compare con el PA de R1. Docs: docs/decisiones-provisionales.md.
def regla_pas_que_evaluan_el_mes(dias_lectivos: list[date], pa_de: Callable[[date], Optional[T]]) -> list[T]:
    """Los PA desde los que se puede evaluar el mes: el de R1 primero y después los demás
    PA con días lectivos en el mes, en orden cronológico. Vacía si no tiene días lectivos."""
    del_r1 = regla_mes_a_pa(dias_lectivos, pa_de)
    if del_r1 is None:
        return []
    otros = {}
    for dia in sorted(dias_lectivos):
        pa = pa_de(dia)
        if pa is not None and pa.id_periodo_academico != del_r1.id_periodo_academico:
            otros.setdefault(pa.id_periodo_academico, pa)
    return [del_r1, *otros.values()]


def regla_mes_exigible_en_pa(
    dias_lectivos: list[date], pa_de: Callable[[date], Optional[T]], id_periodo_academico: int
) -> bool:
    """Si la evaluación del mes se exige en ese PA (DP-1): si el mes tiene días lectivos de él."""
    return any(
        pa is not None and pa.id_periodo_academico == id_periodo_academico
        for pa in map(pa_de, dias_lectivos)
    )


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


# REGLA CONFIRMADA V1 (Registro de Vuelo, CU031, CU032): en PA2-PA4 la entrada es el nivel
# colocado confirmado del PA inmediatamente anterior del mismo alumno y año escolar, en
# cualquier grupo: la herencia sigue al alumno aunque se haya trasladado de colegio (se
# muestra "Registrado antes del traslado.", sin dar acceso a esa decisión). Decisión del
# equipo, no definida por el CU: si en el PA anterior tuvo varias filas (cambió de grupo),
# vale la decisión más reciente.
def regla_decision_vigente(decisiones: list[tuple[datetime, Optional[T]]]) -> Optional[T]:
    """De las decisiones (fecha_registro, decisión o None si no tiene nivel colocado) del
    alumno en un PA, en cualquier grupo, la más reciente con nivel; None si no hay."""
    con_nivel = [(fecha, decision) for fecha, decision in decisiones if decision is not None]
    return max(con_nivel, key=lambda par: par[0])[1] if con_nivel else None


# REGLA CONFIRMADA I1 (CU011 semana 12, "Docentes activos"): durante un PA cuentan los
# Docentes con una asignación habilitada para ese periodo conforme a CU016. Durante un PR,
# los que tienen al menos una asignación Retroactiva habilitada del PA inmediatamente
# anterior, un permiso excepcional vigente que autorice registrar o corregir, o una
# asignación válida para el PA inmediatamente siguiente aunque todavía no haya empezado;
# una asignación para un PA posterior a ese no basta. Cada Docente cuenta una vez, y su
# inclusión por una asignación próxima no le permite registrar durante el receso. Se
# aplica en `asignaciones_habilitadas.habilitantes` y en `inicio_service.docentes_activos`.
