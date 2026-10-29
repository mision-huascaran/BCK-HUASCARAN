"""Salud del Sistema (CU012, tarjeta del Directivo).

    salud = sincronizados / (sincronizados + pendientes reportados) × 100

redondeado al entero más cercano; null ("Sin datos disponibles") si no hay ni unos ni
otros. Solo cuentan los cuatro módulos operativos y cada registro una sola vez, por su
clave canónica (app/services/claves_registro.py):

- Registros guardados del PA vigente de su colegio: Asistencia, cada alumno en una clase
  con estado u observación; Lectura, cada fila con algún dato del Docente (LSN, LSL u
  observación) y 1 por tabla "No realizada" (sus filas no cuentan); Rúbrica, cada
  evaluación mensual guardada (aunque esté incompleta); Registro de Vuelo, cada alumno ×
  PA guardado (aunque falte el nivel colocado).
- Afectados: claves con un aviso de pendiente (`operacion_pendiente`) o cuya última
  operación quedó rechazada sin una aceptada ni un descarte posterior.
- Sincronizados: los guardados cuya clave no está afectada.
- Pendientes: claves afectadas distintas, más los avisos sin clave (uno por
  `id_operacion`, fronts anteriores). Los rechazos sin clave (operaciones anteriores a
  esta regla) no se cuentan.

Cada módulo aporta en `CONTADORES` (módulo → función) las claves de sus registros
guardados; un módulo nuevo se suma ahí sin tocar la fórmula. Sin consultas por registro:
una por módulo y dos para los afectados.
"""
from typing import Callable, Optional

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.evaluacion import (
    LECTURA_NO_REALIZADA,
    Asistencia,
    Clase,
    LecturaSemanal,
    LecturaSemanalAlumno,
    RegistroVuelo,
    RubricaMensual,
)
from app.models.trazabilidad import RECHAZADA, OperacionPendiente, OperacionSincronizacion
from app.services import claves_registro
from app.services.calendario import PeriodoCalendario, en_pa_de_su_colegio, pa_actual_de_colegios

PaPorColegio = dict[int, PeriodoCalendario]
ContadorModulo = Callable[[Session, PaPorColegio], list[str]]

MODULOS_SALUD = ("asistencias", "lectura", "rubrica", "registro_vuelo")
CAMPOS_CON_DATOS_LECTURA = ("lsn_titulo", "lsn_nota", "lsn_maximo", "cantidad_lsl", "observacion")


def _de_la_clase_vigente(pa_por_colegio: PaPorColegio):
    return en_pa_de_su_colegio(Clase.id_colegio, Clase.id_periodo_academico, pa_por_colegio)


def asistencias_guardadas(db: Session, pa_por_colegio: PaPorColegio) -> list[str]:
    """Cada alumno en una clase del PA vigente de su colegio con estado u observación."""
    filas = db.exec(
        select(Clase.id_colegio, Clase.id_grado, Clase.id_seccion, Clase.fecha, Asistencia.id_alumno)
        .join(Clase, Clase.id_clase == Asistencia.id_clase)
        .where(
            or_(col(Asistencia.estado).is_not(None), col(Asistencia.observacion).is_not(None)),
            _de_la_clase_vigente(pa_por_colegio),
        )
    ).all()
    return [claves_registro.asistencia(*fila) for fila in filas]


def _tablas_del_pa_vigente(pa_por_colegio: PaPorColegio):
    return (
        select(
            LecturaSemanal.id_colegio, LecturaSemanal.id_grado, LecturaSemanal.id_seccion,
            LecturaSemanal.id_programa, LecturaSemanal.id_semana,
        )
        .join(Clase, Clase.id_clase == LecturaSemanal.id_clase_referencia)
        .where(_de_la_clase_vigente(pa_por_colegio))
    )


def lecturas_guardadas(db: Session, pa_por_colegio: PaPorColegio) -> list[str]:
    """Filas con algún dato del Docente de las tablas "normal" y 1 por tabla "No
    realizada", de las tablas cuya clase de referencia es del PA vigente de su colegio."""
    con_datos = or_(*(col(getattr(LecturaSemanalAlumno, campo)).is_not(None) for campo in CAMPOS_CON_DATOS_LECTURA))
    filas = db.exec(
        _tablas_del_pa_vigente(pa_por_colegio)
        .add_columns(LecturaSemanalAlumno.id_alumno)
        .join(LecturaSemanalAlumno, LecturaSemanalAlumno.id_lectura_semanal == LecturaSemanal.id)
        .where(LecturaSemanal.estado != LECTURA_NO_REALIZADA, con_datos)
    ).all()
    no_realizadas = db.exec(
        _tablas_del_pa_vigente(pa_por_colegio).where(LecturaSemanal.estado == LECTURA_NO_REALIZADA)
    ).all()
    return [claves_registro.fila_lectura(*f) for f in filas] + [claves_registro.tabla_lectura(*t) for t in no_realizadas]


