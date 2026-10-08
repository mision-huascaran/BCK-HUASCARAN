"""Seguimiento de docentes por el Supervisor (CU020, CU021)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.comun import ItemCatalogo
from app.services.sincronizacion import EstadoSincronizacionDocente


class DocenteSeguimiento(BaseModel):
    """Fila de CU020 y cabecera de CU021."""

    id_docente: int
    nombres: str
    apellidos: str
    activo: bool
    # Colegios de sus asignaciones del periodo vigente, sin repetir; vacía si no tiene.
    colegios: list[ItemCatalogo]
    # Inicio de su sesión autenticada más reciente; null si nunca entró.
    ultima_conexion: Optional[datetime] = None
    sincronizacion: EstadoSincronizacionDocente
    registros_pendientes: int
