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


class ProfesorUpdate(BaseModel):
    """Actualiza los datos del profesor. Se aplican a `usuario` y a su ficha `docente`."""

    nombres: Optional[str] = None
    apellidos: Optional[str] = None
    correo: Optional[str] = None
