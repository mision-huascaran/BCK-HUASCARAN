"""Dashboard del Supervisor y del Directivo (CU033, CU034 y CU035).

`/asistencia` y `/lectura` son los endpoints provisionales de Romina; se reemplazan en
las tandas siguientes.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_db
from app.core.errores import error_de_campo
from app.models.organizacion import Rol
from app.schemas.dashboard import (
    AsistenciaDashboard, DistribucionSubprograma, FiltrosDashboard, FiltrosDisponibles, LecturaDashboard,
    ResultadosAprendizaje,
)
from app.services import dashboard_service
from app.services.dashboard.aprendizaje import distribucion_subprograma, resultados_aprendizaje
from app.services.dashboard.carga_inicial import filtros_disponibles
from app.services.dashboard.filtros import FiltrosDelPeriodo
from app.services.dashboard.roles import SoloSupervisor, SupervisorODirectivo
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


@router.get("/distribucion-subprograma", response_model=DistribucionSubprograma)
def distribucion(db: Db, _usuario: SoloSupervisor, filtros: FiltrosDelPeriodo):
    """CU033: alumnos por subprograma (cantidad y porcentaje sobre el total) según su
    pertenencia en la fecha de corte de su colegio: el fin del PA en su calendario o, si
    sigue en curso, hoy (`fechas_corte`). Un colegio con el PA sin iniciar va en
    `colegios_sin_informacion`. Sin alumnos: `sin_datos` con el mensaje del CU.

    Solo Supervisor. Errores: los de los filtros comunes del Dashboard."""
    return distribucion_subprograma(db, filtros)


@router.get("/resultados-aprendizaje", response_model=ResultadosAprendizaje)
def resultados(db: Db, _usuario: SupervisorODirectivo, filtros: FiltrosDelPeriodo):
    """CU033 (paso 6) y CU035 (paso 6): por subprograma configurado, el resultado global
    (nivel del programa de la decisión vigente del Registro de Vuelo) y las dimensiones
    Fluidez y Comprensión (última evaluación mensual completa del PA). Todos los niveles de
    cada escala, también los de 0; porcentajes sobre los evaluados y los pendientes aparte.

    Supervisor y Directivo (solo agregados). Errores: los de los filtros comunes."""
    return resultados_aprendizaje(db, filtros)


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
