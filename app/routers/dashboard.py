"""Dashboard del Supervisor y del Directivo (CU033, CU034 y CU035).

`/asistencia`, `/lectura` y `/aprendizaje` son los endpoints provisionales de Romina; se
reemplazan en las tandas D2, D3 y D5.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_db
from app.core.errores import error_de_campo
from app.models.organizacion import Rol
from app.schemas.dashboard import (
    AprendizajePeriodoDashboard, AsistenciaDashboard, FiltrosDashboard, FiltrosDisponibles, LecturaDashboard,
)
from app.services import dashboard_service
from app.services.dashboard.carga_inicial import filtros_disponibles
from app.services.dashboard.roles import SupervisorODirectivo
from app.services.usuario_service import DIRECTIVO

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Db = Annotated[Session, Depends(get_db)]
Acceso = SupervisorODirectivo


@router.get("/filtros", response_model=FiltrosDisponibles)
def filtros(db: Db, usuario: SupervisorODirectivo):
    """Carga inicial de CU033 y CU035 (paso 3): colegios del alcance, años escolares con
    sus PA (nombre visible "Primer periodo"...) y la selección inicial.

    `seleccion_inicial` es el PA en que están hoy todos los colegios del alcance (en un
    receso, el PA anterior y su año). Es null si no coinciden o si hoy no cae en ningún PA
    ni PR configurado: el front pide elegir año y periodo."""
    return filtros_disponibles(db, usuario)


def _filtros(id_colegio, id_grado, id_seccion, id_programa, id_periodo_academico,
             id_anio_escolar, id_colegios):
    return FiltrosDashboard(
        id_colegio=id_colegio, id_colegios=id_colegios, id_anio_escolar=id_anio_escolar,
        id_grado=id_grado, id_seccion=id_seccion, id_programa=id_programa,
        id_periodo_academico=id_periodo_academico,
    )


@router.get("/asistencia", response_model=list[AsistenciaDashboard])
def asistencia(db: Db, usuario: Acceso, id_colegio: Optional[int] = None,
               id_grado: Optional[int] = None, id_seccion: Optional[int] = None,
               id_programa: Optional[int] = None, id_periodo_academico: Optional[int] = None,
               id_anio_escolar: Optional[int] = None,
               id_colegios: Optional[list[int]] = Query(default=None),
               por_alumno: bool = False):
    # Identificadores por alumno solo disponibles para Supervisor; Directivo recibe agregados.
    if por_alumno and db.get(Rol, usuario.id_rol).nombre == DIRECTIVO:
        raise error_de_campo("por_alumno", "El Directivo solo puede consultar información agregada.")
    return dashboard_service.asistencia(db, _filtros(id_colegio, id_grado, id_seccion, id_programa,
                                                      id_periodo_academico, id_anio_escolar, id_colegios), por_alumno)


@router.get("/lectura", response_model=LecturaDashboard)
def lectura(db: Db, _usuario: Acceso, id_colegio: Optional[int] = None,
            id_grado: Optional[int] = None, id_seccion: Optional[int] = None,
            id_programa: Optional[int] = None, id_periodo_academico: Optional[int] = None,
            id_anio_escolar: Optional[int] = None,
            id_colegios: Optional[list[int]] = Query(default=None)):
    return dashboard_service.libros(db, _filtros(id_colegio, id_grado, id_seccion, id_programa,
                                                 id_periodo_academico, id_anio_escolar, id_colegios))


@router.get("/aprendizaje", response_model=AprendizajePeriodoDashboard)
def aprendizaje(db: Db, _usuario: Acceso,
                id_periodo_academico: int,
                id_colegio: Optional[int] = None,
                id_grado: Optional[int] = None,
                id_seccion: Optional[int] = None,
                id_programa: Optional[int] = None,
                id_anio_escolar: Optional[int] = None,
                id_colegios: Optional[list[int]] = Query(default=None)):
    """CU033/CU035: niveles globales y dimensiones, con pendientes por PA."""
    return dashboard_service.aprendizaje(
        db, _filtros(id_colegio, id_grado, id_seccion, id_programa,
                     id_periodo_academico, id_anio_escolar, id_colegios)
    )
