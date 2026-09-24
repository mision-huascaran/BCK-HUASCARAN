from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AlumnoCreate(BaseModel):
    nombres: str
    apellidos: str
    id_colegio: int
    id_grado: int
    id_programa_actual: int
    activo: bool = True


class AlumnoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_alumno: int
    # Opcionales porque la vista de Directivo los devuelve en null: su acceso a datos
    # identificables de alumnos esta fuera del alcance acordado.
    nombres: Optional[str]
    apellidos: Optional[str]
    id_colegio: int
    id_grado: int
    id_programa_actual: int
    fecha_registro: date
    activo: bool
    creado_por: int
    creado_en: date
    modificado_por: Optional[int]
    modificado_en: Optional[date]


class AlumnoUpdate(BaseModel):
    """Actualizacion parcial: solo los campos presentes en el body se modifican.

    Se distingue 'no enviado' de 'enviado como null' con exclude_unset en el servicio.
    """

    nombres: Optional[str] = None
    apellidos: Optional[str] = None
    id_colegio: Optional[int] = None
    id_grado: Optional[int] = None
    id_programa_actual: Optional[int] = None
    activo: Optional[bool] = None


class AlumnoPagina(BaseModel):
    """Envoltorio de paginacion: `total` es el total que cumple el filtro, no el de la pagina."""

    total: int
    limit: int
    offset: int
    items: list[AlumnoResponse]
