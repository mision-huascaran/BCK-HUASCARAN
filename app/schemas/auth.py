from typing import Optional

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    correo: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    correo: str
    id_docente: Optional[int] = None
    dni: Optional[str] = None
    nombres: str
    apellidos: str
    activo: bool
