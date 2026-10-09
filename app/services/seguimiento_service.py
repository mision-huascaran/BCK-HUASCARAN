"""Seguimiento de docentes por el Supervisor (CU020, CU021). Solo lectura.

"Última conexión" es el inicio de la sesión autenticada más reciente del docente (tabla
`sesion`), no una actividad.
"""
from datetime import date
from typing import Optional

from fastapi import status
from sqlalchemy import case, func
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.models.organizacion import DocenteColegioGrado, Rol, Usuario
from app.models.seguridad import Sesion
from app.schemas.actividad import ActividadResumen
from app.schemas.seguimiento import DocenteSeguimiento
from app.services.actividad import cerrar_vencidas_y_confirmar
from app.services.actividad_consulta import (
    FiltrosActividad,
    entre_fechas,
    exigir_rango_valido,
    listar_actividades,
)
from app.services.asignacion_service import colegios_vigentes_por_docente
from app.services.calendario import periodo_vigente
from app.services.sincronizacion import estado_docente_sql, pendientes_docente_sql
from app.services.usuario_service import DOCENTE


def _consulta_docentes():
    """Docentes con su última conexión, estado de sincronización y pendientes."""
    ultima = (
        select(Sesion.id_usuario, func.max(Sesion.inicio).label("inicio"))
        .group_by(Sesion.id_usuario)
        .subquery()
    )
    pendientes = pendientes_docente_sql()
    return (
        select(Usuario, ultima.c.inicio, estado_docente_sql(pendientes), pendientes)
        .join(Rol, Rol.id_rol == Usuario.id_rol)
        .outerjoin(ultima, ultima.c.id_usuario == Usuario.id_usuario)
        .where(Rol.nombre == DOCENTE, col(Usuario.id_docente).is_not(None))
    ), ultima.c.inicio, pendientes


def _filas_a_items(db: Session, filas) -> list[DocenteSeguimiento]:
    colegios = colegios_vigentes_por_docente(db, [u.id_docente for u, *_ in filas])
    return [
        DocenteSeguimiento(
            id_docente=usuario.id_docente,
            nombres=usuario.nombres,
            apellidos=usuario.apellidos,
            activo=usuario.activo,
            colegios=colegios.get(usuario.id_docente, []),
            ultima_conexion=ultima,
            sincronizacion=sincronizacion,
            registros_pendientes=pendientes,
        )
        for usuario, ultima, sincronizacion, pendientes in filas
    ]


def listar_docentes(
    db: Session,
    id_colegio: Optional[int],
    desde: Optional[date],
    hasta: Optional[date],
    sincronizacion: Optional[str],
    activo: bool,
    page: int,
) -> Paginado[DocenteSeguimiento]:
    """CU020. Primero los que tienen registros pendientes, luego por nombre. Un número
    fijo de consultas: total, página y colegios de la página (más el periodo vigente)."""
    exigir_rango_valido(desde, hasta)
    consulta, ultima_conexion, pendientes = _consulta_docentes()
    consulta = consulta.where(Usuario.activo == activo, *entre_fechas(ultima_conexion, desde, hasta))
    if id_colegio is not None:
        periodo = periodo_vigente(db)
        if periodo is None:
            return pagina([], 0, page)
        consulta = consulta.where(
            col(Usuario.id_docente).in_(
                select(DocenteColegioGrado.id_docente).where(
                    DocenteColegioGrado.id_colegio == id_colegio,
                    DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
                )
            )
        )
    if sincronizacion is not None:
        consulta = consulta.where(estado_docente_sql(pendientes) == sincronizacion)
    consulta = consulta.order_by(
        case((pendientes > 0, 0), else_=1), Usuario.nombres, Usuario.apellidos, Usuario.id_usuario
    )
    filas, total = paginar(db, consulta, page)
    return pagina(_filas_a_items(db, filas), total, page)


def _no_encontrado() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_404_NOT_FOUND, "El docente no existe.", motivo="docente_no_encontrado")


def _usuario_del_docente(db: Session, id_docente: int) -> Usuario:
    usuario = db.exec(
        select(Usuario)
        .join(Rol, Rol.id_rol == Usuario.id_rol)
        .where(Usuario.id_docente == id_docente, Rol.nombre == DOCENTE)
    ).first()
    if usuario is None:
        raise _no_encontrado()
    return usuario


def obtener_docente(db: Session, id_docente: int) -> DocenteSeguimiento:
    """Cabecera de CU021."""
    consulta, _, _ = _consulta_docentes()
    filas = db.exec(consulta.where(Usuario.id_docente == id_docente)).all()
    if not filas:
        raise _no_encontrado()
    return _filas_a_items(db, filas)[0]


def actividades_del_docente(
    db: Session, id_docente: int, filtros: FiltrosActividad, page: int
) -> Paginado[ActividadResumen]:
    """Historial de CU021, de la más reciente a la más antigua, con su productividad."""
    usuario = _usuario_del_docente(db, id_docente)
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    return listar_actividades(db, id_docente, filtros, page)
