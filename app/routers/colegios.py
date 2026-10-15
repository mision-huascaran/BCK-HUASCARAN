"""Colegios (CU013).

Lectura: Supervisor (todos) y Docente (los de su alcance). Escritura: solo Supervisor.
El Directivo no tiene acceso (403).
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.colegio import ColegioCrear, ColegioDetalle, ColegioEditar, ColegioItem, Ubicaciones
from app.services import colegio_service
from app.services.usuario_service import DOCENTE, SUPERVISOR, nombre_rol

router = APIRouter(tags=["colegios"])

Db = Annotated[Session, Depends(get_db)]
Lector = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]
Supervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]


def _visibles(db: Session, usuario: Usuario) -> Optional[set[int]]:
    return colegio_service.colegios_visibles(db, usuario, nombre_rol(db, usuario.id_rol) == DOCENTE)


@router.get("/colegios", response_model=Paginado[ColegioItem])
def listar_colegios(
    db: Db,
    usuario: Lector,
    departamento: Optional[str] = None,
    distrito: Optional[str] = None,
    activo: bool = True,
    page: Pagina = 1,
):
    """Supervisor: todos. Docente: los de su alcance en el periodo vigente.
    `departamento` y `distrito` comparan sin distinguir mayúsculas ni espacios extremos."""
    return colegio_service.listar_colegios(
        db, _visibles(db, usuario), departamento, distrito, activo, page
    )


@router.get("/colegios/ubicaciones", response_model=Ubicaciones)
def listar_ubicaciones(db: Db, usuario: Lector, departamento: Optional[str] = None):
    """Departamentos y distritos distintos, para los filtros. Con `departamento`, los
    distritos se limitan a ese departamento."""
    return colegio_service.ubicaciones(db, _visibles(db, usuario), departamento)


@router.get("/colegios/{id_colegio}", response_model=ColegioDetalle)
def obtener_colegio(id_colegio: int, db: Db, usuario: Lector):
    return colegio_service.obtener_colegio(db, _visibles(db, usuario), id_colegio)


@router.post("/colegios", response_model=ColegioDetalle, status_code=status.HTTP_201_CREATED)
def crear_colegio(data: ColegioCrear, db: Db, actor: Supervisor):
    return colegio_service.crear_colegio(db, data, actor)


@router.patch("/colegios/{id_colegio}", response_model=ColegioDetalle)
def editar_colegio(id_colegio: int, data: ColegioEditar, db: Db, actor: Supervisor):
    """`grados` y `programas`, si se envían, son la lista completa (reemplazan a la actual)."""
    return colegio_service.editar_colegio(db, id_colegio, data, actor)


@router.patch("/colegios/{id_colegio}/activar", response_model=ColegioItem)
def activar_colegio(id_colegio: int, db: Db, actor: Supervisor):
    return colegio_service.cambiar_estado(db, id_colegio, True, actor)


@router.patch("/colegios/{id_colegio}/desactivar", response_model=ColegioItem)
def desactivar_colegio(id_colegio: int, db: Db, actor: Supervisor):
    """Borrado lógico: no toca alumnos, registros ni asignaciones."""
    return colegio_service.cambiar_estado(db, id_colegio, False, actor)
