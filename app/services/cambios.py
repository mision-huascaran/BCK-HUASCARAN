"""Cambios hechos dentro de una actividad, leídos de `auditoria` por `id_actividad`
(CU017, CU019, CU021).

Un "cambio" es un registro distinto afectado (`tabla`, `id_registro`): crear un alumno
con seis campos es un cambio; editar dos campos de un alumno, también. Cada tabla
pertenece a un módulo operativo; una tabla sin módulo no rompe nada: cuenta en "otros"
y se avisa en el log para agregarla al mapeo.
"""
import logging
import uuid
from collections import defaultdict
from typing import Iterable

from sqlalchemy import func, union
from sqlmodel import Session, col, select

from app.core.ciclo import ciclo_sql
from app.models.evaluacion import (
    Asistencia,
    EvaluacionDiagnosticaAlumno,
    LecturaSemanalAlumno,
    NivelFinalMensual,
)
from app.models.organizacion import Alumno, CicloEbr, Colegio, Grado, Programa, Seccion
from app.models.trazabilidad import Auditoria
from app.schemas.actividad import AsignacionInvolucrada, CambiosModulo, ProductividadModulo
from app.schemas.comun import ItemCatalogo
from app.services.auditoria import ACTIVAR, CREAR, EDITAR, INACTIVAR

logger = logging.getLogger(__name__)

RUBRICA = "rubrica"
LECTURA = "seguimiento_lectura"
VUELO = "registro_vuelo"
ALUMNOS = "alumnos"
ASISTENCIA = "asistencia"
OTROS = "otros"

# Tabla auditada -> módulo operativo. Los CU nombran Rúbrica, Seguimiento de Lectura y
# Registro de Vuelo; Alumnos y Asistencia también requieren actividad y se cuentan.
MODULOS = {
    "nivel_final_mensual": RUBRICA,
    "lectura_semanal": LECTURA,
    "lectura_semanal_alumno": LECTURA,
    "evaluacion_diagnostica_alumno": VUELO,
    "alumno": ALUMNOS,
    "asistencia": ASISTENCIA,
}
# Orden de CU021 (productividad), que siguen también CU017 y CU019.
ORDEN_MODULOS = (ASISTENCIA, RUBRICA, VUELO, LECTURA, ALUMNOS, OTROS)

# Nombre de la cantidad en la productividad de CU021: siempre en plural, también con 1
# ("1 Asistencias"), como lo pide el CU.
NOMBRES = {
    ASISTENCIA: "Asistencias",
    RUBRICA: "Rúbricas",
    VUELO: "Registros de Vuelo",
    LECTURA: "Seguimientos de Lectura",
    ALUMNOS: "Alumnos",
    OTROS: "Otros registros",
}
SIN_REGISTROS = "Sin registros"

CAMPO_POR_ACCION = {CREAR: "creados", EDITAR: "editados", ACTIVAR: "activados", INACTIVAR: "inactivados"}

# Cómo llegar del registro auditado a su alumno, para las asignaciones involucradas.
CON_ID_ALUMNO = {
    "asistencia": Asistencia,
    "evaluacion_diagnostica_alumno": EvaluacionDiagnosticaAlumno,
    "lectura_semanal_alumno": LecturaSemanalAlumno,
    "nivel_final_mensual": NivelFinalMensual,
}

_avisadas: set[str] = set()


def modulo_de(tabla: str) -> str:
    modulo = MODULOS.get(tabla)
    if modulo is None:
        if tabla not in _avisadas:
            _avisadas.add(tabla)
            logger.warning("La tabla auditada '%s' no tiene módulo asignado: se cuenta en '%s'.", tabla, OTROS)
        return OTROS
    return modulo


def _ordenados(conteos: dict[str, int]) -> list[tuple[str, int]]:
    return [(m, conteos[m]) for m in ORDEN_MODULOS if conteos.get(m)]


# ── Listados (CU017, CU021) ─────────────────────────────────────────────────────────

