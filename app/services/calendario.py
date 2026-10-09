"""Calendario escolar: qué año y qué periodo están en curso hoy (día de Lima) y qué días
son hábiles."""
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import AnioEscolar, DiaNoLaborable, PeriodoAcademico

VIERNES = 4


def periodo_vigente(db: Session) -> Optional[PeriodoAcademico]:
    """El periodo cuyo rango contiene hoy, o None si estamos entre periodos."""
    hoy = hoy_lima()
    return db.exec(
        select(PeriodoAcademico).where(
            PeriodoAcademico.fecha_inicio <= hoy, PeriodoAcademico.fecha_fin >= hoy
        )
    ).first()


def anio_vigente(db: Session) -> Optional[AnioEscolar]:
    """El año escolar cuyo rango contiene hoy, o None si estamos entre años."""
    hoy = hoy_lima()
    return db.exec(
        select(AnioEscolar).where(AnioEscolar.fecha_inicio <= hoy, AnioEscolar.fecha_fin >= hoy)
    ).first()


def proximo_periodo(db: Session) -> Optional[PeriodoAcademico]:
    """El primer periodo que todavía no empezó, o None si no hay ninguno cargado."""
    return db.exec(
        select(PeriodoAcademico)
        .where(PeriodoAcademico.fecha_inicio > hoy_lima())
        .order_by(col(PeriodoAcademico.fecha_inicio))
    ).first()


def periodos_del_anio(db: Session, id_anio_escolar: int) -> list[PeriodoAcademico]:
    """Periodos del año, en orden cronológico."""
    return list(
        db.exec(
            select(PeriodoAcademico)
            .where(PeriodoAcademico.id_anio_escolar == id_anio_escolar)
            .order_by(col(PeriodoAcademico.fecha_inicio))
        ).all()
    )


def no_terminado(periodo: PeriodoAcademico) -> bool:
    """Vigente o futuro: su último día es hoy o después."""
    return periodo.fecha_fin >= hoy_lima()


def periodo_de_referencia(db: Session) -> Optional[PeriodoAcademico]:
    """Periodo al que se asigna un dato que no trae periodo propio (p. ej. el subprograma
    de un alumno): el vigente; entre periodos, el próximo; si no hay ninguno futuro, el
    último cargado. None solo si no hay ningún periodo."""
    return (
        periodo_vigente(db)
        or proximo_periodo(db)
        or db.exec(select(PeriodoAcademico).order_by(col(PeriodoAcademico.fecha_inicio).desc())).first()
    )


def anio_de_referencia(db: Session) -> Optional[AnioEscolar]:
    """Año escolar que muestran las vistas del año en curso: el vigente; entre años, el
    último que ya empezó (el que acaba de terminar, con datos); si ninguno empezó, el
    último cargado."""
    vigente = anio_vigente(db)
    if vigente is not None:
        return vigente
    ya_empezado = db.exec(
        select(AnioEscolar)
        .where(AnioEscolar.fecha_inicio <= hoy_lima())
        .order_by(col(AnioEscolar.fecha_inicio).desc())
    ).first()
    return ya_empezado or db.exec(
        select(AnioEscolar).order_by(col(AnioEscolar.fecha_inicio).desc())
    ).first()


# ── Días hábiles ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DiasHabiles:
    """Días hábiles de un rango, ya cargados y en orden, para contar muchos subrangos
    (p. ej. uno por docente) sin volver a consultar la BD."""

    dias: tuple[date, ...]

    def contar(self, desde: date, hasta: date) -> int:
        """Cuántos días hábiles hay en [desde, hasta). Solo ve los días cargados."""
        if hasta <= desde:
            return 0
        return bisect_left(self.dias, hasta) - bisect_left(self.dias, desde)


def cargar_dias_habiles(db: Session, desde: date, hasta: date) -> DiasHabiles:
    """Días hábiles de [desde, hasta): lunes a viernes, dentro de algún periodo académico
    y que no estén en `dia_no_laborable`. Dos consultas, sin importar el largo del rango."""
    if hasta <= desde:
        return DiasHabiles(())
    periodos = db.exec(
        select(PeriodoAcademico.fecha_inicio, PeriodoAcademico.fecha_fin).where(
            PeriodoAcademico.fecha_inicio < hasta, PeriodoAcademico.fecha_fin >= desde
        )
    ).all()
    no_laborables = set(
        db.exec(
            select(DiaNoLaborable.fecha).where(DiaNoLaborable.fecha >= desde, DiaNoLaborable.fecha < hasta)
        ).all()
    )
    dias: set[date] = set()
    for fecha_inicio, fecha_fin in periodos:
        dia = max(fecha_inicio, desde)
        ultimo = min(fecha_fin, hasta - timedelta(days=1))
        while dia <= ultimo:
            if dia.weekday() <= VIERNES and dia not in no_laborables:
                dias.add(dia)
            dia += timedelta(days=1)
    return DiasHabiles(tuple(sorted(dias)))


def dias_habiles(db: Session, desde: date, hasta: date) -> int:
    """Cuántos días hábiles hay en [desde, hasta)."""
    return cargar_dias_habiles(db, desde, hasta).contar(desde, hasta)
