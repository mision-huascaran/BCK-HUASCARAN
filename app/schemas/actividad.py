"""Actividades de trabajo del Docente (CU009, CU010)."""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel


class ActividadIniciar(BaseModel):
    # Lo genera el cliente: reenviar el mismo id no duplica la actividad.
    id_actividad: uuid.UUID
    # Hora real de inicio (con zona). Por defecto, el instante en que llega al servidor.
    inicio: Optional[AwareDatetime] = None


class ActividadFinalizar(BaseModel):
    # Hora real de cierre (con zona), p. ej. si finalizó sin conexión. Por defecto, ahora.
    fin: Optional[AwareDatetime] = None


class ActividadItem(BaseModel):
    id: uuid.UUID
    inicio: datetime
    fin: Optional[datetime] = None
    tipo_cierre: Optional[str] = None
    estado: Literal["Activa", "Finalizada"]


class ActividadRespuesta(BaseModel):
    actividad: ActividadItem
    # Vencimiento de la sesión de la petición: ninguna actividad pasa de ese instante.
    sesion_expira: datetime


class ActividadActiva(BaseModel):
    id: uuid.UUID
    inicio: datetime