def cambios_por_actividad(db: Session, ids_actividad: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    """{id_actividad: {módulo: registros distintos}} de varias actividades, en una consulta."""
    ids = list(ids_actividad)
    resultado: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if not ids:
        return resultado
    filas = db.exec(
        select(Auditoria.id_actividad, Auditoria.tabla, func.count(func.distinct(Auditoria.id_registro)))
        .where(col(Auditoria.id_actividad).in_(ids))
        .group_by(Auditoria.id_actividad, Auditoria.tabla)
    ).all()
    for id_actividad, tabla, cantidad in filas:
        resultado[id_actividad][modulo_de(tabla)] += cantidad
    return resultado


def productividad(conteos: dict[str, int]) -> list[ProductividadModulo]:
    """Solo los módulos con cambios, en orden fijo."""
    return [ProductividadModulo(modulo=m, cantidad=n) for m, n in _ordenados(conteos)]


def productividad_texto(conteos: dict[str, int]) -> str:
    """'15 Asistencias, 5 Rúbricas' o 'Sin registros'."""
    partes = [f"{n} {NOMBRES[m]}" for m, n in _ordenados(conteos)]
    return ", ".join(partes) if partes else SIN_REGISTROS


# ── Detalle (CU019) ─────────────────────────────────────────────────────────────────

def registros_de(db: Session, id_actividad: uuid.UUID) -> list[tuple[str, str, str]]:
    """(tabla, id_registro, acción) distintos que tocó la actividad."""
    return list(
        db.exec(
            select(Auditoria.tabla, Auditoria.id_registro, Auditoria.accion)
            .where(Auditoria.id_actividad == id_actividad)
            .distinct()
        ).all()
    )


def resumen_por_modulo(registros: list[tuple[str, str, str]]) -> list[CambiosModulo]:
    """Registros distintos por módulo y, dentro, por acción. Un registro creado y luego
    editado en la misma actividad cuenta una vez en el total y una vez en cada acción."""
    total: dict[str, set] = defaultdict(set)
    por_accion: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for tabla, id_registro, accion in registros:
        modulo = modulo_de(tabla)
        total[modulo].add((tabla, id_registro))
        por_accion[modulo][accion].add((tabla, id_registro))
    return [
        CambiosModulo(
            modulo=modulo,
            total=len(total[modulo]),
            **{campo: len(por_accion[modulo][accion]) for accion, campo in CAMPO_POR_ACCION.items()},
        )
        for modulo in ORDEN_MODULOS
        if total.get(modulo)
    ]


def _ids_enteros(registros: list[tuple[str, str, str]], tabla: str) -> set[int]:
    return {int(r) for t, r, _ in registros if t == tabla and r.isdigit()}


def asignaciones_involucradas(db: Session, registros: list[tuple[str, str, str]]) -> list[AsignacionInvolucrada]:
    """Colegio, grado, ciclo y sección de los alumnos de los registros tocados, según el
    estado actual de cada alumno. Una sola consulta."""
    consultas = []
    ids = _ids_enteros(registros, "alumno")
    if ids:
        consultas.append(select(Alumno.id_alumno).where(col(Alumno.id_alumno).in_(ids)))
    for tabla, modelo in CON_ID_ALUMNO.items():
        ids = _ids_enteros(registros, tabla)
        if ids:
            consultas.append(select(modelo.id_alumno).where(col(modelo.id).in_(ids)))
    # Una tabla de lectura (crearla, marcarla no realizada) involucra a los alumnos de sus filas.
    ids = _ids_enteros(registros, "lectura_semanal")
    if ids:
        consultas.append(
            select(LecturaSemanalAlumno.id_alumno).where(col(LecturaSemanalAlumno.id_lectura_semanal).in_(ids))
        )
    if not consultas:
        return []

    alumnos = union(*consultas).subquery()
    ciclo = ciclo_sql(Programa.nombre, CicloEbr.nombre)
    filas = db.exec(
        select(Colegio.id_colegio, Colegio.nombre, Seccion.nombre, Grado.id_grado, Grado.nombre, ciclo)
        .select_from(Alumno)
        .join(alumnos, alumnos.c[0] == Alumno.id_alumno)
        .join(Colegio, Colegio.id_colegio == Alumno.id_colegio)
        .join(Grado, Grado.id_grado == Alumno.id_grado)
        .join(Seccion, Seccion.id_seccion == Alumno.id_seccion)
        .join(CicloEbr, CicloEbr.id_ciclo == Grado.id_ciclo)
        .join(Programa, Programa.id_programa == Alumno.id_programa_actual)
        .distinct()
    ).all()
    # Se ordena aquí: con DISTINCT, PostgreSQL exige que el ORDER BY repita la expresión
    # exacta del ciclo, y sus parámetros se renderizan aparte.
    filas = sorted(filas, key=lambda f: (f[1], f[3], f[2], f[5]))
    return [
        AsignacionInvolucrada(
            colegio=ItemCatalogo(id=id_colegio, nombre=colegio),
            grado=ItemCatalogo(id=id_grado, nombre=grado),
            ciclo=nombre_ciclo,
            seccion=seccion,
        )
        for id_colegio, colegio, seccion, id_grado, grado, nombre_ciclo in filas
    ]
