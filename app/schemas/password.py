"""Requests y responses del cambio y la recuperación de contraseña (CU001, CU005, CU006).

Los schemas solo validan la forma de la petición y normalizan el correo. La coincidencia,
la política de CU006 y "distinta de la actual" se validan en el servicio
(`password_service.validar_contrasena_nueva`), porque deben evaluarse después del PIN o
de la Recovery Key y responder con el formato de `ErrorNegocio`.
"""
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _normalizar_correo(correo: str) -> str:
    return correo.strip().lower()


def _sin_espacios(valor: str) -> str:
    return valor.strip()


CorreoNormalizado = Annotated[str, AfterValidator(_normalizar_correo)]
Codigo = Annotated[str, AfterValidator(_sin_espacios)]


# ── Con sesión iniciada (/me/password/*) ────────────────────────────────────────────

class VerificarCodigoRequest(BaseModel):
    codigo: Codigo


class CambiarContrasenaRequest(BaseModel):
    codigo: Codigo
    contraseña_nueva: str
    confirmar_contraseña_nueva: str


# ── Flujo público (/password/*) ─────────────────────────────────────────────────────

class RecuperarContrasenaRequest(BaseModel):
    """Inicio del flujo público: solo el correo, sin sesión iniciada."""

    correo: CorreoNormalizado


class VerificarCodigoPublicoRequest(BaseModel):
    correo: CorreoNormalizado
    codigo: Codigo


class RestablecerContrasenaRequest(BaseModel):
    """Cierre del flujo público: el correo identifica al usuario, el código lo autoriza."""

    correo: CorreoNormalizado
    codigo: Codigo
    contraseña_nueva: str
    confirmar_contraseña_nueva: str


class RecuperarConLlaveRequest(BaseModel):
    """Recuperación del Supervisor original con una Recovery Key (CU001)."""

    correo: CorreoNormalizado
    # Una llave tiene 34 caracteres con guiones; el tope solo evita cuerpos absurdos.
    llave: str = Field(max_length=100)
    contraseña_nueva: str
    confirmar_contraseña_nueva: str


# ── Responses ───────────────────────────────────────────────────────────────────────

class MensajeResponse(BaseModel):
    mensaje: str


class CodigoValidoResponse(BaseModel):
    valido: bool


class RecuperarConLlaveResponse(MensajeResponse):
    llaves_restantes: int
