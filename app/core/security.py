from datetime import timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.tiempo import ahora_utc

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

CODIGO_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin O/0, I/1/L — se confunden al leer/tipear


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


# Hash de un valor que nadie conoce. Se verifica contra él cuando la cuenta no existe,
# para que la respuesta tarde lo mismo que con una cuenta real.
HASH_FICTICIO = hash_password("valor-usado-solo-para-igualar-el-tiempo-de-respuesta")


def create_access_token(data: dict[str, Any]) -> str:
    expire = ahora_utc() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