def rubricas_guardadas(db: Session, pa_por_colegio: PaPorColegio) -> list[str]:
    """Cada evaluación mensual del PA vigente de su colegio, aunque esté incompleta."""
    filas = db.exec(
        select(RubricaMensual.id_alumno, RubricaMensual.id_anio_escolar, RubricaMensual.mes).where(
            en_pa_de_su_colegio(RubricaMensual.id_colegio, RubricaMensual.id_periodo_academico, pa_por_colegio)
        )
    ).all()
    return [claves_registro.rubrica(*fila) for fila in filas]


def vuelos_guardados(db: Session, pa_por_colegio: PaPorColegio) -> list[str]:
    """Cada registro alumno × PA vigente de su colegio, aunque falte el nivel colocado."""
    filas = db.exec(
        select(
            RegistroVuelo.id_colegio, RegistroVuelo.id_grado, RegistroVuelo.id_seccion, RegistroVuelo.id_programa,
            RegistroVuelo.id_anio_escolar, RegistroVuelo.id_periodo_academico, RegistroVuelo.id_alumno,
        ).where(en_pa_de_su_colegio(RegistroVuelo.id_colegio, RegistroVuelo.id_periodo_academico, pa_por_colegio))
    ).all()
    return [claves_registro.registro_vuelo(*fila) for fila in filas]


CONTADORES: dict[str, ContadorModulo] = {
    "asistencias": asistencias_guardadas,
    "lectura": lecturas_guardadas,
    "rubrica": rubricas_guardadas,
    "registro_vuelo": vuelos_guardados,
}


def porcentaje(sincronizados: int, pendientes: int) -> Optional[int]:
    """Redondeo al entero más cercano (0,5 hacia arriba); None si no hay datos."""
    total = sincronizados + pendientes
    if total == 0:
        return None
    return (200 * sincronizados + total) // (2 * total)


def _avisos(db: Session) -> tuple[set[str], int]:
    """(claves con avisos de pendiente, avisos sin clave) de los cuatro módulos."""
    filas = db.exec(
        select(OperacionPendiente.clave_registro, func.count())
        .where(col(OperacionPendiente.modulo).in_(MODULOS_SALUD))
        .group_by(OperacionPendiente.clave_registro)
    ).all()
    claves = {clave for clave, _ in filas if clave is not None}
    sin_clave = sum(cantidad for clave, cantidad in filas if clave is None)
    return claves, sin_clave


def rechazadas_sin_resolver(db: Session) -> set[str]:
    """Claves cuya última operación (por llegada) quedó rechazada: sin una aceptada ni un
    descarte posterior."""
    orden = func.row_number().over(
        partition_by=OperacionSincronizacion.clave_registro,
        order_by=(col(OperacionSincronizacion.recibida_en).desc(), col(OperacionSincronizacion.id_operacion).desc()),
    ).label("orden")
    ultimas = (
        select(OperacionSincronizacion.clave_registro, OperacionSincronizacion.estado, orden)
        .where(
            col(OperacionSincronizacion.modulo).in_(MODULOS_SALUD),
            col(OperacionSincronizacion.clave_registro).is_not(None),
        )
        .subquery()
    )
    return set(db.exec(
        select(ultimas.c.clave_registro).where(ultimas.c.orden == 1, ultimas.c.estado == RECHAZADA)
    ).all())


def conteo(db: Session) -> tuple[int, int]:
    """(sincronizados, pendientes reportados)."""
    pa_por_colegio = pa_actual_de_colegios(db, solo_activos=True)
    con_aviso, sin_clave = _avisos(db)
    afectados = con_aviso | rechazadas_sin_resolver(db)
    sincronizados = sum(
        1 for contar in CONTADORES.values() for clave in contar(db, pa_por_colegio) if clave not in afectados
    )
    return sincronizados, len(afectados) + sin_clave


def salud_sistema(db: Session) -> Optional[int]:
    return porcentaje(*conteo(db))
