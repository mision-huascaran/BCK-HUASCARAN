from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.dependencies import get_current_user
from app.models.organizacion import Usuario
from app.schemas.auth import LoginRequest, TokenResponse, UsuarioResponse
from app.services.auth_service import (
    CredencialesInvalidas,
    CuentaInactiva,
    authenticate_user,
)

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    try:
        usuario = authenticate_user(db, data.correo, data.password)
    except CredencialesInvalidas:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )
    except CuentaInactiva:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está deshabilitada. Contacta a tu supervisor.",
        )

    access_token = create_access_token(
        {
            "id_usuario": usuario.id_usuario,
            "id_rol": usuario.id_rol,
            "correo": usuario.correo,
            "id_docente": usuario.id_docente,
        }
    )
    return TokenResponse(access_token=access_token)


@router.post("/logout")
def logout():
    return {"detail": "Sesión cerrada correctamente"}


@router.get("/me", response_model=UsuarioResponse)
def me(current_user: Usuario = Depends(get_current_user)):
    return current_user
