"""Seguimiento de docentes (CU020, CU021). Solo el Supervisor; 403 los demás roles.

CU021 se separa en cabecera e historial: la paginación del historial no recarga la
cabecera, y cada pantalla pide solo lo que muestra.
"""
import uuid
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.actividad import ActividadResumen, EstadoActividad, TipoCierre
from app.schemas.seguimiento import DocenteSeguimiento, ModuloRegistros, RegistroDeActividad
from app.services import registros_actividad, seguimiento_service
from app.services.actividad_consulta import FiltrosActividad
from app.services.sincronizacion import EstadoSincronizacionActividad, EstadoSincronizacionDocente
from app.services.usuario_service import SUPERVISOR

router = APIRouter(tags=["seguimiento"])

Db = Annotated[Session, Depends(get_db)]
SoloSupervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]


@router.get("/seguimiento/docentes", response_model=Paginado[DocenteSeguimiento])
def listar_docentes(
    db: Db,
    _: SoloSupervisor,
    id_colegio: Optional[int] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    sincronizacion: Optional[EstadoSincronizacionDocente] = None,
    activo: bool = True,
    page: Pagina = 1,
):
    """CU020. `id_colegio`: asignado al PA actual de ese colegio. `desde` y `hasta`: días de
    Lima, inclusivos, sobre la última conexión (quien nunca entró queda fuera)."""
    return seguimiento_service.listar_docentes(db, id_colegio, desde, hasta, sincronizacion, activo, page)


@router.get("/seguimiento/docentes/{id_docente}", response_model=DocenteSeguimiento)
def obtener_docente(id_docente: int, db: Db, _: SoloSupervisor):
    """Cabecera de CU021."""
    return seguimiento_service.obtener_docente(db, id_docente)


@router.get("/seguimiento/docentes/{id_docente}/actividades", response_model=Paginado[ActividadResumen])
def actividades_del_docente(
    id_docente: int,
    db: Db,
    _: SoloSupervisor,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    estado: Optional[EstadoActividad] = None,
    sincronizacion: Optional[EstadoSincronizacionActividad] = None,
    tipo_cierre: Optional[TipoCierre] = None,
    page: Pagina = 1,
):
    """Historial de CU021, de la más reciente a la más antigua, con la productividad de
    cada actividad. Acepta los mismos filtros que GET /actividades."""
    filtros = FiltrosActividad(desde, hasta, estado, sincronizacion, tipo_cierre)
    return seguimiento_service.actividades_del_docente(db, id_docente, filtros, page)


@router.get(
    "/seguimiento/docentes/{id_docente}/actividades/{id_actividad}/registros",
    response_model=Paginado[RegistroDeActividad],
)
def registros_de_la_actividad(
    id_docente: int, id_actividad: uuid.UUID, modulo: ModuloRegistros, db: Db, _: SoloSupervisor, page: Pagina = 1
):
    """Atajos de CU021: los registros del módulo tocados en esa actividad (según la
    auditoría de la actividad, no por fecha), con el contexto para abrirlos en solo
    lectura. `asistencias`: una fila por clase. Errores: 404 `docente_no_encontrado` y
    `actividad_no_encontrada`; 422 `validacion` si `modulo` no es uno de
    `asistencias`, `lectura`, `rubrica`, `registro_vuelo`, `alumnos`."""
    return registros_actividad.registros_de_la_actividad(db, id_docente, id_actividad, modulo, page)
