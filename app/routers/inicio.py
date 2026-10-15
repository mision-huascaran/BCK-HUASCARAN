"""Pantalla de Inicio de cada rol (CU010, CU011, CU012). Cada una solo para su rol."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import SesionActual, get_sesion_actual, require_role
from app.models.organizacion import Usuario
from app.schemas.inicio import InicioDirectivo, InicioDocente, InicioSupervisor
from app.services import inicio_service
from app.services.usuario_service import DIRECTIVO, DOCENTE, SUPERVISOR

router = APIRouter(tags=["inicio"])

Db = Annotated[Session, Depends(get_db)]


@router.get("/inicio/docente", response_model=InicioDocente)
def inicio_docente(
    db: Db,
    docente: Annotated[Usuario, Depends(require_role(DOCENTE))],
    actual: Annotated[SesionActual, Depends(get_sesion_actual)],
):
    """Asignaciones vigentes, actividad activa y vencimiento de la sesión (CU010)."""
    return inicio_service.inicio_docente(db, docente, actual.sesion)


@router.get("/inicio/supervisor", response_model=InicioSupervisor)
def inicio_supervisor(db: Db, _: Annotated[Usuario, Depends(require_role(SUPERVISOR))]):
    """Indicadores globales y alertas de inactividad docente (CU011)."""
    return inicio_service.inicio_supervisor(db)


@router.get("/inicio/directivo", response_model=InicioDirectivo)
def inicio_directivo(db: Db, _: Annotated[Usuario, Depends(require_role(DIRECTIVO))]):
    """Indicadores institucionales, sin datos personales de alumnos (CU012)."""
    return inicio_service.inicio_directivo(db)
