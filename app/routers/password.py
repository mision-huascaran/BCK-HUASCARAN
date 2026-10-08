"""Cambio y recuperación de contraseña (CU001, CU005, CU006).

Los errores salen con el formato de `ErrorNegocio`: {"detail", "motivo"?, ...}.
"""
import uuid
from functools import partial
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import get_current_user, get_id_sesion_actual
from app.models.organizacion import Usuario
from app.schemas.password import (
    CambiarContrasenaRequest,
    CodigoValidoResponse,
    MensajeResponse,
    RecuperarConLlaveRequest,
    RecuperarConLlaveResponse,
    RecuperarContrasenaRequest,
    RestablecerContrasenaRequest,
    VerificarCodigoPublicoRequest,
    VerificarCodigoRequest,
)
from app.services import password_service, recovery_key_service

router = APIRouter(tags=["password"])

Db = Annotated[Session, Depends(get_db)]
UsuarioActual = Annotated[Usuario, Depends(get_current_user)]


def _fabrica_sesion(db: Session):
    """Sesiones nuevas sobre la misma conexión configurada que la de la petición, para
    las tareas en segundo plano (corren cuando la sesión de la petición ya se cerró)."""
    return partial(Session, db.get_bind())


# ── Con sesión iniciada ─────────────────────────────────────────────────────────────

@router.post("/me/password/codigo", response_model=MensajeResponse)
def solicitar_codigo_endpoint(db: Db, usuario_actual: UsuarioActual):
    password_service.solicitar_pin_con_sesion(db, usuario_actual)
    return MensajeResponse(mensaje=password_service.MENSAJE_PIN_ENVIADO_CON_SESION)


@router.post("/me/password/verificar-codigo", response_model=CodigoValidoResponse)
def verificar_codigo_endpoint(data: VerificarCodigoRequest, db: Db, usuario_actual: UsuarioActual):
    password_service.verificar_pin_con_sesion(db, usuario_actual, data.codigo)
    return CodigoValidoResponse(valido=True)


@router.post("/me/password", response_model=MensajeResponse)
def cambiar_contrasena_endpoint(
    data: CambiarContrasenaRequest,
    background_tasks: BackgroundTasks,
    db: Db,
    usuario_actual: UsuarioActual,
    id_sesion_actual: Annotated[Optional[uuid.UUID], Depends(get_id_sesion_actual)],
):
    usuario = password_service.cambiar_contrasena(
        db,
        usuario_actual,
        data.codigo,
        data.contraseña_nueva,
        data.confirmar_contraseña_nueva,
        id_sesion_actual=id_sesion_actual,
    )
    background_tasks.add_task(
        password_service.enviar_confirmacion_cambio, usuario.correo, usuario.nombres
    )
    return MensajeResponse(mensaje=password_service.MENSAJE_CONTRASENA_ACTUALIZADA)


# ── Flujo público, sin sesión ───────────────────────────────────────────────────────

@router.post("/password/recuperar", response_model=MensajeResponse)
def recuperar_contrasena_endpoint(
    data: RecuperarContrasenaRequest, background_tasks: BackgroundTasks, db: Db
):
    """Envía un PIN al correo indicado.

    Responde 200 con el mismo mensaje en todos los casos (correo registrado, inexistente,
    cuenta desactivada o límite superado), y el correo sale en segundo plano para que el
    tiempo de respuesta tampoco delate si la cuenta existe.
    """
    envio = password_service.solicitar_pin_publico(db, data.correo)
    if envio is not None:
        background_tasks.add_task(password_service.enviar_pin, _fabrica_sesion(db), envio)
    return MensajeResponse(mensaje=password_service.MENSAJE_PIN_ENVIADO)


@router.post("/password/verificar-codigo", response_model=CodigoValidoResponse)
def verificar_codigo_publico_endpoint(data: VerificarCodigoPublicoRequest, db: Db):
    """Valida el PIN sin consumirlo (para avanzar a la pantalla de la contraseña nueva)."""
    password_service.verificar_pin_publico(db, data.correo, data.codigo)
    return CodigoValidoResponse(valido=True)


@router.post("/password/restablecer", response_model=MensajeResponse)
def restablecer_contrasena_endpoint(
    data: RestablecerContrasenaRequest, background_tasks: BackgroundTasks, db: Db
):
    """Cambia la contraseña con el PIN recibido y cierra todas las sesiones abiertas."""
    usuario = password_service.restablecer_contrasena(
        db, data.correo, data.codigo, data.contraseña_nueva, data.confirmar_contraseña_nueva
    )
    background_tasks.add_task(
        password_service.enviar_confirmacion_cambio, usuario.correo, usuario.nombres
    )
    return MensajeResponse(mensaje=password_service.MENSAJE_CONTRASENA_ACTUALIZADA)


@router.post("/password/recuperar-con-llave", response_model=RecuperarConLlaveResponse)
def recuperar_con_llave_endpoint(
    data: RecuperarConLlaveRequest, background_tasks: BackgroundTasks, db: Db
):
    """Recuperación del Supervisor original con una Recovery Key de un solo uso (CU001)."""
    usuario, restantes = recovery_key_service.recuperar_con_llave(
        db, data.correo, data.llave, data.contraseña_nueva, data.confirmar_contraseña_nueva
    )
    background_tasks.add_task(
        password_service.enviar_confirmacion_cambio, usuario.correo, usuario.nombres
    )
    return RecuperarConLlaveResponse(
        mensaje=password_service.MENSAJE_CONTRASENA_ACTUALIZADA, llaves_restantes=restantes
    )
