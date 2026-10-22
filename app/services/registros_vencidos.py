"""Registros vencidos e incompletos (CU011). Por ahora solo de Asistencias: Lectura,
Rúbrica y Registro de Vuelo se agregan en T10.

Se calcula al consultar, sobre el PA en que hoy está cada colegio según SU calendario
(CU027): un colegio en un PR o fuera de todo periodo no tiene nada que vencer.

- Obligaciones: las clases programadas vigentes del PA en curso (app/services/
  programacion.py; las reprogramadas, en su fecha nueva), hasta el día anterior, de los
  grados con docente asignado en ese PA. La unidad es una clase (grado × sección × fecha).
- VENCIDA: sin ninguna asistencia.
- INCOMPLETA: con asistencias, pero algún alumno de su lista (`clase_alumno`) sin estado.

La lista de cada clase está congelada, así que mover a un alumno de grupo no vuelve
vencida ni incompleta una clase pasada.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.evaluacion import Asistencia, Clase, ClaseAlumno
from app.models.organizacion import DocenteColegioGrado
from app.services.calendario import (
    Calendarios,
    PeriodoCalendario,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
)
from app.services.programacion import ClaseProgramada, FiltroGrupos, clases_programadas

UN_DIA = timedelta(days=1)


@dataclass(frozen=True)
class ClaseObligatoria:
    """Una obligación de Asistencias: colegio, grado, sección, fecha y el docente asignado."""

    id_colegio: int
    id_grado: int
    id_seccion: int
    fecha: date
    id_docente: int


@dataclass
class EstadoRegistros:
    vencidas: list[ClaseObligatoria] = field(default_factory=list)
    incompletas: list[ClaseObligatoria] = field(default_factory=list)
    # Colegios con alguna asistencia en su PA actual.
    colegios_con_registros: set[int] = field(default_factory=set)


def _docentes_por_grado(db: Session, pa_actual: dict[int, PeriodoCalendario]) -> dict[tuple[int, int], list[int]]:
    """{(colegio, grado): docentes asignados en el PA actual de ese colegio}."""
    filas = db.exec(
        select(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado, DocenteColegioGrado.id_docente).where(
            en_pa_de_su_colegio(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_periodo_academico, pa_actual)
        )
    ).all()
    por_grado: dict[tuple[int, int], list[int]] = defaultdict(list)
    for id_colegio, id_grado, id_docente in filas:
        por_grado[(id_colegio, id_grado)].append(id_docente)
    return por_grado


def _incompletas(db: Session, ids_clase: list[int]) -> set[int]:
    """De esas clases, las que tienen algún alumno de su lista sin estado."""
    if not ids_clase:
        return set()
    return set(
        db.exec(
            select(ClaseAlumno.id_clase)
            .outerjoin(
                Asistencia,
                (Asistencia.id_clase == ClaseAlumno.id_clase) & (Asistencia.id_alumno == ClaseAlumno.id_alumno),
            )
            .where(col(ClaseAlumno.id_clase).in_(ids_clase), col(Asistencia.estado).is_(None))
            .distinct()
        ).all()
    )


def _colegios_con_registros(db: Session, pa_actual: dict[int, PeriodoCalendario]) -> set[int]:
    return set(
        db.exec(
            select(Clase.id_colegio)
            .where(
                en_pa_de_su_colegio(Clase.id_colegio, Clase.id_periodo_academico, pa_actual),
                col(Clase.id_clase).in_(select(Asistencia.id_clase)),
            )
            .distinct()
        ).all()
    )


def _obligaciones(
    db: Session, calendarios: Calendarios, id_colegio: int, pa: PeriodoCalendario, hasta: date
) -> list[ClaseProgramada]:
    if hasta < pa.fecha_inicio:
        return []
    clases = clases_programadas(db, FiltroGrupos(id_colegio), pa.fecha_inicio, min(pa.fecha_fin, hasta), calendarios)
    return [c for c in clases if c.id_periodo_academico == pa.id_periodo_academico]


def estado_de_registros(db: Session, calendarios: Optional[Calendarios] = None) -> EstadoRegistros:
    """Clases vencidas e incompletas del PA actual de cada colegio hasta ayer. Con
    `calendarios` ya cargados no vuelve a leerlos."""
    calendarios = calendarios or cargar_calendarios(db)
    pa_actual = pa_actual_de_colegios(db, solo_activos=True, calendarios=calendarios)
    if not pa_actual:
        return EstadoRegistros()
    docentes = _docentes_por_grado(db, pa_actual)
    ayer = hoy_lima() - UN_DIA
    obligaciones = [
        (id_colegio, clase)
        for id_colegio in sorted({c for c, _ in docentes})
        for clase in _obligaciones(db, calendarios, id_colegio, pa_actual[id_colegio], ayer)
        if (id_colegio, clase.id_grado) in docentes
    ]
    incompletas = _incompletas(db, [c.id_clase for _, c in obligaciones if c.tiene_asistencias])
    estado = EstadoRegistros(colegios_con_registros=_colegios_con_registros(db, pa_actual))
    for id_colegio, clase in obligaciones:
        destino = estado.incompletas if clase.id_clase in incompletas else None
        if not clase.tiene_asistencias:
            destino = estado.vencidas
        if destino is None:
            continue
        for id_docente in docentes[(id_colegio, clase.id_grado)]:
            destino.append(ClaseObligatoria(id_colegio, clase.id_grado, clase.id_seccion, clase.fecha, id_docente))
    return estado
