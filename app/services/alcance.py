"""Alcance del Docente: los colegios y grados que tiene a cargo en una fecha.

Se usa para recortar lo que ve y lo que puede registrar un Docente. Una asignación está
habilitada en una fecha si su PA es el PA en que está SU colegio en esa fecha, según el
calendario de ese colegio (CU027): dos colegios pueden estar en periodos distintos el
mismo día.

Punto de extensión (T4): las asignaciones retroactivas en un PR y los permisos
excepcionales de CU028 se suman en `asignaciones_habilitadas`.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import Colegio, DocenteColegioGrado
from app.services.calendario import Calendarios, cargar_calendarios


@dataclass(frozen=True)
class AsignacionHabilitada:
    id_colegio: int
    id_grado: int
    id_periodo_academico: int


def _asignaciones(db: Session, id_docente: int, solo_colegios_activos: bool) -> list[tuple[int, int, int]]:
    """(colegio, grado, periodo) de todas las asignaciones del docente."""
    consulta = select(
        DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado, DocenteColegioGrado.id_periodo_academico
    ).where(DocenteColegioGrado.id_docente == id_docente)
    if solo_colegios_activos:
        consulta = consulta.join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio).where(
            col(Colegio.activo).is_(True)
        )
    return list(db.exec(consulta).all())


def asignaciones_habilitadas(
    db: Session,
    id_docente: int,
    en: Optional[date] = None,
    solo_colegios_activos: bool = True,
    calendarios: Optional[Calendarios] = None,
) -> list[AsignacionHabilitada]:
    """Asignaciones del docente cuyo PA es el PA de su colegio en la fecha `en` (hoy por
    defecto). Vacía si ninguno de sus colegios está en un PA: entre periodos no se inventa
    uno. Con `calendarios` ya cargados no vuelve a leerlos."""
    dia = en or hoy_lima()
    filas = _asignaciones(db, id_docente, solo_colegios_activos)
    if not filas:
        return []
    calendarios = calendarios or cargar_calendarios(db, ids_colegio={c for c, _, _ in filas})
    habilitadas = []
    for id_colegio, id_grado, id_periodo in filas:
        pa = calendarios.pa_de(id_colegio, dia)
        if pa is not None and pa.id_periodo_academico == id_periodo:
            habilitadas.append(AsignacionHabilitada(id_colegio, id_grado, id_periodo))
    return habilitadas


def alcance_docente(db: Session, id_docente: int, calendarios: Optional[Calendarios] = None) -> set[tuple[int, int]]:
    """Pares (id_colegio, id_grado) habilitados hoy para el docente, en colegios activos."""
    return {(a.id_colegio, a.id_grado) for a in asignaciones_habilitadas(db, id_docente, calendarios=calendarios)}


def tenia_asignacion(db: Session, id_docente: int, dia: date, solo_colegios_activos: bool) -> bool:
    """Si el docente tenía alguna asignación habilitada en `dia` (día de Lima), cada una
    según el calendario de su colegio.

    Sirve para fechas pasadas, p. ej. una actividad sincronizada después de un nuevo
    login. `solo_colegios_activos` exige además que el colegio siga activo hoy (no se
    guarda desde cuándo está inactivo, así que solo tiene sentido para lo que ocurre ahora).
    """
    return bool(asignaciones_habilitadas(db, id_docente, dia, solo_colegios_activos))
