"""Asignaciones docente-colegio-grado-periodo (solo lectura).

Se crean y se cambian con POST y PATCH /usuarios.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.asignacion import AsignacionItem, MiAsignacion
from app.services import asignacion_service

router = APIRouter(tags=["asignaciones"])

Db = Annotated[Session, Depends(get_db)]


@router.get("/asignaciones", response_model=list[AsignacionItem])
def listar_asignaciones(
    db: Db,
    _: Annotated[Usuario, Depends(require_role("Supervisor"))],
    id_docente: Optional[int] = None,
    id_periodo_academico: Optional[int] = None,
    id_colegio: Optional[int] = None,
):
    return asignacion_service.listar_asignaciones(db, id_docente, id_periodo_academico, id_colegio)


@router.get("/me/asignaciones", response_model=list[MiAsignacion])
def mis_asignaciones(db: Db, docente: Annotated[Usuario, Depends(require_role("Docente"))]):
    """Colegios y grados que el Docente tiene a cargo en el periodo vigente."""
    if docente.id_docente is None:
        return []
    return asignacion_service.mis_asignaciones(db, docente.id_docente)
