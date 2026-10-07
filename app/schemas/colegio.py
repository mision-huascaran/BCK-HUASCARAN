from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ColegioCreate(BaseModel):
    nombre: str
    departamento: str
    provincia: Optional[str] = None
    # Obligatorio para colegios nuevos; en la BD es nullable por los colegios anteriores.
    distrito: str
    nivel_educativo: str = "Primaria"
    seccion: str = "Única"
    activo: bool = True


class ColegioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_colegio: int
    nombre: str
    nivel_educativo: str
    departamento: str
    provincia: Optional[str] = None
    distrito: Optional[str] = None
    seccion: str
    activo: bool
    creado_por: int
    creado_en: datetime
    modificado_por: Optional[int] = None
    modificado_en: Optional[datetime] = None


class ColegioUpdate(BaseModel):
    nombre: Optional[str] = None
    nivel_educativo: Optional[str] = None
    departamento: Optional[str] = None
    provincia: Optional[str] = None
    distrito: Optional[str] = None
    seccion: Optional[str] = None
    activo: Optional[bool] = None
