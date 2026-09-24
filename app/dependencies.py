from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlmodel import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.organizacion import Rol, Usuario

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas o sesión expirada",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
    except JWTError:
        raise credentials_exception

    id_usuario = payload.get("id_usuario")
    if id_usuario is None:
        raise credentials_exception

    usuario = db.get(Usuario, id_usuario)
    if usuario is None or not usuario.activo:
        raise credentials_exception

    return usuario


def require_role(*roles_permitidos: str):
    def dependency(
        current_user: Usuario = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Usuario:
        rol = db.get(Rol, current_user.id_rol)
        if rol is None or rol.nombre not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta acción",
            )
        return current_user
    return dependency
