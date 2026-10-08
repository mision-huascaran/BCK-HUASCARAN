"""Actividades de trabajo del Docente (CU009, CU010). Solo el Docente; 403 los demás."""
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import SesionActual, get_sesion_actual, require_role
from app.models.organizacion import Usuario
from app.schemas.actividad import ActividadFinalizar, ActividadIniciar, ActividadRespuesta
from app.services import actividad as servicio
from app.services.usuario_service import DOCENTE

router = APIRouter(tags=["actividades"])

Db = Annotated[Session, Depends(get_db)]
SoloDocente = Annotated[Usuario, Depends(require_role(DOCENTE))]
Actual = Annotated[SesionActual, Depends(get_sesion_actual)]


@router.post(
    "/actividades",
    response_model=ActividadRespuesta,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ActividadRespuesta, "description": "El id ya estaba registrado para este docente"}},
)
def iniciar_actividad(data: ActividadIniciar, db: Db, docente: SoloDocente, actual: Actual, response: Response):
    """Inicia una actividad en la sesión actual. Idempotente por `id_actividad`: si ese id
    ya está registrado para el docente, responde 200 con esa actividad sin cambios."""
    actividad, creada = servicio.iniciar_actividad(db, docente, actual.sesion, data.id_actividad, data.inicio)
    if not creada:
        response.status_code = status.HTTP_200_OK
    return ActividadRespuesta(actividad=servicio.a_item(actividad), sesion_expira=actual.sesion.expira)


@router.post("/actividades/{id_actividad}/finalizar", response_model=ActividadRespuesta)
def finalizar_actividad(
    id_actividad: uuid.UUID,
    db: Db,
    docente: SoloDocente,
    actual: Actual,
    data: Annotated[Optional[ActividadFinalizar], Body()] = None,
):
    """Finaliza la actividad (no cierra la sesión). Si ya estaba finalizada, responde 200
    con su estado actual, sin cambios. El cuerpo es opcional."""
    fin = data.fin if data is not None else None
    actividad = servicio.finalizar_actividad(db, docente, id_actividad, fin)
    return ActividadRespuesta(actividad=servicio.a_item(actividad), sesion_expira=actual.sesion.expira)
