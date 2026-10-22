"""Requests y responses de autenticación (CU002, CU003, CU007, CU008).

Los instantes (`inicio`, `expira`, `fin`) viajan en UTC con sufijo Z.
"""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

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
    """`fin` o `fin_minimo`, nunca los dos."""

    # Hora real (con zona) en que el usuario pulsó "Cerrar sesión" sin conexión.
    fin: Optional[AwareDatetime] = None
    # Sin continuidad del reloj: la última hora verificada (latido). El cierre queda en
    # max(fin_minimo, última petición recibida), sin pasar del vencimiento, y no verificado.
    fin_minimo: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def _uno_de_los_dos(self) -> "CerrarSesionRequest":
        if (self.fin is None) == (self.fin_minimo is None):
            raise ValueError("Indica la hora de cierre (fin) o la mínima (fin_minimo), pero no ambas.")
        return self


class SesionCerrada(BaseModel):
    id: uuid.UUID
    inicio: datetime
    expira: datetime
    fin: datetime
    tipo_cierre: TipoCierreSesion
    # False si `fin` se estimó a partir de `fin_minimo`.
    fin_verificado: bool


class CierreSesionResponse(BaseModel):
    sesion: SesionCerrada
    # La actividad que terminó con el cierre de la sesión ("Forzado por cierre de
    # sesión"); null si no había ninguna o si el usuario no es Docente.
    actividad: Optional[ActividadItem] = None
