from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.organizacion import Usuario
from app.schemas.password import (
    CambiarContraseñaRequest,
    RecuperarContraseñaRequest,
    RestablecerContraseñaRequest,
    VerificarCodigoRequest,
)
from app.services.password_service import (
    CodigoInvalidoOExpirado,
    ContraseñaIgualALaActual,
    EnvioDeCodigoFallido,
    cambiar_contraseña,
    restablecer_contraseña,
    solicitar_codigo,
    solicitar_codigo_publico,
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


@router.post("/password/recuperar")
def recuperar_contraseña_endpoint(
    data: RecuperarContraseñaRequest,
    db: Session = Depends(get_db),
):
    """Flujo publico, sin token: envia el codigo al correo indicado.

    Responde 200 siempre, exista o no el correo. Si respondiera 404 para los correos
    desconocidos, cualquiera podria usar este endpoint para averiguar que correos
    tienen cuenta en el sistema.
    """
    solicitar_codigo_publico(db, data.correo)
    return {"detail": "Si el correo está registrado, enviamos un código de verificación"}


@router.post("/password/restablecer")
def restablecer_contraseña_endpoint(
    data: RestablecerContraseñaRequest,
    db: Session = Depends(get_db),
):
    """Flujo publico, sin token: cambia la contraseña con el codigo recibido por correo."""
    try:
        restablecer_contraseña(db, data)
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
