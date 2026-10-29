"""Endpoints provisionales de indicadores"""
from typing import Annotated, Optional

from fastapi import Query

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario, Rol
from app.schemas.dashboard import (
    AsistenciaDashboard, AprendizajePeriodoDashboard, CumplimientoDashboard, FiltrosDashboard, LecturaDashboard, NivelDashboard,
)
from app.services import dashboard_service
from app.services.usuario_service import DIRECTIVO, SUPERVISOR

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Db = Annotated[Session, Depends(get_db)]
Acceso = Annotated[Usuario, Depends(require_role(SUPERVISOR, DIRECTIVO))]


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
    from app.core.errores import error_de_campo
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


@router.get("/niveles", response_model=list[NivelDashboard])
def niveles(db: Db, _usuario: Acceso, id_colegio: Optional[int] = None,
            id_grado: Optional[int] = None, id_seccion: Optional[int] = None,
            id_programa: Optional[int] = None, id_periodo_academico: Optional[int] = None,
            id_anio_escolar: Optional[int] = None,
            id_colegios: Optional[list[int]] = Query(default=None)):
    return dashboard_service.niveles(db, _filtros(id_colegio, id_grado, id_seccion, id_programa,
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


@router.get("/cumplimiento", response_model=CumplimientoDashboard)
def cumplimiento(db: Db, _usuario: Acceso, id_colegio: Optional[int] = None,
                 id_grado: Optional[int] = None, id_seccion: Optional[int] = None,
                 id_programa: Optional[int] = None, id_periodo_academico: Optional[int] = None,
            id_anio_escolar: Optional[int] = None,
            id_colegios: Optional[list[int]] = Query(default=None)):
    return dashboard_service.cumplimiento(db, _filtros(id_colegio, id_grado, id_seccion, id_programa,
                                                        id_periodo_academico, id_anio_escolar, id_colegios))