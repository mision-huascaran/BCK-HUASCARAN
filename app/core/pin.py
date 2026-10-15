"""PIN de recuperación de contraseña (CU005, CU006).

El PIN tiene 6 dígitos: solo 10^6 combinaciones. Con bcrypt, un hash filtrado se rompe
por fuerza bruta en minutos, así que bcrypt no aporta nada aquí. Se guarda un HMAC-SHA256
con una clave que solo tiene el servidor: sin esa clave, el valor de la BD no sirve para
probar PIN fuera de línea. La protección en línea la dan la vigencia (15 minutos) y el
tope de 5 intentos.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from app.core.config import settings

DIGITOS_PIN = 6
VIGENCIA_PIN = timedelta(minutes=15)
MAX_INTENTOS_PIN = 5

# Clave propia del PIN, derivada de la clave del servidor con un contexto fijo. Así el
# HMAC del PIN y la firma de los JWT no comparten clave: un valor de uno nunca sirve
# como valor válido del otro. Cambiar el contexto (p. ej. a v2) invalida los PIN vigentes.
_CONTEXTO_CLAVE_PIN = b"sicedu/pin-recuperacion/v1"


def _clave_pin() -> bytes:
    return hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"), _CONTEXTO_CLAVE_PIN, hashlib.sha256
    ).digest()


def generar_pin() -> str:
    return f"{secrets.randbelow(10**DIGITOS_PIN):0{DIGITOS_PIN}d}"


def hmac_pin(id_usuario: int, pin: str) -> str:
    """HMAC-SHA256 en hexadecimal (64 caracteres).

    Incluye el id del usuario: el mismo PIN en dos cuentas produce valores distintos.
    """
    mensaje = f"{id_usuario}:{pin}".encode("utf-8")
    return hmac.new(_clave_pin(), mensaje, hashlib.sha256).hexdigest()


def pin_coincide(id_usuario: int, pin: str, hmac_guardado: str) -> bool:
    return hmac.compare_digest(hmac_pin(id_usuario, pin), hmac_guardado)
