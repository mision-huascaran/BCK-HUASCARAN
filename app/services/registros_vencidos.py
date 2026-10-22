"""Registros vencidos e incompletos (CU011). Por ahora solo de Asistencias: los módulos de
Rúbrica, Lectura y Vuelo aún no existen.

Se calcula al consultar, sobre el PA en que hoy está cada colegio según SU calendario
(CU027): un colegio en un PR o fuera de todo periodo no tiene nada que vencer. Número fijo
de consultas: los calendarios se cargan una sola vez.

- Grados que se exigen: los de colegios activos con `dia_clase` configurado, docente
  asignado en el PA actual de ese colegio y al menos un alumno activo.
- Clase esperada: cada semana del PA, la fecha de su `dia_clase`, si es un día lectivo
  del calendario del colegio (un día no lectivo no vence).
- VENCIDA: la clase esperada ya terminó (vence a las 23:59 de su día: fecha < hoy) y no
  hay ningún registro de asistencia de esa aula (colegio y grado guardados en el
  registro) con esa fecha.
- INCOMPLETA: hay registros de esa aula en esa fecha, pero alguno no tiene estado, o algún
  alumno que hoy es activo de ese colegio y grado no tiene registro con estado en ella. No
  se le exige a quien ya tiene registro esa fecha en otra aula (rotó ese día: su
  asistencia se tomó allá).

El aula de cada registro es la que guarda el registro, así que rotar a un alumno no vuelve
vencida ni incompleta una clase pasada. La unidad es un grado en una fecha.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Callable, Optional

from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.evaluacion import Asistencia
from app.models.organizacion import Alumno, Colegio, ColegioGrado, DocenteColegioGrado
from app.services.calendario import (
    Calendarios,
    PeriodoCalendario,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
)

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
    # Colegios con algún registro de asistencia en su PA actual.
    colegios_con_registros: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _GradoExigido:
    id_colegio: int
    id_grado: int
    dia_clase: int
    id_docente: int


@dataclass
class _Clase:
    """Lo registrado en un aula en una fecha."""

    con_estado: set[int] = field(default_factory=set)
    alguno_sin_estado: bool = False


def _grados_exigidos(db: Session, pa_actual: dict[int, PeriodoCalendario]) -> list[_GradoExigido]:
    """Grados de colegios activos con día de clase, docente en el PA actual de su colegio
    y algún alumno activo."""
    activos = (
        select(Alumno.id_colegio, Alumno.id_grado)
        .where(col(Alumno.activo).is_(True))
        .group_by(Alumno.id_colegio, Alumno.id_grado)
        .subquery()
    )
    filas = db.exec(
        select(ColegioGrado.id_colegio, ColegioGrado.id_grado, ColegioGrado.dia_clase, DocenteColegioGrado.id_docente)
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
            en_pa_de_su_colegio(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_periodo_academico, pa_actual),
        )
    ).all()
    return [_GradoExigido(*fila) for fila in filas]


def _fechas_de_clase(dia_clase: int, desde: date, hasta: date, es_lectivo: Callable[[date], bool]) -> list[date]:
    """Fechas del `dia_clase` (1 = lunes) en [desde, hasta] que son días lectivos."""
    primera = desde + timedelta(days=(dia_clase - 1 - desde.weekday()) % 7)
    fechas = []
    while primera <= hasta:
        if es_lectivo(primera):
            fechas.append(primera)
        primera += UNA_SEMANA
    return fechas


@dataclass
class _Registros:
    """Lo registrado en el periodo, indexado para evaluar cada clase esperada."""

    # (colegio, grado, fecha) -> lo registrado en esa clase.
    clases: dict[tuple[int, int, date], _Clase] = field(default_factory=lambda: defaultdict(_Clase))
    # (alumno, fecha) -> aulas (colegio, grado) en que tiene registro.
    aulas_del_alumno: dict[tuple[int, date], set[tuple[int, int]]] = field(default_factory=lambda: defaultdict(set))

    def es_incompleta(self, aula: tuple[int, int], fecha: date, alumnos_del_aula: set[int]) -> bool:
        """Algún registro de la clase sin estado, o algún alumno de hoy del aula sin
        registro con estado en ella (salvo que ya tenga registro esa fecha en otra aula)."""
        clase = self.clases[(*aula, fecha)]
        exigidos = {a for a in alumnos_del_aula if self.aulas_del_alumno.get((a, fecha), set()) <= {aula}}
        return clase.alguno_sin_estado or bool(exigidos - clase.con_estado)


def _registros_del_periodo(db: Session, desde: date, hasta: date) -> _Registros:
    registros = _Registros()
    for id_colegio, id_grado, fecha, id_alumno, estado in db.exec(
        select(Asistencia.id_colegio, Asistencia.id_grado, Asistencia.fecha, Asistencia.id_alumno, Asistencia.estado)
        .where(Asistencia.fecha >= desde, Asistencia.fecha <= hasta)
    ).all():
        clase = registros.clases[(id_colegio, id_grado, fecha)]
        if estado is None:
            clase.alguno_sin_estado = True
        else:
            clase.con_estado.add(id_alumno)
        registros.aulas_del_alumno[(id_alumno, fecha)].add((id_colegio, id_grado))
    return registros


def _alumnos_activos_por_aula(db: Session) -> dict[tuple[int, int], set[int]]:
    por_aula: dict[tuple[int, int], set[int]] = defaultdict(set)
    for id_alumno, id_colegio, id_grado in db.exec(
        select(Alumno.id_alumno, Alumno.id_colegio, Alumno.id_grado).where(col(Alumno.activo).is_(True))
    ).all():
        por_aula[(id_colegio, id_grado)].add(id_alumno)
    return por_aula


def _evaluar_grado(
    grado: _GradoExigido,
    pa: PeriodoCalendario,
    calendarios: Calendarios,
    registros: _Registros,
    alumnos_del_aula: set[int],
    estado: EstadoRegistros,
) -> None:
    """Suma a `estado` las clases vencidas e incompletas del grado en su PA, hasta hoy."""
    hoy = hoy_lima()
    aula = (grado.id_colegio, grado.id_grado)
    lectivo = partial(calendarios.es_lectivo, grado.id_colegio)
    for fecha in _fechas_de_clase(grado.dia_clase, pa.fecha_inicio, min(pa.fecha_fin, hoy), lectivo):
        clase = Clase(grado.id_colegio, grado.id_grado, fecha, grado.id_docente)
        if (*aula, fecha) not in registros.clases:
            if fecha < hoy:
                estado.vencidas.append(clase)
        elif registros.es_incompleta(aula, fecha, alumnos_del_aula):
            estado.incompletas.append(clase)


def estado_de_registros(db: Session, calendarios: Optional[Calendarios] = None) -> EstadoRegistros:
    """Clases vencidas e incompletas del PA actual de cada colegio hasta hoy (incluido:
    una clase de hoy con registros a medias ya cuenta como incompleta; vencida, recién
    mañana). Con `calendarios` ya cargados no vuelve a leerlos."""
    calendarios = calendarios or cargar_calendarios(db)
    pa_actual = pa_actual_de_colegios(db, solo_activos=True, calendarios=calendarios)
    if not pa_actual:
        return EstadoRegistros()
    registros = _registros_del_periodo(
        db, min(pa.fecha_inicio for pa in pa_actual.values()), max(pa.fecha_fin for pa in pa_actual.values())
    )
    alumnos_por_aula = _alumnos_activos_por_aula(db)

    estado = EstadoRegistros(
        colegios_con_registros={
            c for c, _, fecha in registros.clases if c in pa_actual and pa_actual[c].contiene(fecha)
        }
    )
    for grado in _grados_exigidos(db, pa_actual):
        alumnos = alumnos_por_aula[(grado.id_colegio, grado.id_grado)]
        _evaluar_grado(grado, pa_actual[grado.id_colegio], calendarios, registros, alumnos, estado)
    return estado
