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
    PermisoLoteCrear,
    PermisoRevocar,
)
from app.schemas.habilitacion import MisHabilitaciones
from app.services import habilitaciones_offline, permiso_service
from app.services.usuario_service import DOCENTE, SUPERVISOR

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


@router.post(
    "/usuarios/{id_usuario}/permisos/lote", response_model=list[PermisoDetalle], status_code=status.HTTP_201_CREATED
)
def otorgar_permisos_en_lote(id_usuario: int, data: PermisoLoteCrear, db: Db, actor: Supervisor):
    """Un permiso por cada combinación de grado y módulo (1 a 24), con el mismo año,
    colegio, PA, vigencia y motivo. Todo o nada: si una combinación falla no se guarda
    ninguna. Respuesta en el orden de `combinaciones`.

    Errores: 404 `docente_no_encontrado`; 409 `docente_inactivo`; 422 por campo
    (`motivo`, `inicio`, `fin`, `id_anio_escolar`, `id_colegio`, `id_periodo_academico`,
    `combinaciones` repetidas, vacías o más de 24). Si fallan combinaciones (grado que el
    colegio no ofrece: 422 en `combinaciones.{i}.id_grado`; superposición: 409
    `permiso_superpuesto`), responde el status, `motivo` y `detail` de la primera, con
    todas en `combinaciones_rechazadas`."""
    return permiso_service.otorgar_lote(db, id_usuario, data, actor)


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


@router.get("/me/permisos", response_model=list[PermisoItem])
def mis_permisos(db: Db, docente: Annotated[Usuario, Depends(require_role(DOCENTE))]):
    """Permisos de corrección vigentes del Docente autenticado (misma forma que los ítems
    de GET /usuarios/{id}/permisos), por fin de vigencia."""
    return permiso_service.mis_permisos_vigentes(db, docente)


@router.get("/me/habilitaciones", response_model=MisHabilitaciones)
def mis_habilitaciones(db: Db, docente: Annotated[Usuario, Depends(require_role(DOCENTE))]):
    """Lo que el front guarda para validar sin conexión: asignaciones de PA en curso o
    futuros, retroactivas cuyo PR no terminó, permisos programados o vigentes y el
    calendario de cada (colegio, año escolar) que aparece en ellos."""
    return habilitaciones_offline.mis_habilitaciones(db, docente)
