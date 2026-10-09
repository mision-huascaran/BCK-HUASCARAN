"""Alumnos (CU014) y su vista de detalle (CU015).

Supervisor: todos los alumnos. Docente: los de su alcance; sus escrituras requieren una
actividad activa. Directivo: sin acceso (403).
"""
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.alumno import (
    AlumnoCrear,
    AlumnoDetalle,
    AlumnoEditar,
    AlumnoItem,
    EventoHistorial,
    LecturaSemanal,
    RegistroVuelo,
    Resumen,
    Rubrica,
)
from app.services import alumno_detalle_service as detalle
from app.services import alumno_service
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["alumnos"])

Db = Annotated[Session, Depends(get_db)]
ConAcceso = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]


@router.get("/alumnos", response_model=Paginado[AlumnoItem])
def listar_alumnos(
    db: Db,
    usuario: ConAcceso,
    id_colegio: Optional[int] = None,
    id_programa: Optional[int] = None,
    ciclo: Optional[Literal["III", "IV", "V"]] = None,
    id_grado: Optional[int] = None,
    activo: bool = True,
    q: Annotated[Optional[str], Query(description="Busca en nombres y apellidos")] = None,
    page: Pagina = 1,
):
    """Ordenado por apellidos y nombres. El Docente solo recibe los de su alcance."""
    return alumno_service.listar_alumnos(db, usuario, id_colegio, id_programa, ciclo, id_grado, activo, q, page)


@router.post("/alumnos", response_model=AlumnoItem, status_code=status.HTTP_201_CREATED)
def crear_alumno(data: AlumnoCrear, db: Db, usuario: ConAcceso):
    return alumno_service.crear_alumno(db, data, usuario)


@router.get("/alumnos/{id_alumno}", response_model=AlumnoDetalle)
def obtener_alumno(id_alumno: int, db: Db, usuario: ConAcceso):
    """Cabecera de la vista de detalle."""
    return alumno_service.obtener_alumno(db, usuario, id_alumno)


@router.patch("/alumnos/{id_alumno}", response_model=AlumnoItem)
def editar_alumno(id_alumno: int, data: AlumnoEditar, db: Db, usuario: ConAcceso):
    return alumno_service.editar_alumno(db, id_alumno, data, usuario)


@router.patch("/alumnos/{id_alumno}/activar", response_model=AlumnoItem)
def activar_alumno(id_alumno: int, db: Db, usuario: ConAcceso):
    return alumno_service.cambiar_estado(db, id_alumno, True, usuario)


@router.patch("/alumnos/{id_alumno}/desactivar", response_model=AlumnoItem)
def desactivar_alumno(id_alumno: int, db: Db, usuario: ConAcceso):
    return alumno_service.cambiar_estado(db, id_alumno, False, usuario)


# ── Pestañas de la vista de detalle (año escolar en curso) ──────────────────────────

@router.get("/alumnos/{id_alumno}/resumen", response_model=Resumen)
def resumen(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.resumen(db, id_alumno)


@router.get("/alumnos/{id_alumno}/registro-vuelo", response_model=list[RegistroVuelo])
def registro_vuelo(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.registro_vuelo(db, id_alumno)


@router.get("/alumnos/{id_alumno}/rubrica", response_model=Rubrica)
def rubrica(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.rubrica(db, id_alumno)


@router.get("/alumnos/{id_alumno}/lectura", response_model=list[LecturaSemanal])
def lectura(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.lectura(db, id_alumno)


@router.get("/alumnos/{id_alumno}/historial", response_model=Paginado[EventoHistorial])
def historial(id_alumno: int, db: Db, usuario: ConAcceso, page: Pagina = 1):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.historial(db, id_alumno, page)
