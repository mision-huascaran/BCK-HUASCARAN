"""Importación de la nómina de alumnos (CU014): vista previa, confirmación y consulta.

La vista previa no escribe nada ni se guarda en el servidor: su huella resume el análisis
y la confirmación vuelve a analizar el archivo y exige la misma huella. Los alumnos
importados quedan con la pertenencia pendiente.
"""
import uuid
from collections import defaultdict
from typing import Optional

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, hoy_lima
from app.core.texto import nombre_comparable
from app.models.organizacion import Alumno, Colegio, ColegioGrado, Grado, ImportacionAlumnos, Usuario
from app.schemas.importacion import ObservacionImportacion, ResultadoImportacion, VistaPreviaImportacion
from app.services.auditoria import IMPORTAR, registrar_auditoria
from app.services.importacion_nomina import (
    Analisis,
    Contexto,
    Nomina,
    clasificar,
    clave_de_nombre,
    colegio_de,
    dnis_de,
    leer_grado,
    leer_nomina,
)

MENSAJE_SIN_ALUMNOS = "No hay alumnos para importar en este archivo."
MENSAJE_DESACTUALIZADA = "Los datos cambiaron desde la vista previa. Carga el archivo nuevamente."
MENSAJE_NO_ENCONTRADA = "No se encontró la importación."
MENSAJE_EXISTENTE = "Ya existe una importación de otro usuario con ese identificador."
CAMPO_ARCHIVO = "archivo"
TABLA_ALUMNO = "alumno"


def _desactualizada() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_DESACTUALIZADA, motivo="importacion_desactualizada")


# ── Análisis ────────────────────────────────────────────────────────────────────────

def _colegios_activos(db: Session) -> dict[str, int]:
    return {
        nombre_comparable(nombre): id_colegio
        for id_colegio, nombre in db.exec(
            select(Colegio.id_colegio, Colegio.nombre).where(col(Colegio.activo).is_(True))
        ).all()
    }


def _contexto(db: Session, nomina: Nomina) -> Contexto:
    """Lo que el análisis necesita de la base, en una consulta por concepto."""
    colegios = _colegios_activos(db)
    involucrados = {i for i in (colegio_de(f, colegios) for f in nomina.filas) if i is not None}

    grados_por_colegio: dict[int, set[int]] = defaultdict(set)
    for id_colegio, id_grado in db.exec(
        select(ColegioGrado.id_colegio, ColegioGrado.id_grado).where(col(ColegioGrado.id_colegio).in_(involucrados))
    ).all():
        grados_por_colegio[id_colegio].add(id_grado)
    id_grado_por_numero = {leer_grado(g.nombre).valor: g.id_grado for g in db.exec(select(Grado)).all()}

    alumnos: dict[tuple[int, tuple[str, ...]], list[Optional[str]]] = defaultdict(list)
    for id_colegio, apellidos, nombres, dni in db.exec(
        select(Alumno.id_colegio, Alumno.apellidos, Alumno.nombres, Alumno.dni)
        .where(col(Alumno.id_colegio).in_(involucrados))
    ).all():
        alumnos[(id_colegio, clave_de_nombre(apellidos, nombres))].append(dni)

    dnis_existentes = set(db.exec(select(Alumno.dni).where(col(Alumno.dni).in_(set(dnis_de(nomina))))).all())
    return Contexto(colegios, grados_por_colegio, id_grado_por_numero, dnis_existentes, alumnos)


def _analizar(db: Session, datos: bytes) -> Analisis:
    nomina = leer_nomina(datos)
    return clasificar(nomina, _contexto(db, nomina))


def vista_previa(db: Session, datos: bytes) -> VistaPreviaImportacion:
    """Qué pasaría con cada fila. No escribe nada."""
    analisis = _analizar(db, datos)
    return VistaPreviaImportacion(
        listos=analisis.listos,
        con_advertencia=analisis.con_advertencia,
        no_importadas=analisis.no_importadas,
        huella=analisis.huella,
        observaciones=[
            ObservacionImportacion(hoja=o.hoja, fila=o.fila, tipo=o.tipo, motivo=o.motivo, detalle=o.detalle)
            for o in analisis.observaciones
        ],
    )


