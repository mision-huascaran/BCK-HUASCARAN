"""Alcance del Docente: los colegios y grados que tiene a cargo hoy.

Se usa para recortar lo que ve y lo que puede registrar un Docente.
"""
from datetime import date

from sqlmodel import Session, col, select

from app.models.organizacion import Colegio, DocenteColegioGrado, PeriodoAcademico
from app.services.calendario import periodo_vigente


def alcance_docente(db: Session, id_docente: int) -> set[tuple[int, int]]:
    """Pares (id_colegio, id_grado) asignados al docente en el periodo vigente.

    Excluye los colegios inactivos. Vacío si no hay periodo vigente (entre periodos
    ningún docente tiene alcance: no se inventa un periodo que no está en curso).
    """
    periodo = periodo_vigente(db)
    if periodo is None:
        return set()
    filas = db.exec(
        select(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            DocenteColegioGrado.id_docente == id_docente,
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
            col(Colegio.activo).is_(True),
        )
    ).all()
    return {(id_colegio, id_grado) for id_colegio, id_grado in filas}


def tenia_asignacion(db: Session, id_docente: int, dia: date, solo_colegios_activos: bool) -> bool:
    """Si el docente tenía alguna asignación en el periodo que contiene `dia` (día de Lima).

    Sirve para fechas pasadas, p. ej. una actividad sincronizada después de un nuevo
    login. `solo_colegios_activos` exige además que el colegio siga activo hoy (no se
    guarda desde cuándo está inactivo, así que solo tiene sentido para lo que ocurre ahora).
    """
    consulta = (
        select(DocenteColegioGrado.id_docente)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .where(
            DocenteColegioGrado.id_docente == id_docente,
            PeriodoAcademico.fecha_inicio <= dia,
            PeriodoAcademico.fecha_fin >= dia,
        )
    )
    if solo_colegios_activos:
        consulta = consulta.join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio).where(
            col(Colegio.activo).is_(True)
        )
    return db.exec(consulta.limit(1)).first() is not None
