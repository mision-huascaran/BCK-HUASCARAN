"""Actividad de trabajo del Docente (CU009, CU010).

Toda escritura de un Docente sobre datos de alumnos requiere una actividad activa; el
Supervisor no la necesita. La actividad se vincula a la auditoría de cada cambio.

Una sesión puede tener varias actividades seguidas, pero el docente solo una activa a la
vez (índice único parcial en la BD), y ninguna pasa de la expiración de su sesión.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadItem
from app.services.alcance import alcance_docente
from app.services.sesiones import CIERRE_ACTIVIDAD_MANUAL, cerrar_sesiones_vencidas
from app.services.usuario_service import DOCENTE, nombre_rol

# Tolerancia para el reloj del cliente: una hora enviada hasta 2 minutos en el futuro
# se acepta (relojes que se adelantan un poco); más allá es un dato inválido.
MARGEN_RELOJ = timedelta(minutes=2)

EN_CURSO = "en_curso"
FINALIZADA = "finalizada"


def a_item(actividad: Actividad) -> ActividadItem:
    return ActividadItem(
        id=actividad.id_actividad,
        inicio=actividad.inicio,
        fin=actividad.fin,
        tipo_cierre=actividad.tipo_cierre,
        estado=EN_CURSO if actividad.fin is None else FINALIZADA,
    )


def cerrar_vencidas_y_confirmar(db: Session, id_usuario: Optional[int] = None) -> None:
    """Cierra las sesiones vencidas (del usuario, o de todos sin `id_usuario`) y sus
    actividades, y lo confirma de inmediato: queda guardado aunque la petición termine
    en error."""
    if cerrar_sesiones_vencidas(db, id_usuario):
        db.commit()


def actividad_activa(db: Session, id_docente: Optional[int]) -> Optional[Actividad]:
    """La actividad activa (`fin IS NULL`) del docente cuya sesión siga abierta y vigente."""
    if id_docente is None:
        return None
    return db.exec(
        select(Actividad)
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(
            Actividad.id_docente == id_docente,
            col(Actividad.fin).is_(None),
            col(Sesion.fin).is_(None),
            Sesion.expira > ahora_utc(),
        )
    ).first()


def requerir_actividad_activa(db: Session, usuario: Usuario) -> Optional[uuid.UUID]:
    """`id_actividad` con el que se auditan los cambios de este usuario.

    - Supervisor (o cualquier rol que no sea Docente): None, no necesita actividad.
    - Docente: su actividad activa cuya sesión siga abierta y vigente. Antes se cierran
      sus sesiones vencidas (y con ellas sus actividades), para que una actividad
      "activa" de una sesión ya vencida no habilite escrituras. Ese cierre se confirma
      aunque la petición termine en 409.
      Sin actividad válida: 409 `actividad_requerida`.
    """
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return None
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    actividad = actividad_activa(db, usuario.id_docente)
    if actividad is None:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "Debe iniciar una actividad para realizar cambios.",
            motivo="actividad_requerida",
        )
    return actividad.id_actividad


# ── Iniciar ─────────────────────────────────────────────────────────────────────────

def _registrada(db: Session, id_actividad: uuid.UUID, id_docente: Optional[int]) -> Optional[Actividad]:
    """Idempotencia: la actividad ya registrada con ese id si es del docente; 409 si el
    id lo usa otro docente; None si el id está libre."""
    actividad = db.get(Actividad, id_actividad, populate_existing=True)
    if actividad is None:
        return None
    if actividad.id_docente != id_docente:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "El identificador de la actividad ya está en uso.",
            motivo="actividad_id_en_uso",
        )
    return actividad


def _ya_hay_una_activa() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_409_CONFLICT,
        "Debe finalizar la actividad actual antes de iniciar una nueva.",
        motivo="actividad_activa_existente",
    )


def iniciar_actividad(
    db: Session, usuario: Usuario, sesion: Sesion, id_actividad: uuid.UUID, inicio: Optional[datetime]
) -> tuple[Actividad, bool]:
    """Inicia una actividad en la sesión de la petición (CU009).

    Devuelve (actividad, creada). `creada` es False cuando el id ya estaba registrado
    para este docente: se devuelve esa actividad sin cambios, esté activa o finalizada.
    """
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    registrada = _registrada(db, id_actividad, usuario.id_docente)
    if registrada is not None:
        return registrada, False
    activa = actividad_activa(db, usuario.id_docente)
    if activa is not None:
        if activa.id_actividad == id_actividad:
            # Un reintento simultáneo con el mismo id la registró después de la consulta anterior.
            return activa, False
        raise _ya_hay_una_activa()
    if usuario.id_docente is None or not alcance_docente(db, usuario.id_docente):
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "No puede iniciar una actividad porque no tiene asignaciones activas.",
            motivo="sin_asignaciones",
        )

    ahora = ahora_utc()
    inicio = ahora if inicio is None else inicio.astimezone(timezone.utc)
    if not sesion.inicio <= inicio <= ahora + MARGEN_RELOJ:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La hora de inicio debe estar entre el inicio de la sesión y la hora actual.",
            motivo="inicio_invalido",
        )

    actividad = Actividad(
        id_actividad=id_actividad,
        id_sesion=sesion.id_sesion,
        id_docente=usuario.id_docente,
        inicio=inicio,
        sincronizado_en=ahora,
    )
    db.add(actividad)
    try:
        db.commit()
    except IntegrityError as error:
        # Dos inicios simultáneos que pasaron las validaciones a la vez: el mismo id
        # (reintento del cliente) o el índice de una sola actividad activa por docente.
        db.rollback()
        registrada = _registrada(db, id_actividad, usuario.id_docente)
        if registrada is not None:
            return registrada, False
        raise _ya_hay_una_activa() from error
    db.refresh(actividad)
    return actividad, True


# ── Finalizar ───────────────────────────────────────────────────────────────────────

def finalizar_actividad(
    db: Session, usuario: Usuario, id_actividad: uuid.UUID, fin: Optional[datetime]
) -> Actividad:
    """Finaliza una actividad del docente (CU009). No cierra la sesión.

    Si ya estaba finalizada (por el docente o automáticamente), la devuelve sin cambios.
    """
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    # FOR UPDATE: un logout o una expiración simultáneos esperan a este cierre.
    actividad = db.exec(
        select(Actividad)
        .where(Actividad.id_actividad == id_actividad)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if actividad is None or actividad.id_docente != usuario.id_docente:
        raise ErrorNegocio(
            status.HTTP_404_NOT_FOUND, "La actividad no existe.", motivo="actividad_no_encontrada"
        )
    if actividad.fin is not None:
        return actividad

    ahora = ahora_utc()
    expira = db.exec(select(Sesion.expira).where(Sesion.id_sesion == actividad.id_sesion)).one()
    fin = min(ahora, expira) if fin is None else fin.astimezone(timezone.utc)
    if not actividad.inicio <= fin <= min(expira, ahora + MARGEN_RELOJ):
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La hora de fin debe estar entre el inicio de la actividad y la hora actual, "
            "sin pasar la expiración de la sesión.",
            motivo="fin_invalido",
        )
    actividad.fin = fin
    actividad.tipo_cierre = CIERRE_ACTIVIDAD_MANUAL
    actividad.sincronizado_en = ahora
    db.add(actividad)
    db.commit()
    db.refresh(actividad)
    return actividad
