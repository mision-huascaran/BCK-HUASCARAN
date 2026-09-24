from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.organizacion import Colegio, Usuario
from app.schemas.colegio import ColegioCreate, ColegioResponse, ColegioUpdate
from app.services.asignacion_service import alcance_del_docente
from app.services.colegio_service import ColegioNoExiste, actualizar_colegio, crear_colegio

router = APIRouter(tags=["colegios"])


@router.post("/colegios", response_model=ColegioResponse)
def crear_colegio_endpoint(
    data: ColegioCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    return crear_colegio(db, data, usuario_actual)


@router.get("/colegios", response_model=List[ColegioResponse])
def listar_colegios(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """Todos los colegios; al Docente, solo aquellos donde tiene asignacion vigente."""
    if usuario_actual.id_docente is not None:
        ids = {c for c, _ in alcance_del_docente(db, usuario_actual.id_docente)}
        if not ids:
            return []
        return db.exec(select(Colegio).where(Colegio.id_colegio.in_(ids))).all()
    return db.exec(select(Colegio)).all()


@router.patch("/colegios/{id_colegio}", response_model=ColegioResponse)
def actualizar_colegio_endpoint(
    id_colegio: int,
    data: ColegioUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        return actualizar_colegio(db, id_colegio, data, usuario_actual)
    except ColegioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un colegio con id_colegio={id_colegio}",
        )
