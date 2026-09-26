from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.profesor import (
    ProfesorCreate,
    ProfesorListItem,
    ProfesorResponse,
    ProfesorUpdate,
)
from app.services.profesor_service import (
    CorreoYaRegistrado,
    ProfesorNoExiste,
    RolDocenteNoConfigurado,
    actualizar_profesor,
    cambiar_estado_profesor,
    crear_profesor,
    listar_profesores,
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


@router.get("/profesores", response_model=list[ProfesorListItem])
def listar_profesores_endpoint(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    return listar_profesores(db)


@router.patch("/profesores/{id_usuario}/desactivar", response_model=ProfesorResponse)
def desactivar_profesor_endpoint(
    id_usuario: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        usuario = cambiar_estado_profesor(db, id_usuario, activo=False, usuario_actual=usuario_actual)
    except ProfesorNoExiste:
        raise HTTPException(status_code=404, detail=f"No existe un profesor con id_usuario={id_usuario}")
    return usuario


@router.patch("/profesores/{id_usuario}/activar", response_model=ProfesorResponse)
def activar_profesor_endpoint(
    id_usuario: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        usuario = cambiar_estado_profesor(db, id_usuario, activo=True, usuario_actual=usuario_actual)
    except ProfesorNoExiste:
        raise HTTPException(status_code=404, detail=f"No existe un profesor con id_usuario={id_usuario}")
    return usuario


@router.patch("/profesores/{id_usuario}", response_model=ProfesorResponse)
def actualizar_profesor_endpoint(
    id_usuario: int,
    data: ProfesorUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        return actualizar_profesor(db, id_usuario, data, usuario_actual)
    except ProfesorNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un profesor con id_usuario={id_usuario}",
        )
    except CorreoYaRegistrado:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo {data.correo} ya está registrado",
        )
