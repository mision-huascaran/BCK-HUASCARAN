from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.organizacion import Colegio, Usuario
from app.schemas.colegio import ColegioCreate, ColegioResponse
from app.services.colegio_service import crear_colegio

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
    return db.exec(select(Colegio)).all()