# ── Consulta ────────────────────────────────────────────────────────────────────────

def _resultado(importacion: ImportacionAlumnos) -> ResultadoImportacion:
    return ResultadoImportacion(
        id_importacion=importacion.id_importacion,
        fecha=importacion.fecha,
        nombre_archivo=importacion.nombre_archivo,
        importados=importacion.importados,
        no_importadas=importacion.no_importadas,
        con_advertencia=importacion.con_advertencia,
    )


def consultar(db: Session, id_importacion: uuid.UUID) -> ResultadoImportacion:
    """El resultado de una importación; 404 si no se aplicó."""
    importacion = db.get(ImportacionAlumnos, id_importacion)
    if importacion is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, MENSAJE_NO_ENCONTRADA, motivo="importacion_no_encontrada")
    return _resultado(importacion)


def resultado_previo(db: Session, id_importacion: uuid.UUID, actor: Usuario) -> Optional[ResultadoImportacion]:
    """El resultado guardado si ese id ya se usó; 409 si lo usó otro usuario."""
    importacion = db.get(ImportacionAlumnos, id_importacion)
    if importacion is None:
        return None
    if importacion.id_usuario != actor.id_usuario:
        raise ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_EXISTENTE, motivo="importacion_existente")
    return _resultado(importacion)


# ── Confirmación ────────────────────────────────────────────────────────────────────

def _registrar(
    db: Session, analisis: Analisis, id_importacion: uuid.UUID, nombre_archivo: str, actor: Usuario
) -> ImportacionAlumnos:
    """Escribe la importación, sus alumnos y su auditoría, sin hacer commit. La importación
    va primero: sus alumnos la referencian."""
    ahora = ahora_utc()
    importacion = ImportacionAlumnos(
        id_importacion=id_importacion, id_usuario=actor.id_usuario, fecha=ahora, nombre_archivo=nombre_archivo,
        huella=analisis.huella, importados=analisis.listos, no_importadas=analisis.no_importadas,
        con_advertencia=analisis.con_advertencia,
    )
    db.add(importacion)
    db.flush()
    hoy = hoy_lima()
    alumnos = [
        Alumno(
            nombres=a.nombres, apellidos=a.apellidos, dni=a.dni, id_colegio=a.id_colegio, id_grado=a.id_grado,
            id_importacion=id_importacion, fecha_registro=hoy, activo=True, creado_por=actor.id_usuario, creado_en=ahora,
        )
        for a in analisis.altas
    ]
    db.add_all(alumnos)
    db.flush()
    for alumno in alumnos:
        registrar_auditoria(
            db, tabla=TABLA_ALUMNO, id_registro=alumno.id_alumno, accion=IMPORTAR,
            cambios={CAMPO_ARCHIVO: (None, nombre_archivo)}, id_usuario=actor.id_usuario, fecha=ahora, id_actividad=None,
        )
    return importacion


def confirmar(
    db: Session, datos: bytes, id_importacion: uuid.UUID, huella: str, nombre_archivo: str, actor: Usuario
) -> tuple[ResultadoImportacion, bool]:
    """Vuelve a analizar el archivo y, si nada cambió, registra todo en una transacción.
    Devuelve (resultado, si se creó ahora)."""
    analisis = _analizar(db, datos)
    if analisis.listos == 0:
        raise ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, MENSAJE_SIN_ALUMNOS, motivo="sin_alumnos")
    if huella != analisis.huella:
        raise _desactualizada()
    try:
        importacion = _registrar(db, analisis, id_importacion, nombre_archivo, actor)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        mensaje = str(error.orig)
        if "uq_alumno_dni" in mensaje or "alumno.dni" in mensaje:
            raise _desactualizada() from error
        previo = resultado_previo(db, id_importacion, actor)
        if previo is None:
            raise
        return previo, False
    return _resultado(importacion), True
