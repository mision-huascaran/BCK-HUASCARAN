"""Programación de clases y reprogramaciones (CU013)."""
from datetime import date
from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.clase import ClaseItem, ReprogramacionCrear, ReprogramacionItem
from app.services import clase_service
from app.services.programacion import FiltroGrupos
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["clases"])

Db = Annotated[Session, Depends(get_db)]
Lector = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]


@router.get("/colegios/{id_colegio}/clases", response_model=Union[Paginado[ClaseItem], list[ClaseItem]])
def listar_clases(
    id_colegio: int,
    db: Db,
    usuario: Lector,
    id_grado: Optional[int] = None,
    id_seccion: Optional[int] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    page: Annotated[Optional[int], Query(ge=1)] = None,
):
    """Clases del colegio (habituales calculadas, reprogramadas en su fecha nueva y
    adicionales), por fecha vigente, grado y sección. Por defecto, del año del
    calendario del colegio que contiene hoy. Con `page`, paginado de 40; sin él, el
    arreglo completo. Supervisor: cualquier colegio; Docente: su alcance de lectura.

    Errores: 403 `aula_fuera_de_alcance`, 404 `colegio_no_encontrado`, 422
    `rango_fechas_invalido`.
    """
    return clase_service.listar_clases(db, usuario, FiltroGrupos(id_colegio, id_grado, id_seccion), (desde, hasta), page)


@router.post(
    "/colegios/{id_colegio}/clases/reprogramaciones", response_model=ClaseItem, status_code=status.HTTP_201_CREATED
)
def reprogramar_clase(
    id_colegio: int,
    data: ReprogramacionCrear,
    db: Db,
    supervisor: Annotated[Usuario, Depends(require_role(SUPERVISOR))],
):
    """Traslada una clase habitual a otra fecha lectiva del mismo PA (puede ser otro día
    de la semana). Se identifica por grupo y fecha programada; si no tenía fila, se crea.
    Se puede volver a trasladar (cada traslado queda en el historial).

    Errores: 404 `colegio_no_encontrado`, `clase_no_programada`; 409
    `clase_con_asistencias` ("Esta clase ya tiene asistencias registradas y no puede
    reprogramarse."), `modificacion_concurrente`; 422 `seccion_inexistente`,
    `validacion` (motivo vacío), `fecha_sin_cambio`, `fecha_en_receso`,
    `fecha_no_lectiva`, `fecha_de_otro_periodo`, `fecha_ocupada`.
    """
    return clase_service.reprogramar(db, id_colegio, data, supervisor)


@router.get("/clases/{id_clase}/reprogramaciones", response_model=list[ReprogramacionItem])
def historial_de_reprogramaciones(id_clase: int, db: Db, usuario: Lector):
    """Traslados de la clase, del más antiguo al más reciente. Errores: 403
    `aula_fuera_de_alcance`, 404 `clase_no_encontrada`."""
    return clase_service.historial(db, usuario, id_clase)
