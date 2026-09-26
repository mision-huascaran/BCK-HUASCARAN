from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ColegioCreate(BaseModel):
    nombre: str
    zona: Optional[str] = None


class ColegioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_colegio: int
    nombre: str
    zona: Optional[str]
    creado_por: int
    creado_en: date
    modificado_por: Optional[int]
    modificado_en: Optional[date]


class ColegioUpdate(BaseModel):
    nombre: Optional[str] = None
    zona: Optional[str] = None
