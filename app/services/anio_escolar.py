"""Año escolar al día con su calendario general (CU027).

Decisión del equipo: las fechas del año escolar son las de su calendario GENERAL, del
inicio del PA1 al fin del PR4 (los particulares no las cambian). Con ellas se generan sus
semanas (`semana_reporte`), de lunes a viernes, con `numero_semana` correlativo: toda
semana con al menos un día hábil dentro del año.

Lo usan la API (crear y editar el general) y el CLI `cargar-calendario`. Si el nuevo rango
dejaría fuera semanas que ya tienen tablas de lectura, el llamador rechaza el cambio
(`semanas_en_conflicto`); las semanas sin tablas que quedan fuera se eliminan: son datos
generados. Aquí no se hace commit.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Protocol

from sqlmodel import Session, col, select

from app.models.evaluacion import LecturaSemanal, SemanaReporte
from app.models.organizacion import TIPO_PA, TIPO_PR, AnioEscolar

LUNES, VIERNES = 0, 4
UNA_SEMANA = timedelta(days=7)
ULTIMO_PA = 4


class Periodo(Protocol):
    tipo: str
    numero: int
    fecha_inicio: date
    fecha_fin: date


@dataclass(frozen=True)
class SemanaDeseada:
    lunes: date
    viernes: date
    numero_semana: int


@dataclass
class CambiosDelAnio:
    """Lo que cambió al sincronizar (para el resumen del CLI)."""

    fechas: bool = False
    semanas: Counter = field(default_factory=Counter)


def rango_del_calendario(periodos: Iterable[Periodo]) -> tuple[date, date]:
    """(inicio del PA1, fin del PR4) de los periodos de un calendario."""
    por_clave = {(p.tipo, p.numero): p for p in periodos}
    return por_clave[(TIPO_PA, 1)].fecha_inicio, por_clave[(TIPO_PR, ULTIMO_PA)].fecha_fin


def semanas_deseadas(inicio: date, fin: date) -> list[SemanaDeseada]:
    """Semanas de lunes a viernes con al menos un día hábil en [inicio, fin] (el año
    escolar), numeradas 1..N."""
    lunes = inicio - timedelta(days=inicio.weekday() - LUNES)
    if lunes + timedelta(days=VIERNES) < inicio:
        lunes += UNA_SEMANA
    semanas = []
    while lunes <= fin:
        semanas.append(SemanaDeseada(lunes, lunes + timedelta(days=VIERNES), len(semanas) + 1))
        lunes += UNA_SEMANA
    return semanas


def _semanas_del_anio(db: Session, anio: AnioEscolar) -> list[SemanaReporte]:
    if anio.id_anio_escolar is None:
        return []
    return list(db.exec(select(SemanaReporte).where(SemanaReporte.id_anio_escolar == anio.id_anio_escolar)).all())


def semanas_en_conflicto(db: Session, anio: Optional[AnioEscolar], inicio: date, fin: date) -> list[LecturaSemanal]:
    """Tablas de lectura cuya semana quedaría fuera del año con el rango [inicio, fin]
    (por fecha de su semana)."""
    if anio is None or anio.id_anio_escolar is None:
        return []
    deseadas = {s.lunes for s in semanas_deseadas(inicio, fin)}
    fuera = [s.id_semana for s in _semanas_del_anio(db, anio) if s.fecha_inicio not in deseadas]
    if not fuera:
        return []
    return list(db.exec(
        select(LecturaSemanal).where(col(LecturaSemanal.id_semana).in_(fuera)).order_by(LecturaSemanal.id_semana)
    ).all())


def sincronizar_anio(
    db: Session, anio: AnioEscolar, inicio: date, fin: date, id_actor: int, ahora: datetime
) -> CambiosDelAnio:
    """Deja el año con fechas [inicio, fin] (las de su calendario general) y sus semanas.
    El llamador ya verificó `semanas_en_conflicto`."""
    cambios = CambiosDelAnio()
    if (anio.fecha_inicio, anio.fecha_fin) != (inicio, fin):
        anio.fecha_inicio, anio.fecha_fin = inicio, fin
        if anio.id_anio_escolar is not None:
            anio.modificado_por, anio.modificado_en = id_actor, ahora
        cambios.fechas = True
        db.add(anio)
        db.flush()
    pendientes = {s.lunes: s for s in semanas_deseadas(inicio, fin)}
    for semana in _semanas_del_anio(db, anio):
        deseada = pendientes.pop(semana.fecha_inicio, None)
        if deseada is None:
            # Sin tablas (lo garantiza semanas_en_conflicto): era una semana generada que ya no aplica.
            db.delete(semana)
            cambios.semanas["eliminadas"] += 1
            continue
        valores = (deseada.viernes, deseada.numero_semana)
        if (semana.fecha_fin, semana.numero_semana) != valores:
            semana.fecha_fin, semana.numero_semana = valores
            semana.modificado_por, semana.modificado_en = id_actor, ahora
            db.add(semana)
            cambios.semanas["actualizadas"] += 1
    db.flush()
    for deseada in pendientes.values():
        db.add(SemanaReporte(
            id_anio_escolar=anio.id_anio_escolar, numero_semana=deseada.numero_semana, fecha_inicio=deseada.lunes,
            fecha_fin=deseada.viernes, creado_por=id_actor, creado_en=ahora,
        ))
        cambios.semanas["creadas"] += 1
    db.flush()
    return cambios
