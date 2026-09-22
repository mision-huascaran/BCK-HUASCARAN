from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.organizacion import Grado, Programa, Usuario
from app.schemas.catalogos import GradoResponse, ProgramaResponse

router = APIRouter(tags=["catalogos"])


@router.get("/grados", response_model=List[GradoResponse])
def listar_grados(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    return db.exec(select(Grado)).all()


@router.get("/programas", response_model=List[ProgramaResponse])
def listar_programas(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    return db.exec(select(Programa)).all()
