"""Importación de la nómina de alumnos desde Excel (CU014)."""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

TipoObservacion = Literal["rechazo", "advertencia", "hoja_omitida"]
MotivoObservacion = Literal[
    "colegio_no_registrado",
    "dni_repetido",
    "alumno_ya_registrado",
    "grado_no_reconocido",
    "dni_no_valido",
    "nombre_sin_separar",
    "nombre_no_es_texto",
    "nombre_con_formula",
    "nombre_demasiado_largo",
    "nombre_no_valido",
    "subprograma_no_valido",
    "programa_no_ofrecido",
    "primer_grado_solo_alfabetizacion",
    "subprograma_sin_grado",
    "seccion_por_elegir",
    "hoja_omitida",
]
MotivoPendiente = Literal["sin_columna_subprograma", "subprograma_vacio", "subprograma_sin_grado", "seccion_por_elegir"]


class ObservacionImportacion(BaseModel):
    hoja: str
    # Número de fila de Excel; null en una hoja omitida.
    fila: Optional[int] = None
    tipo: TipoObservacion
    # El front decide por `motivo`; `detalle` es el texto para mostrar.
    motivo: MotivoObservacion
    detalle: str


class PendientesPorMotivo(BaseModel):
    """Cuántos alumnos importables quedan con la pertenencia pendiente, y por qué."""

    motivo: MotivoPendiente
    cantidad: int
    detalle: str


class VistaPreviaImportacion(BaseModel):
    listos: int
    con_advertencia: int
    no_importadas: int
    # SHA-256 del análisis: se devuelve al confirmar.
    huella: str
    # De los `listos`, los que quedan con la pertenencia pendiente (el resto, completa).
    pendientes: int
    pendientes_por_motivo: list[PendientesPorMotivo]
    observaciones: list[ObservacionImportacion]


class ResultadoImportacion(BaseModel):
    id_importacion: uuid.UUID
    fecha: datetime
    nombre_archivo: str
    importados: int
    no_importadas: int
    con_advertencia: int
