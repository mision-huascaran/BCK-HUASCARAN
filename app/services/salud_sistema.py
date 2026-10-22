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

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models.evaluacion import Asistencia, Clase
from app.models.trazabilidad import OperacionPendiente
from app.services.calendario import PeriodoCalendario, en_pa_de_su_colegio, pa_actual_de_colegios
from app.services.lectura_service import filas_confirmadas
from app.services.rubrica_service import rubricas_completadas

PaPorColegio = dict[int, PeriodoCalendario]
ContadorModulo = Callable[[Session, PaPorColegio], int]

MODULOS_SALUD = ("asistencias", "lectura", "rubrica", "registro_vuelo")


def asistencias_confirmadas(db: Session, pa_por_colegio: PaPorColegio) -> int:
    """Asistencia: una unidad por alumno y clase del PA vigente de su colegio con estado."""
    return db.exec(
        select(func.count())
        .select_from(Asistencia)
        .join(Clase, Clase.id_clase == Asistencia.id_clase)
        .where(
            col(Asistencia.estado).is_not(None),
            en_pa_de_su_colegio(Clase.id_colegio, Clase.id_periodo_academico, pa_por_colegio),
        )
    ).one()


CONTADORES: dict[str, ContadorModulo] = {
    "asistencias": asistencias_confirmadas,
    "lectura": filas_confirmadas,
    "rubrica": rubricas_completadas,
}


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
