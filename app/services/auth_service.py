"""Inicio de sesión (CU002) y cierre de sesión (CU003).

Bloqueo por correo: 5 fallos seguidos bloquean el correo 15 minutos, sobre la misma
fila de `control_acceso_correo` y con el mismo mecanismo que la Recovery Key
(app/services/control_acceso.py). Cuentan como fallo: correo inexistente, contraseña
incorrecta y contraseña correcta de una cuenta desactivada.
"""
import uuid
from dataclasses import dataclass

from fastapi import status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.correo import normalizar_correo
from app.core.errores import ErrorNegocio
from app.core.security import HASH_FICTICIO, verify_password
from app.models.organizacion import Rol, Usuario
from app.models.seguridad import ControlAccesoCorreo, Sesion
from app.services.control_acceso import (
    MENSAJE_BLOQUEO,
    MOTIVO_BLOQUEO,
    bloquear_y_verificar,
    registrar_intento_exitoso,
    registrar_intento_fallido,
)
from app.services.sesiones import (
    cerrar_sesion_por_logout,
    cerrar_sesiones_vencidas,
    crear_sesion_y_token,
)

MOTIVO_CREDENCIALES_INVALIDAS = "credenciales_invalidas"
MENSAJE_CREDENCIALES_INVALIDAS = "Correo o contraseña incorrectos."
MOTIVO_CUENTA_DESACTIVADA = "cuenta_desactivada"
MENSAJE_CUENTA_DESACTIVADA = (
    "Su cuenta se encuentra desactivada. Comuníquese con el Supervisor para solicitar su habilitación."
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


def iniciar_sesion(db: Session, correo: str, password: str) -> InicioDeSesion:
    """Valida las credenciales y abre una sesión nueva, todo en una transacción.

    La fila de control del correo queda bloqueada (FOR UPDATE) mientras se evalúa el
    intento: varios logins simultáneos al mismo correo se evalúan de a uno y el contador
    no se puede saltar lanzándolos en paralelo.
    """
    correo = normalizar_correo(correo)
    control = bloquear_y_verificar(db, correo)

    usuario = db.exec(select(Usuario).where(func.lower(Usuario.correo) == correo)).first()
    # Sin cuenta se verifica igual contra un hash ficticio: misma demora que con una real.
    hash_a_verificar = usuario.password_hash if usuario is not None else HASH_FICTICIO
    if not verify_password(password, hash_a_verificar) or usuario is None:
        raise _rechazar(
            db, control, status.HTTP_401_UNAUTHORIZED,
            MENSAJE_CREDENCIALES_INVALIDAS, MOTIVO_CREDENCIALES_INVALIDAS,
        )
    if not usuario.activo:
        raise _rechazar(
            db, control, status.HTTP_403_FORBIDDEN,
            MENSAJE_CUENTA_DESACTIVADA, MOTIVO_CUENTA_DESACTIVADA,
        )

    registrar_intento_exitoso(db, control)
    cerrar_sesiones_vencidas(db, usuario.id_usuario)
    token, sesion = crear_sesion_y_token(db, usuario)
    db.commit()

    rol = db.get(Rol, usuario.id_rol)
    return InicioDeSesion(token=token, sesion=sesion, usuario=usuario, rol=rol.nombre if rol else "")


def cerrar_sesion(db: Session, id_usuario: int, id_sesion: uuid.UUID) -> None:
    cerrar_sesion_por_logout(db, id_usuario, id_sesion)
    db.commit()
