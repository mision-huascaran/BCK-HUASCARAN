"""Permisos excepcionales de corrección (CU028). Solo el Supervisor, sin actividad."""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.permiso import (
    EstadoPermiso,
    ModuloPermiso,
    OpcionesPermiso,
    PermisoCrear,
    PermisoDetalle,
    PermisoItem,
    PermisoRevocar,
)
from app.services import permiso_service
from app.services.usuario_service import SUPERVISOR

router = APIRouter(tags=["permisos"])

Db = Annotated[Session, Depends(get_db)]
Supervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]


@router.get("/usuarios/{id_usuario}/permisos", response_model=Paginado[PermisoItem])
def listar_permisos(
    id_usuario: int,
    db: Db,
    _: Supervisor,
    id_anio_escolar: Optional[int] = None,
    id_colegio: Optional[int] = None,
    id_periodo_academico: Optional[int] = None,
    modulo: Optional[ModuloPermiso] = None,
    estado: Optional[EstadoPermiso] = None,
    page: Pagina = 1,
):
    """Del más reciente otorgado al más antiguo, con el estado calculado. 404 si el
    usuario no es un Docente."""
    filtros = {
        "id_anio_escolar": id_anio_escolar,
        "id_colegio": id_colegio,
        "id_periodo_academico": id_periodo_academico,
        "modulo": modulo,
        "estado": estado,
    }
    return permiso_service.listar(db, id_usuario, filtros, page)


@router.get("/usuarios/{id_usuario}/permisos/{id_permiso}", response_model=PermisoDetalle)
def obtener_permiso(id_usuario: int, id_permiso: int, db: Db, _: Supervisor):
    return permiso_service.obtener(db, id_usuario, id_permiso)


@router.post("/usuarios/{id_usuario}/permisos", response_model=PermisoDetalle, status_code=status.HTTP_201_CREATED)
def otorgar_permiso(id_usuario: int, data: PermisoCrear, db: Db, actor: Supervisor):
    return permiso_service.otorgar(db, id_usuario, data, actor)


@router.post("/usuarios/{id_usuario}/permisos/{id_permiso}/revocar", response_model=PermisoDetalle)
def revocar_permiso(id_usuario: int, id_permiso: int, data: PermisoRevocar, db: Db, actor: Supervisor):
    return permiso_service.revocar(db, id_usuario, id_permiso, data.motivo, actor)


@router.get("/permisos/opciones", response_model=OpcionesPermiso)
def opciones_de_permiso(id_colegio: int, id_anio_escolar: int, db: Db, _: Supervisor):
    """PA del colegio cuyo plazo ordinario ya terminó y grados que ofrece, para el modal.
    No se limita a las asignaciones del docente."""
    return permiso_service.opciones(db, id_colegio, id_anio_escolar)
