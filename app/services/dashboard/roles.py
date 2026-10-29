"""Quién consulta cada bloque del Dashboard. El Docente no tiene Dashboard (403).

El Directivo solo ve agregados (CU035): los bloques con nombres de alumnos o listados
nominales se declaran con `SoloSupervisor`.
"""
from typing import Annotated

from fastapi import Depends

from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.services.usuario_service import DIRECTIVO, SUPERVISOR

SoloSupervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]
SupervisorODirectivo = Annotated[Usuario, Depends(require_role(SUPERVISOR, DIRECTIVO))]
