from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.alumno import AlumnoCreate, AlumnoResponse
from app.services.alumno_service import (
    ColegioNoExiste,
    GradoNoExiste,
    ProgramaNoExiste,
    crear_alumno,
)

router = APIRouter(tags=["alumnos"])


@router.post("/alumnos", response_model=AlumnoResponse)
def crear_alumno_endpoint(
    data: AlumnoCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        return crear_alumno(db, data, usuario_actual)
    except ColegioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un colegio con id_colegio={data.id_colegio}",
        )
    except GradoNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un grado con id_grado={data.id_grado}",
        )
    except ProgramaNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un programa con id_programa_actual={data.id_programa_actual}",
        )
