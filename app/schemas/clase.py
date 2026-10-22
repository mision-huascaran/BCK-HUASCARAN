"""Clases y su programación (CU013, CU022, base de CU024).

La clase es colegio × grado × sección; el subprograma es del alumno.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.comun import ItemCatalogo

TipoClase = Literal["habitual", "adicional"]


class ClaseItem(BaseModel):
    # null si la clase todavía no tiene fila (habitual sin registros ni traslados).
    id_clase: Optional[int] = None
    id_grado: int
    id_seccion: int
    fecha_original: date
    # La vigente.
    fecha: date
    tipo: TipoClase
    reprogramada: bool
    tiene_asistencias: bool
    id_periodo_academico: int
    # Para reprogramar (null si no tiene fila).
    version: Optional[int] = None


class ReprogramacionCrear(BaseModel):
    id_grado: int
    id_seccion: int
    fecha_original: date
    fecha_nueva: date
    motivo: str = Field(max_length=500)
    # La `version` de la clase que se vio (null si no tenía fila). Si se envía y cambió, 409.
    version: Optional[int] = None


class ReprogramacionItem(BaseModel):
    id: int
    fecha_anterior: date
    fecha_nueva: date
    motivo: str
    usuario: ItemCatalogo
    fecha: datetime
