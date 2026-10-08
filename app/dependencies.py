"""Dependencias de autenticación y autorización.

Un token vale solo si su sesión (claim `jti`) existe en la BD, pertenece al usuario del
token (`sub`), sigue abierta y no venció. El usuario y su rol se leen siempre de la BD,
nunca del token.
"""
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlmodel import Session

from app.core.database import get_db
from app.core.errores import ErrorNegocio
from app.core.security import decode_access_token
from app.core.tiempo import ahora_utc
from app.models.organizacion import Rol, Usuario
from app.models.seguridad import Sesion
from app.services.sesiones import cerrar_sesiones_vencidas

# auto_error=False: sin cabecera Authorization se responde con el mismo formato que
# cualquier otra sesión inválida, en vez del {"detail": "Not authenticated"} de FastAPI.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

MOTIVO_SESION_INVALIDA = "sesion_invalida"
MOTIVO_SESION_EXPIRADA = "sesion_expirada"
MENSAJE_SESION_INVALIDA = "La sesión no es válida. Inicie sesión nuevamente."
MENSAJE_SESION_EXPIRADA = "La sesión ha expirado. Inicie sesión nuevamente."


def _no_autenticado(mensaje: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_401_UNAUTHORIZED, mensaje, motivo=motivo, headers={"WWW-Authenticate": "Bearer"}
    )


def sesion_invalida() -> ErrorNegocio:
    return _no_autenticado(MENSAJE_SESION_INVALIDA, MOTIVO_SESION_INVALIDA)


def verificar_firma(token: Optional[str]) -> dict:
    """Claims de un token con firma válida. Sin token o con firma inválida: 401."""
    if not token:
        raise sesion_invalida()
    try:
        return decode_access_token(token)
    except JWTError:
        raise sesion_invalida() from None


def ids_del_token(claims: dict) -> Optional[tuple[int, uuid.UUID]]:
    """(id_usuario, id_sesion) de los claims `sub` y `jti`, o None si faltan o no son
    válidos (p. ej. tokens emitidos antes de que existieran las sesiones)."""
    try:
        return int(claims["sub"]), uuid.UUID(str(claims["jti"]))
    except (KeyError, TypeError, ValueError):
        return None


def _cerrar_vencidas_aparte(db: Session, id_usuario: int) -> None:
    """Cierra las sesiones vencidas del usuario en una transacción propia.

    La petición va a terminar en 401 (y su sesión de BD, en rollback); el cierre debe
    quedar guardado igual, sin mezclarse con lo que haya hecho la petición.
    """
    with Session(db.get_bind()) as aparte:
        cerrar_sesiones_vencidas(aparte, id_usuario)
        aparte.commit()


@dataclass(frozen=True)
class SesionActual:
    usuario: Usuario
    sesion: Sesion


def get_sesion_actual(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> SesionActual:
    """Usuario autenticado y su sesión. FastAPI la resuelve una vez por petición aunque
    varias dependencias la usen."""
    ids = ids_del_token(verificar_firma(token))
    if ids is None:
        raise sesion_invalida()
    id_usuario, id_sesion = ids

    sesion = db.get(Sesion, id_sesion)
    if sesion is None or sesion.id_usuario != id_usuario or sesion.fin is not None:
        raise sesion_invalida()
    if sesion.expira <= ahora_utc():
        _cerrar_vencidas_aparte(db, id_usuario)
        raise _no_autenticado(MENSAJE_SESION_EXPIRADA, MOTIVO_SESION_EXPIRADA)

    usuario = db.get(Usuario, id_usuario)
    if usuario is None or not usuario.activo:
        raise sesion_invalida()
    return SesionActual(usuario=usuario, sesion=sesion)


def get_current_user(actual: SesionActual = Depends(get_sesion_actual)) -> Usuario:
    return actual.usuario


def get_id_sesion_actual(actual: SesionActual = Depends(get_sesion_actual)) -> uuid.UUID:
    """Id de la sesión de la petición (p. ej. para que /me/password no la cierre)."""
    return actual.sesion.id_sesion


def require_role(*roles_permitidos: str):
    def dependency(
        current_user: Usuario = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Usuario:
        rol = db.get(Rol, current_user.id_rol)
        if rol is None or rol.nombre not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta acción",
            )
        return current_user
    return dependency
