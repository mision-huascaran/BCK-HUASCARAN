"""Requests y responses de autenticación (CU002, CU003, CU007, CU008).

Los instantes (`inicio`, `expira`, `fin`) viajan en UTC con sufijo Z.
"""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.core.correo import CorreoNormalizado
from app.schemas.actividad import ActividadItem

TipoCierreSesion = Literal[
    "Manual",
    "Automático por expiración",
    "Invalidada por restablecimiento de contraseña",
    "Invalidada por desactivación",
]


class LoginRequest(BaseModel):
    correo: CorreoNormalizado
    password: str


class SesionLogin(BaseModel):
    id: uuid.UUID
    inicio: datetime
    expira: datetime


class UsuarioLogin(BaseModel):
    id: int
    nombres: str
    apellidos: str
    rol: str
    es_supervisor_original: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    sesion: SesionLogin
    usuario: UsuarioLogin


class SesionMe(BaseModel):
    inicio: datetime
    expira: datetime


class MeResponse(BaseModel):
    """Respuesta de GET /me: perfil del usuario y la sesión con la que llamó."""

    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    correo: str
    id_docente: Optional[int] = None
    dni: Optional[str] = None
    nombres: str
    apellidos: str
    activo: bool
    es_supervisor_original: bool
    sesion: SesionMe


# ── Cierre diferido de una sesión anterior (CU008) ──────────────────────────────────

class CerrarSesionRequest(BaseModel):
    # Hora real (con zona) en que el usuario pulsó "Cerrar sesión" sin conexión.
    fin: AwareDatetime


class SesionCerrada(BaseModel):
    id: uuid.UUID
    inicio: datetime
    expira: datetime
    fin: datetime
    tipo_cierre: TipoCierreSesion


class CierreSesionResponse(BaseModel):
    sesion: SesionCerrada
    # La actividad que terminó con el cierre de la sesión ("Forzado por cierre de
    # sesión"); null si no había ninguna o si el usuario no es Docente.
    actividad: Optional[ActividadItem] = None
