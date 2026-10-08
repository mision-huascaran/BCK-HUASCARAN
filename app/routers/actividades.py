"""Actividades de trabajo del Docente (CU009, CU010, CU017, CU018, CU019).

Solo el Docente, y solo las suyas; 403 los demás roles. Lo que los CU017 a CU019 llaman
"sesión" es una actividad.
"""
import uuid
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import SesionActual, get_sesion_actual, require_role
from app.models.organizacion import Usuario
from app.schemas.actividad import (
    ActividadDetalle,
    ActividadFinalizar,
    ActividadIniciar,
    ActividadRespuesta,
    ActividadResumen,
    EstadoActividad,
    TipoCierre,
)
from app.services import actividad as servicio
from app.services import actividad_consulta
from app.services.actividad_consulta import FiltrosActividad
from app.services.sincronizacion import EstadoSincronizacionActividad
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


@router.get("/actividades", response_model=Paginado[ActividadResumen])
def listar_actividades(
    db: Db,
    docente: SoloDocente,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    estado: Optional[EstadoActividad] = None,
    sincronizacion: Optional[EstadoSincronizacionActividad] = None,
    tipo_cierre: Optional[TipoCierre] = None,
    page: Pagina = 1,
):
    """Actividades propias, de la más reciente a la más antigua (CU017, CU018). `desde` y
    `hasta` son días de Lima, inclusivos, sobre el inicio. Sin filtros = "Todos"."""
    servicio.cerrar_vencidas_y_confirmar(db, docente.id_usuario)
    filtros = FiltrosActividad(desde, hasta, estado, sincronizacion, tipo_cierre)
    return actividad_consulta.listar_actividades(db, docente.id_docente, filtros, page)


@router.get("/actividades/{id_actividad}", response_model=ActividadDetalle)
def detalle_actividad(id_actividad: uuid.UUID, db: Db, docente: SoloDocente):
    """Detalle de una actividad propia (CU019). Ajena o inexistente: 404."""
    servicio.cerrar_vencidas_y_confirmar(db, docente.id_usuario)
    return actividad_consulta.detalle_actividad(db, docente.id_docente, id_actividad)
