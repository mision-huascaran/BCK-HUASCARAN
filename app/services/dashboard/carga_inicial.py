"""Carga inicial del Dashboard (CU033 y CU035, paso 3): colegios del alcance, años con
sus PA y la selección con la que se abre la pantalla."""
from typing import Optional

from sqlmodel import Session, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import AnioEscolar, PeriodoAcademico, Usuario
from app.schemas.dashboard import AnioConPeriodos, FiltrosDisponibles, SeleccionInicial
from app.services.calendario import cargar_calendarios
from app.services.dashboard.filtros import colegios_del_alcance, item_colegio, periodo_visible


def _anios(db: Session) -> list[AnioConPeriodos]:
    """Años escolares registrados, del más antiguo al más reciente, con sus PA en orden."""
    filas = db.exec(
        select(AnioEscolar, PeriodoAcademico)
        .outerjoin(PeriodoAcademico, PeriodoAcademico.id_anio_escolar == AnioEscolar.id_anio_escolar)
        .order_by(AnioEscolar.fecha_inicio, AnioEscolar.id_anio_escolar, PeriodoAcademico.numero)
    ).all()
    anios: dict[int, AnioConPeriodos] = {}
    for anio, periodo in filas:
        item = anios.setdefault(
            anio.id_anio_escolar, AnioConPeriodos(id=anio.id_anio_escolar, nombre=anio.nombre, periodos=[])
        )
        if periodo is not None:
            item.periodos.append(periodo_visible(periodo))
    return list(anios.values())


def seleccion_inicial(db: Session, ids_colegio: list[int]) -> Optional[SeleccionInicial]:
    """El PA en que están hoy todos los colegios, con su año escolar. En un receso (PR n)
    cuenta el PA anterior (PA n) y su año, aunque el receso ya esté en el año calendario
    siguiente.

    None si no hay colegios, si alguno no está hoy en un PA ni en un PR configurado, o si
    no coinciden en el mismo PA: no se elige un calendario por aproximación.
    """
    if not ids_colegio:
        return None
    hoy = hoy_lima()
    calendarios = cargar_calendarios(db, ids_colegio=ids_colegio)
    elegidas = set()
    for id_colegio in ids_colegio:
        calendario = calendarios.calendario_en(id_colegio, hoy)
        periodo = calendario.periodo_de(hoy) if calendario is not None else None
        if periodo is None or periodo.id_periodo_academico is None:
            return None
        elegidas.add((calendario.id_anio_escolar, periodo.id_periodo_academico))
    if len(elegidas) != 1:
        return None
    id_anio, id_periodo = elegidas.pop()
    return SeleccionInicial(id_anio_escolar=id_anio, id_periodo_academico=id_periodo)


def filtros_disponibles(db: Session, usuario: Usuario) -> FiltrosDisponibles:
    colegios = colegios_del_alcance(db, usuario)
    return FiltrosDisponibles(
        colegios=[item_colegio(c) for c in colegios],
        anios=_anios(db),
        seleccion_inicial=seleccion_inicial(db, [c.id_colegio for c in colegios]),
    )
