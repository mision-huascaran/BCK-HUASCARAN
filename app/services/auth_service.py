from sqlmodel import Session, select

from app.core.security import HASH_FICTICIO, verify_password
from app.models.organizacion import Usuario


class CredencialesInvalidas(Exception):
    """Correo no existe o la contraseña no coincide."""


class CuentaInactiva(Exception):
    """La contraseña es correcta pero la cuenta está desactivada."""


def authenticate_user(db: Session, correo: str, password: str) -> Usuario:
    usuario = db.exec(select(Usuario).where(Usuario.correo == correo)).first()

    if usuario is None:
        verify_password(password, HASH_FICTICIO)  # tiempo uniforme, aunque el resultado se descarta
        raise CredencialesInvalidas()

    if not verify_password(password, usuario.password_hash):
        raise CredencialesInvalidas()

    if not usuario.activo:
        raise CuentaInactiva()

    return usuario
