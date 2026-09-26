from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AsignacionCreate(BaseModel):
    id_docente: int
    id_colegio: int
    id_grado: int
    id_periodo_academico: int


class AsignacionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    id_docente: int
    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    creado_por: int
    creado_en: date
    modificado_por: Optional[int]
    modificado_en: Optional[date]


class AsignacionListItem(BaseModel):
    """Asignacion con los nombres resueltos, para pintarla sin cruzar catalogos."""

    id: int
    id_docente: int
    docente: str
    id_colegio: int
    colegio: str
    id_grado: int
    grado: str
    id_periodo_academico: int
    periodo: str
    vigente: bool
