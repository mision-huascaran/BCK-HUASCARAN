"""Requests y responses de autenticación (CU002, CU003, CU007).

Los instantes (`inicio`, `expira`) viajan en UTC con sufijo Z.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.core.correo import CorreoNormalizado


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
