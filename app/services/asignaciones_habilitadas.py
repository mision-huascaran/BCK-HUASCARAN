"""Qué asignaciones habilitan hoy a un docente en cada colegio (CU011, CU012), según el
calendario de ese colegio:

- en un PA: las de ese PA;
- en un PR: las retroactivas habilitadas del PA anterior y las del PA siguiente;
- con `cierre_usa_ultimo_pa` (Colegios Operando), en el PR4 o después del fin del año:
  las del último PA del año.

Se resuelve una vez por colegio y se aplica como una condición SQL sobre
`docente_colegio_grado`.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import and_, false, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from app.models.organizacion import TIPO_PR, DocenteColegioGrado
from app.services.calendario import Calendarios, PeriodoCalendario

ULTIMO_PR = 4


@dataclass
class Habilitantes:
    """Los PA cuyas asignaciones cuentan en el colegio: cualquiera (`periodos`) o solo
    las retroactivas (`retroactivas`)."""

    periodos: set[int] = field(default_factory=set)
    retroactivas: set[int] = field(default_factory=set)


def _siguiente_pa(calendarios: Calendarios, id_colegio: int, despues_de: date) -> Optional[PeriodoCalendario]:
    return next((p for p in calendarios.pas_del_colegio(id_colegio) if p.fecha_inicio > despues_de), None)


def _ultimo_pa_terminado(calendarios: Calendarios, id_colegio: int, hoy: date) -> Optional[PeriodoCalendario]:
    terminados = [p for p in calendarios.pas_del_colegio(id_colegio) if p.fecha_fin < hoy]
    return terminados[-1] if terminados else None


def _en_receso(
    calendarios: Calendarios, id_colegio: int, periodo: PeriodoCalendario, hoy: date, cierre_usa_ultimo_pa: bool
) -> Habilitantes:
    if cierre_usa_ultimo_pa and periodo.numero == ULTIMO_PR:
        return Habilitantes(periodos={periodo.id_periodo_academico})
    siguiente = _siguiente_pa(calendarios, id_colegio, hoy)
    return Habilitantes(
        periodos={siguiente.id_periodo_academico} if siguiente else set(),
        retroactivas={periodo.id_periodo_academico},
    )


def habilitantes_del_colegio(
    calendarios: Calendarios, id_colegio: int, hoy: date, cierre_usa_ultimo_pa: bool = False
) -> Habilitantes:
    periodo = calendarios.periodo_de(id_colegio, hoy)
    if periodo is None:
        ultimo = _ultimo_pa_terminado(calendarios, id_colegio, hoy) if cierre_usa_ultimo_pa else None
        return Habilitantes(periodos={ultimo.id_periodo_academico} if ultimo else set())
    if periodo.tipo == TIPO_PR:
        return _en_receso(calendarios, id_colegio, periodo, hoy, cierre_usa_ultimo_pa)
    return Habilitantes(periodos={periodo.id_periodo_academico})


def habilitantes(
    calendarios: Calendarios, ids_colegio: list[int], hoy: date, cierre_usa_ultimo_pa: bool = False
) -> dict[int, Habilitantes]:
    return {c: habilitantes_del_colegio(calendarios, c, hoy, cierre_usa_ultimo_pa) for c in ids_colegio}


def asignacion_habilitada_sql(por_colegio: dict[int, Habilitantes]) -> ColumnElement[bool]:
    """Condición sobre `DocenteColegioGrado`: la asignación cuenta hoy en su colegio."""
    condiciones = []
    for id_colegio, h in por_colegio.items():
        periodo = DocenteColegioGrado.id_periodo_academico
        cuenta = or_(
            col(periodo).in_(h.periodos) if h.periodos else false(),
            and_(col(DocenteColegioGrado.retroactiva).is_(True), col(periodo).in_(h.retroactivas))
            if h.retroactivas else false(),
        )
        condiciones.append(and_(DocenteColegioGrado.id_colegio == id_colegio, cuenta))
    return or_(*condiciones) if condiciones else false()
