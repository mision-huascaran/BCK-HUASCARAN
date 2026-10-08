from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

CODIGO_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin O/0, I/1/L — se confunden al leer/tipear


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


# Hash de un valor que nadie conoce. Se verifica contra él cuando la cuenta no existe,
# para que la respuesta tarde lo mismo que con una cuenta real.
HASH_FICTICIO = hash_password("valor-usado-solo-para-igualar-el-tiempo-de-respuesta")


def create_access_token(claims: dict[str, Any]) -> str:
    """Firma los claims tal como llegan. No crea sesión ni pone vencimiento.

    Un token sin su fila en `sesion` no sirve para nada: para emitir uno válido se usa
    `app.services.sesiones.crear_sesion_y_token`, que pone `sub`, `jti`, `iat` y `exp`.
    """
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verifica la firma y devuelve los claims. Lanza JWTError si la firma no es válida.

    No rechaza el token por `exp`: la fuente de verdad del vencimiento es `sesion.expira`
    en la BD. Si la librería lo rechazara antes, la sesión vencida nunca se cerraría
    (ver app/dependencies.py).
    """
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_exp": False},
    )
