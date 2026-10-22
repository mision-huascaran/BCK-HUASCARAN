"""Sincronización de operaciones hechas sin conexión (CU017-CU021).

Cada escritura de los módulos llega como un ítem con su `id_operacion` (lo genera el
cliente y es el mismo en cada reintento) y se responde con un `ResultadoOperacion`.
"""
import uuid
from typing import Any, Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field

ModuloSincronizacion = Literal["asistencias", "lectura", "rubrica", "registro_vuelo", "alumnos", "actividad"]
EstadoResultado = Literal["aceptada", "rechazada"]
EstadoConsultado = Literal["aceptada", "rechazada", "descartada", "desconocida"]

MAX_PENDIENTES_POR_LOTE = 500


class ItemOperacion(BaseModel):
    """Campos comunes de cada ítem de un lote de escritura."""

    id_operacion: uuid.UUID
    # La versión del registro que veía el usuario; null si es nuevo.
    version: Optional[int] = None
    # Instante real de la operación; obligatorio si viene de la cola offline. Por
    # defecto, el instante en que llega.
    realizado_en: Optional[AwareDatetime] = None
    # Actividad en que se hizo; por defecto, la actividad activa del Docente.
    id_actividad: Optional[uuid.UUID] = None


class ResultadoOperacion(BaseModel):
    id_operacion: uuid.UUID
    estado: EstadoResultado
    motivo: Optional[str] = None
    detail: Optional[str] = None
    # El registro tal como quedó (o el vigente, si ganó uno más reciente).
    registro: Optional[dict[str, Any]] = None


class PendienteInformado(BaseModel):
    id_operacion: uuid.UUID
    id_dispositivo: str = Field(min_length=1, max_length=200)
    id_actividad: Optional[uuid.UUID] = None
    modulo: ModuloSincronizacion
    realizado_en: AwareDatetime


class PendientesRegistrados(BaseModel):
    recibidos: int
    # Nuevos avisos guardados (los repetidos y los ya procesados se ignoran).
    registrados: int


class EstadoOperacion(BaseModel):
    estado: EstadoConsultado
    motivo: Optional[str] = None
    respuesta: Optional[dict[str, Any]] = None
