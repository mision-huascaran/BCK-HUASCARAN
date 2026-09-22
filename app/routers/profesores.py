from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.profesor import ProfesorCreate, ProfesorResponse
from app.services.profesor_service import (
    CorreoYaRegistrado,
    RolDocenteNoConfigurado,
    crear_profesor,
)

router = APIRouter(tags=["profesores"])


@router.post("/profesores", response_model=ProfesorResponse)
def crear_profesor_endpoint(
    data: ProfesorCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        usuario, contraseña_temporal = crear_profesor(db, data, usuario_actual)
    except CorreoYaRegistrado:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo {data.correo} ya está registrado",
        )
    except RolDocenteNoConfigurado:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error de configuración del sistema: el rol Docente no existe",
        )

    return ProfesorResponse(
        **usuario.model_dump(),
        contraseña_temporal=contraseña_temporal,
    )
