from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.organizacion import Usuario
from app.schemas.password import CambiarContraseñaRequest, VerificarCodigoRequest
from app.services.password_service import (
    CodigoInvalidoOExpirado,
    ContraseñaIgualALaActual,
    EnvioDeCodigoFallido,
    cambiar_contraseña,
    solicitar_codigo,
    verificar_codigo,
)

router = APIRouter(tags=["password"])


@router.post("/me/password/codigo")
def solicitar_codigo_endpoint(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    try:
        solicitar_codigo(db, usuario_actual)
    except EnvioDeCodigoFallido:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No pudimos enviar el código, intenta de nuevo",
        )
    return {"detail": "Código enviado a tu correo"}


@router.post("/me/password/verificar-codigo")
def verificar_codigo_endpoint(
    data: VerificarCodigoRequest,
    usuario_actual: Usuario = Depends(get_current_user),
):
    if not verificar_codigo(usuario_actual, data.codigo):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código incorrecto o expirado",
        )
    return {"detail": "Código correcto"}


@router.post("/me/password")
def cambiar_contraseña_endpoint(
    data: CambiarContraseñaRequest,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    try:
        cambiar_contraseña(db, usuario_actual, data)
    except CodigoInvalidoOExpirado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código incorrecto o expirado",
        )
    except ContraseñaIgualALaActual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña nueva no puede ser igual a la actual",
        )
    return {"detail": "Contraseña actualizada correctamente"}
