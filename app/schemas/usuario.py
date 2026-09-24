from typing import Optional

from pydantic import BaseModel, ConfigDict


class UsuarioCreate(BaseModel):
    nombres: str
    apellidos: str
    correo: str
    id_rol: int
    activo: bool = True


class UsuarioCreado(BaseModel):
    """Respuesta de POST /usuarios.

    `contraseña_temporal` solo viaja cuando el correo no se pudo enviar; si se envio,
    llega en null y la credencial queda unicamente en el buzon del usuario.
    """

    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    correo: str
    id_docente: Optional[int]
    nombres: str
    apellidos: str
    activo: bool
    contraseña_temporal: Optional[str] = None
    correo_enviado: bool = True


class UsuarioListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    rol: str
    correo: str
    id_docente: Optional[int]
    nombres: str
    apellidos: str
    activo: bool


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    id_rol: int
    correo: str
    id_docente: Optional[int]
    nombres: str
    apellidos: str
    activo: bool


class UsuarioUpdate(BaseModel):
    """Edicion de una cuenta. Parcial: solo los campos presentes se modifican.

    No incluye `id_rol`: cambiar el rol de una cuenta ya creada mueve la cuenta de
    parcela y afecta a las reglas de ultima cuenta activa, asi que se deja fuera.
    """

    nombres: Optional[str] = None
    apellidos: Optional[str] = None
    correo: Optional[str] = None
