"""Calendario escolar: qué año y qué periodo están en curso hoy (día de Lima)."""
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import AnioEscolar, PeriodoAcademico


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
