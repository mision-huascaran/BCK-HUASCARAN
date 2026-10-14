"""Registros vencidos e incompletos (CU011). Por ahora solo de Asistencias: los módulos de
Rúbrica, Lectura y Vuelo aún no existen.

Se calcula al consultar, sobre el periodo académico vigente (sin periodo vigente no hay
nada que vencer), con un número fijo de consultas.

- Grados que se exigen: los de colegios activos con `dia_clase` configurado, docente
  asignado en el periodo vigente y al menos un alumno activo.
- Clase esperada: cada semana del periodo, la fecha de su `dia_clase`, si cae dentro del
  periodo y no es un `dia_no_laborable` (un feriado no vence).
- VENCIDA: la clase esperada ya terminó (vence a las 23:59 de su día: fecha < hoy) y no
  hay ningún registro de asistencia de alumnos de ese colegio y grado con esa fecha.
- INCOMPLETA: hay registros en esa fecha para ese colegio y grado, pero algún alumno hoy
  activo de ese colegio y grado no tiene registro o lo tiene sin estado.

El colegio y el grado de un registro son los actuales de su alumno (la asistencia no los
guarda). La unidad es un grado en una fecha.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.evaluacion import Asistencia
from app.models.organizacion import Alumno, Colegio, ColegioGrado, DiaNoLaborable, DocenteColegioGrado
from app.services.calendario import periodo_vigente

UNA_SEMANA = timedelta(days=7)


@dataclass(frozen=True)
class Clase:
    """Clase esperada de un grado: colegio, grado, fecha y el docente asignado."""

    id_colegio: int
    id_grado: int
    fecha: date
    id_docente: int


@dataclass
class EstadoRegistros:
    vencidas: list[Clase] = field(default_factory=list)
    incompletas: list[Clase] = field(default_factory=list)
    # Colegios con algún registro de asistencia en el periodo vigente.
    colegios_con_registros: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _GradoExigido:
    id_colegio: int
    id_grado: int
    dia_clase: int
    id_docente: int
    alumnos_activos: int


def _grados_exigidos(db: Session, id_periodo: int) -> list[_GradoExigido]:
    activos = (
        select(Alumno.id_colegio, Alumno.id_grado, func.count().label("cantidad"))
        .where(col(Alumno.activo).is_(True))
        .group_by(Alumno.id_colegio, Alumno.id_grado)
        .subquery()
    )
    filas = db.exec(
        select(
            ColegioGrado.id_colegio, ColegioGrado.id_grado, ColegioGrado.dia_clase,
            DocenteColegioGrado.id_docente, activos.c.cantidad,
        )
        .join(Colegio, Colegio.id_colegio == ColegioGrado.id_colegio)
        .join(
            DocenteColegioGrado,
            (DocenteColegioGrado.id_colegio == ColegioGrado.id_colegio)
            & (DocenteColegioGrado.id_grado == ColegioGrado.id_grado),
        )
        .join(
            activos,
            (activos.c.id_colegio == ColegioGrado.id_colegio) & (activos.c.id_grado == ColegioGrado.id_grado),
        )
        .where(
            col(Colegio.activo).is_(True),
            col(ColegioGrado.dia_clase).is_not(None),
            DocenteColegioGrado.id_periodo_academico == id_periodo,
        )
    ).all()
    return [_GradoExigido(*fila) for fila in filas]


def _fechas_de_clase(dia_clase: int, desde: date, hasta: date, no_laborables: set[date]) -> list[date]:
    """Fechas del `dia_clase` (1 = lunes) en [desde, hasta], sin días no laborables."""
    primera = desde + timedelta(days=(dia_clase - 1 - desde.weekday()) % 7)
    fechas = []
    while primera <= hasta:
        if primera not in no_laborables:
            fechas.append(primera)
        primera += UNA_SEMANA
    return fechas


def estado_de_registros(db: Session) -> EstadoRegistros:
    """Clases vencidas e incompletas del periodo vigente hasta hoy (incluido: una clase
    de hoy con registros a medias ya cuenta como incompleta; vencida, recién mañana)."""
    periodo = periodo_vigente(db)
    if periodo is None:
        return EstadoRegistros()
    hoy = hoy_lima()
    desde, hasta = periodo.fecha_inicio, min(periodo.fecha_fin, hoy)

    registros = db.exec(
        select(
            Alumno.id_colegio, Alumno.id_grado, Asistencia.fecha,
            func.count(func.distinct(Alumno.id_alumno)).filter(
                col(Alumno.activo).is_(True), col(Asistencia.estado).is_not(None)
            ),
        )
        .join(Alumno, Alumno.id_alumno == Asistencia.id_alumno)
        .where(Asistencia.fecha >= periodo.fecha_inicio, Asistencia.fecha <= periodo.fecha_fin)
        .group_by(Alumno.id_colegio, Alumno.id_grado, Asistencia.fecha)
    ).all()
    # (colegio, grado, fecha) -> alumnos activos con estado registrado.
    marcados = {(c, g, f): n for c, g, f, n in registros}
    estado = EstadoRegistros(colegios_con_registros={c for c, _, _ in marcados})

    no_laborables = set(
        db.exec(select(DiaNoLaborable.fecha).where(DiaNoLaborable.fecha >= desde, DiaNoLaborable.fecha <= hasta)).all()
    )
    for grado in _grados_exigidos(db, periodo.id_periodo_academico):
        for fecha in _fechas_de_clase(grado.dia_clase, desde, hasta, no_laborables):
            clase = Clase(grado.id_colegio, grado.id_grado, fecha, grado.id_docente)
            con_estado = marcados.get((grado.id_colegio, grado.id_grado, fecha))
            if con_estado is None:
                if fecha < hoy:
                    estado.vencidas.append(clase)
            elif con_estado < grado.alumnos_activos:
                estado.incompletas.append(clase)
    return estado

