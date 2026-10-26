"""Inicio de sesión (CU002), primer ingreso (CU001, CU016) y cierre de sesión (CU003).

Bloqueo por correo: 5 fallos seguidos bloquean el correo 15 minutos, sobre la fila de
`control_acceso_correo` del correo (app/services/control_acceso.py). Cuentan como fallo:
correo inexistente, contraseña incorrecta y contraseña correcta de una cuenta desactivada.

Cuenta con contraseña temporal (`debe_cambiar_contrasena`): el login no abre sesión y
responde 403 (`cambio_contrasena_requerido` o, si la temporal venció,
`contrasena_temporal_expirada`); como las credenciales eran correctas, no suma fallo. La
sesión se abre en POST /password/primer-ingreso, al fijar la contraseña definitiva.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import status
from sqlmodel import Session, select

from app.core.correo import normalizar_correo
from app.core.errores import ErrorNegocio
from app.core.security import HASH_FICTICIO, verify_password
from app.core.tiempo import ahora_utc
from app.models.organizacion import Rol, Usuario
from app.models.seguridad import ControlAccesoCorreo, Sesion
from app.models.trazabilidad import Actividad
from app.schemas.auth import SesionLogin, TokenResponse, UsuarioLogin
from app.services.control_acceso import (
    MENSAJE_BLOQUEO,
    MOTIVO_BLOQUEO,
    bloquear_y_verificar,
    registrar_intento_exitoso,
    registrar_intento_fallido,
)
from app.services.password_service import (
    aplicar_contrasena_nueva,
    confirmar_cambio,
    validar_contrasena_nueva,
)
from app.services.sesiones import (
    cerrar_sesion_diferida,
    cerrar_sesion_por_logout,
    cerrar_sesiones_vencidas,
    crear_sesion_y_token,
)

MOTIVO_CREDENCIALES_INVALIDAS = "credenciales_invalidas"
MENSAJE_CREDENCIALES_INVALIDAS = "Correo o contraseña incorrectos."
MOTIVO_CUENTA_DESACTIVADA = "cuenta_desactivada"
MENSAJE_CUENTA_DESACTIVADA = (
    "Tu cuenta se encuentra desactivada. Comunícate con el Supervisor para solicitar su habilitación."
)
MOTIVO_CAMBIO_REQUERIDO = "cambio_contrasena_requerido"
MENSAJE_CAMBIO_REQUERIDO = "Debes establecer una nueva contraseña para continuar."
# Texto provisional: análisis puede ajustarlo.
MOTIVO_TEMPORAL_EXPIRADA = "contrasena_temporal_expirada"
MENSAJE_TEMPORAL_EXPIRADA = (
    "Tu contraseña temporal ha expirado. Usa '¿Olvidaste tu contraseña?' para generar una nueva."
)


@dataclass(frozen=True)
class InicioDeSesion:
    token: str
    sesion: Sesion
    usuario: Usuario
    rol: str


def _rechazar(
    db: Session, control: ControlAccesoCorreo, codigo: int, mensaje: str, motivo: str
) -> ErrorNegocio:
    """Registra el fallo y devuelve el error a lanzar.

    Si este fallo es el que alcanza el máximo, el correo ya quedó bloqueado y la
    respuesta de este mismo intento es la del bloqueo (CU002: el aviso de cuenta
    desactivada solo se muestra mientras no se haya alcanzado el bloqueo).
    """
    registrar_intento_fallido(db, control)
    db.commit()
    if control.bloqueado_hasta is not None:
        return ErrorNegocio(status.HTTP_429_TOO_MANY_REQUESTS, MENSAJE_BLOQUEO, motivo=MOTIVO_BLOQUEO)
    return ErrorNegocio(codigo, mensaje, motivo=motivo)


def _autenticar(
    db: Session, correo: str, password: str, bloquear_usuario: bool = False
) -> tuple[ControlAccesoCorreo, Optional[Usuario]]:
    """(control del correo, usuario si la contraseña es la suya; si no, None).

    La fila de control del correo queda bloqueada (FOR UPDATE) mientras se evalúa el
    intento: varios intentos simultáneos al mismo correo se evalúan de a uno y el
    contador no se puede saltar lanzándolos en paralelo. Sin cuenta se verifica igual
    contra un hash ficticio: misma demora que con una real.
    """
    control = bloquear_y_verificar(db, correo)
    consulta = select(Usuario).where(Usuario.correo == correo)
    if bloquear_usuario:
        consulta = consulta.with_for_update().execution_options(populate_existing=True)
    usuario = db.exec(consulta).first()
    hash_a_verificar = usuario.password_hash if usuario is not None else HASH_FICTICIO
    if not verify_password(password, hash_a_verificar) or usuario is None:
        return control, None
    return control, usuario


def _credenciales_invalidas(db: Session, control: ControlAccesoCorreo) -> ErrorNegocio:
    return _rechazar(
        db, control, status.HTTP_401_UNAUTHORIZED, MENSAJE_CREDENCIALES_INVALIDAS, MOTIVO_CREDENCIALES_INVALIDAS
    )


def temporal_vencida(usuario: Usuario) -> bool:
    return usuario.contrasena_temporal_expira is not None and usuario.contrasena_temporal_expira <= ahora_utc()


def _error_temporal_expirada() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_403_FORBIDDEN, MENSAJE_TEMPORAL_EXPIRADA, motivo=MOTIVO_TEMPORAL_EXPIRADA)


def _abrir_sesion(db: Session, usuario: Usuario) -> InicioDeSesion:
    """Crea la sesión y su token. No hace commit."""
    cerrar_sesiones_vencidas(db, usuario.id_usuario)
    token, sesion = crear_sesion_y_token(db, usuario)
    rol = db.get(Rol, usuario.id_rol)
    return InicioDeSesion(token=token, sesion=sesion, usuario=usuario, rol=rol.nombre if rol else "")


def iniciar_sesion(db: Session, correo: str, password: str) -> InicioDeSesion:
    """Valida las credenciales y abre una sesión nueva, todo en una transacción.

    Con la contraseña temporal pendiente de cambio no abre sesión: 403, sin sumar fallo.
    """
    control, usuario = _autenticar(db, normalizar_correo(correo), password)
    if usuario is None:
        raise _credenciales_invalidas(db, control)
    if not usuario.activo:
        raise _rechazar(
            db, control, status.HTTP_403_FORBIDDEN,
            MENSAJE_CUENTA_DESACTIVADA, MOTIVO_CUENTA_DESACTIVADA,
        )

    registrar_intento_exitoso(db, control)
    if usuario.debe_cambiar_contrasena:
        db.commit()
        if temporal_vencida(usuario):
            raise _error_temporal_expirada()
        raise ErrorNegocio(status.HTTP_403_FORBIDDEN, MENSAJE_CAMBIO_REQUERIDO, motivo=MOTIVO_CAMBIO_REQUERIDO)

    inicio = _abrir_sesion(db, usuario)
    db.commit()
    return inicio


def primer_ingreso(db: Session, correo: str, temporal: str, nueva: str, confirmacion: str) -> InicioDeSesion:
    """Cambio obligatorio de la contraseña temporal y apertura de la sesión (CU001, CU016).

    1. Con bloqueo vigente: 429, sin evaluar nada más.
    2. Cuenta inexistente, inactiva, sin cambio pendiente o temporal incorrecta: el mismo
       401 que el login (no revela cuentas) y suma un fallo.
    3. Temporal vencida: 403 `contrasena_temporal_expirada` (no suma fallo).
    4. Contraseña nueva inválida (CU006, confirmación, igual a la temporal): el error de
       validación, sin sumar fallo.
    5. Éxito, en una transacción: contraseña nueva, marca quitada y sesión de 8 horas.
    """
    control, usuario = _autenticar(db, normalizar_correo(correo), temporal, bloquear_usuario=True)
    if usuario is None or not usuario.activo or not usuario.debe_cambiar_contrasena:
        raise _credenciales_invalidas(db, control)

    registrar_intento_exitoso(db, control)
    try:
        if temporal_vencida(usuario):
            raise _error_temporal_expirada()
        validar_contrasena_nueva(nueva, confirmacion, usuario.password_hash)
    except ErrorNegocio:
        db.commit()
        raise

    aplicar_contrasena_nueva(db, usuario, nueva)
    inicio = _abrir_sesion(db, usuario)
    confirmar_cambio(db)
    return inicio


def a_token_response(inicio: InicioDeSesion) -> TokenResponse:
    """Respuesta de POST /login y de POST /password/primer-ingreso (la misma)."""
    return TokenResponse(
        access_token=inicio.token,
        sesion=SesionLogin(id=inicio.sesion.id_sesion, inicio=inicio.sesion.inicio, expira=inicio.sesion.expira),
        usuario=UsuarioLogin(
            id=inicio.usuario.id_usuario,
            nombres=inicio.usuario.nombres,
            apellidos=inicio.usuario.apellidos,
            rol=inicio.rol,
            es_supervisor_original=inicio.usuario.es_supervisor_original,
        ),
    )


def cerrar_sesion(db: Session, id_usuario: int, id_sesion: uuid.UUID) -> None:
    cerrar_sesion_por_logout(db, id_usuario, id_sesion)
    db.commit()


def cerrar_sesion_anterior(
    db: Session,
    id_usuario: int,
    id_sesion_actual: uuid.UUID,
    id_sesion: uuid.UUID,
    fin: Optional[datetime],
    fin_minimo: Optional[datetime] = None,
) -> tuple[Sesion, Optional[Actividad]]:
    """Cierre diferido de una sesión anterior (CU008), en una sola transacción."""
    sesion, actividad = cerrar_sesion_diferida(db, id_usuario, id_sesion_actual, id_sesion, fin, fin_minimo)
    db.commit()
    return sesion, actividad
