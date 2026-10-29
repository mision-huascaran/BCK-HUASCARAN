"""Cobertura de alumnos y evolución anual (CU033 paso 9, CU035 pasos 5 y 8).

Alumno atendido en un año (A3): su pertenencia histórica se cruza con el año escolar de
su colegio (del inicio del PA1 al fin del PR4 de su calendario, hasta hoy si está en curso),
sin mirar su estado actual. Cuenta una sola vez en el total del año aunque haya cambiado
de colegio o subprograma, y una vez en cada colegio donde estuvo. No se suman los PA.
Colegio atendido: con al menos un alumno atendido.

Años con información: los registrados hasta el seleccionado (incluido) con calendario
aplicable y al menos un alumno atendido en los colegios seleccionados. Un año sin
alumnos atendidos no aparece: no es 0. Los libros de cada año son los de CU034.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import AlumnoPertenencia, AnioEscolar, Colegio
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import AnioEvolucion, CoberturaColegio, EvolucionAnual, IntervaloFechas, LibrosDeUnAnio
from app.services.calendario import CalendarioResuelto, Calendarios, cargar_calendarios
from app.services.dashboard.filtros import FiltrosResueltos, item_colegio
from app.services.dashboard.libros import acumular, aportes_del_anio

MENSAJE_UN_SOLO_ANIO = "No hay información de otros años para mostrar la evolución."


@dataclass(frozen=True)
class AnioDelAlcance:
    anio: AnioEscolar
    # {colegio: su calendario aplicable del año}, solo los que lo tienen.
    calendarios: dict[int, CalendarioResuelto]

    def rango(self, id_colegio: int, hoy: date) -> Optional[tuple[date, date]]:
        """Del inicio del año del colegio a min(fin, hoy); None si no tiene o no empezó."""
        calendario = self.calendarios.get(id_colegio)
        if calendario is None or calendario.fecha_inicio > hoy:
            return None
        return calendario.fecha_inicio, min(calendario.fecha_fin, hoy)

    @property
    def desde(self) -> date:
        return min(c.fecha_inicio for c in self.calendarios.values())

    @property
    def hasta(self) -> date:
        return max(c.fecha_fin for c in self.calendarios.values())


def _anios_hasta(db: Session, filtros: FiltrosResueltos) -> tuple[list[AnioDelAlcance], Calendarios]:
    """Los años hasta el seleccionado (incluido), en orden, con los calendarios de los
    colegios seleccionados (una sola carga para todos los años)."""
    anios = db.exec(
        select(AnioEscolar)
        .where(AnioEscolar.fecha_inicio <= filtros.anio.fecha_inicio)
        .order_by(AnioEscolar.fecha_inicio)
    ).all()
    cargados = cargar_calendarios(db, ids_colegio=filtros.ids_colegio)
    del_alcance = []
    for anio in anios:
        aplicables = {c: cargados.aplicable(c, anio.id_anio_escolar) for c in filtros.ids_colegio}
        calendarios = {c: cal for c, cal in aplicables.items() if cal is not None}
        if calendarios:
            del_alcance.append(AnioDelAlcance(anio, calendarios))
    return del_alcance, cargados


def _pertenencias(db: Session, ids_colegio: list[int], desde: date, hasta: date) -> list[AlumnoPertenencia]:
    return list(db.exec(select(AlumnoPertenencia).where(
        col(AlumnoPertenencia.id_colegio).in_(ids_colegio),
        AlumnoPertenencia.desde <= hasta,
        or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > desde),
    )).all())


def atendidos_por_colegio(
    anio: AnioDelAlcance, pertenencias: list[AlumnoPertenencia], hoy: date
) -> dict[int, set[int]]:
    """{colegio: alumnos atendidos en el año}; solo los colegios con alguno."""
    atendidos: dict[int, set[int]] = defaultdict(set)
    for pertenencia in pertenencias:
        rango = anio.rango(pertenencia.id_colegio, hoy)
        if rango is None:
            continue
        desde, hasta = rango
        if pertenencia.desde <= hasta and (pertenencia.hasta is None or pertenencia.hasta > desde):
            atendidos[pertenencia.id_colegio].add(pertenencia.id_alumno)
    return atendidos


def _anio_evolucion(
    db: Session, anio: AnioDelAlcance, atendidos: dict[int, set[int]], colegios: list[Colegio],
    cargados: Calendarios, hoy: date,
) -> AnioEvolucion:
    libros = acumular(aportes_del_anio(db, anio.calendarios, cargados, list(anio.calendarios)))
    return AnioEvolucion(
        anio_escolar=ItemCatalogo(id=anio.anio.id_anio_escolar, nombre=anio.anio.nombre),
        parcial=anio.desde <= hoy <= anio.hasta,
        alumnos_atendidos=len(set().union(*atendidos.values())),
        colegios_atendidos=len(atendidos),
        libros=LibrosDeUnAnio(total=libros.total_visible, estado=libros.estado),
        colegios=[
            CoberturaColegio(colegio=item_colegio(c), alumnos_atendidos=len(atendidos[c.id_colegio]))
            for c in colegios if c.id_colegio in atendidos
        ],
    )


def evolucion_anual(db: Session, filtros: FiltrosResueltos) -> EvolucionAnual:
    """Por año con información: alumnos y colegios atendidos (CU033) y libros (CU035)."""
    hoy = hoy_lima()
    anios, cargados = _anios_hasta(db, filtros)
    pertenencias = _pertenencias(db, filtros.ids_colegio, anios[0].desde, hoy) if anios else []
    evolucion = []
    for anio in anios:
        atendidos = atendidos_por_colegio(anio, pertenencias, hoy)
        if atendidos:
            evolucion.append(_anio_evolucion(db, anio, atendidos, filtros.colegios, cargados, hoy))
    con_informacion = [a for a in anios if a.anio.id_anio_escolar in {e.anio_escolar.id for e in evolucion}]
    intervalo = (
        IntervaloFechas(desde=con_informacion[0].desde, hasta=con_informacion[-1].hasta) if con_informacion else None
    )
    return EvolucionAnual(
        contexto=filtros.contexto(intervalo),
        anios=evolucion,
        mensaje=MENSAJE_UN_SOLO_ANIO if len(evolucion) == 1 else None,
    )
