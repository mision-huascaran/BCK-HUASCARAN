"""Actividad de trabajo del Docente (CU009, CU010).

Toda escritura de un Docente sobre datos de alumnos requiere una actividad activa; el
Supervisor no la necesita. La actividad se vincula a la auditoría de cada cambio.
Iniciar y finalizar actividades llega en la Tanda 6.
"""
import uuid
from typing import Optional

from fastapi import status
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.services.sesiones import cerrar_sesiones_vencidas
from app.services.usuario_service import DOCENTE, nombre_rol


def requerir_actividad_activa(db: Session, usuario: Usuario) -> Optional[uuid.UUID]:
    """`id_actividad` con el que se auditan los cambios de este usuario.

    - Supervisor (o cualquier rol que no sea Docente): None, no necesita actividad.
    - Docente: su actividad activa (`fin IS NULL`) cuya sesión siga abierta y vigente.
      Antes se cierran sus sesiones vencidas (y con ellas sus actividades), para que una
      actividad "activa" de una sesión ya vencida no habilite escrituras. Ese cierre se
      confirma aunque la petición termine en 409.
      Sin actividad válida: 409 `actividad_requerida`.
    """
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return None
    if cerrar_sesiones_vencidas(db, usuario.id_usuario):
        db.commit()
    id_actividad = db.exec(
        select(Actividad.id_actividad)
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(
            Actividad.id_docente == usuario.id_docente,
            col(Actividad.fin).is_(None),
            col(Sesion.fin).is_(None),
            Sesion.expira > ahora_utc(),
        )
    ).first()
    if id_actividad is None:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "Debe iniciar una actividad para realizar cambios.",
            motivo="actividad_requerida",
        )
    return id_actividad
