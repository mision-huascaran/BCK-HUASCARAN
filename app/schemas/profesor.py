from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProfesorCreate(BaseModel):
    nombres: str
    apellidos: str
    correo: str
    activo: bool = True


class ProfesorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    correo: str
    id_docente: Optional[int]
    nombres: str
    apellidos: str
    activo: bool
    contraseña_temporal: Optional[str] = None


class ProfesorListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_docente: int
    correo: str
    nombres: str
    apellidos: str
    activo: bool
