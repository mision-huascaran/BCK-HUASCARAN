"""Salud del Sistema (CU012, tarjeta del Directivo).

    salud = sincronizados / (sincronizados + pendientes reportados) × 100

redondeado al entero más cercano; null ("Sin datos disponibles") si no hay ni unos ni
otros. Sincronizados: los registros obligatorios del PA vigente de cada colegio ya
confirmados en la BD. Pendientes reportados: los avisos de `operacion_pendiente` de los
cuatro módulos.

Cada módulo aporta su contador en `CONTADORES` (módulo → función); un módulo nuevo se
suma ahí sin tocar la fórmula.
"""
from typing import Callable, Optional

from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from app.models.evaluacion import Asistencia
from app.models.trazabilidad import OperacionPendiente
from app.services.calendario import PeriodoCalendario, pa_actual_de_colegios

PaPorColegio = dict[int, PeriodoCalendario]
ContadorModulo = Callable[[Session, PaPorColegio], int]

MODULOS_SALUD = ("asistencias", "lectura", "rubrica", "registro_vuelo")


def asistencias_confirmadas(db: Session, pa_por_colegio: PaPorColegio) -> int:
    """Asistencia: una unidad por alumno y clase del PA vigente de su colegio con estado."""
    if not pa_por_colegio:
        return 0
    en_pa = or_(
        *(
            and_(Asistencia.id_colegio == id_colegio, Asistencia.fecha.between(pa.fecha_inicio, pa.fecha_fin))
            for id_colegio, pa in pa_por_colegio.items()
        )
    )
    return db.exec(
        select(func.count()).select_from(Asistencia).where(col(Asistencia.estado).is_not(None), en_pa)
    ).one()


CONTADORES: dict[str, ContadorModulo] = {"asistencias": asistencias_confirmadas}


def porcentaje(sincronizados: int, pendientes: int) -> Optional[int]:
    """Redondeo al entero más cercano (0,5 hacia arriba); None si no hay datos."""
    total = sincronizados + pendientes
    if total == 0:
        return None
    return (200 * sincronizados + total) // (2 * total)


def pendientes_reportados(db: Session) -> int:
    return db.exec(
        select(func.count())
        .select_from(OperacionPendiente)
        .where(col(OperacionPendiente.modulo).in_(MODULOS_SALUD))
    ).one()


def salud_sistema(db: Session) -> Optional[int]:
    pa_por_colegio = pa_actual_de_colegios(db, solo_activos=True)
    sincronizados = sum(contar(db, pa_por_colegio) for contar in CONTADORES.values())
    return porcentaje(sincronizados, pendientes_reportados(db))
