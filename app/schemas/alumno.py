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
    nombres: str
    apellidos: str
    id_colegio: int
    id_grado: int
    id_programa_actual: int
    fecha_registro: date
    activo: bool
    creado_por: int
    creado_en: date
    modificado_por: Optional[int]
    modificado_en: Optional[date]
