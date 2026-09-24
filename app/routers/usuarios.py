from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.usuario import (
    UsuarioCreado,
    UsuarioCreate,
    UsuarioListItem,
    UsuarioResponse,
    UsuarioUpdate,
)
from app.services.usuario_service import (
    CorreoYaRegistrado,
    FueraDeTuParcela,
    NoPuedeDesactivarseASiMismo,
    RolNoAdministrable,
    RolNoExiste,
    UltimaCuentaActivaDelRol,
    UsuarioNoExiste,
    actualizar_usuario,
    cambiar_estado_usuario,
    crear_usuario,
    listar_usuarios,
)

router = APIRouter(tags=["usuarios"])

# Supervisor y Directivo entran a estos endpoints; que puede tocar cada uno lo decide
# la parcela de roles en usuario_service, no el rol de entrada.
GESTORES_DE_CUENTAS = ("Supervisor", "Directivo")


def _error_parcela(e: FueraDeTuParcela) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Un {e.rol_actor} no gestiona cuentas de {e.rol_objetivo}",
    )


@router.post("/usuarios", response_model=UsuarioCreado, status_code=status.HTTP_201_CREATED)
def crear_usuario_endpoint(
    data: UsuarioCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role(*GESTORES_DE_CUENTAS)),
):
    try:
        usuario, contraseña_temporal, correo_enviado = crear_usuario(db, data, usuario_actual)
    except CorreoYaRegistrado:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo {data.correo} ya está registrado",
        )
    except RolNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un rol con id_rol={data.id_rol}",
        )
    except FueraDeTuParcela as e:
        raise _error_parcela(e)
    except RolNoAdministrable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Para crear una cuenta de Docente usa POST /profesores, "
                   "que además crea su ficha de docente",
        )

    return UsuarioCreado(
        **usuario.model_dump(),
        contraseña_temporal=contraseña_temporal,
        correo_enviado=correo_enviado,
    )


@router.get("/usuarios", response_model=list[UsuarioListItem])
def listar_usuarios_endpoint(
    rol: Optional[str] = Query(default=None, description="Filtra por nombre de rol exacto"),
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role(*GESTORES_DE_CUENTAS)),
):
    return listar_usuarios(db, usuario_actual, rol)


@router.patch("/usuarios/{id_usuario}/desactivar", response_model=UsuarioResponse)
def desactivar_usuario_endpoint(
    id_usuario: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role(*GESTORES_DE_CUENTAS)),
):
    try:
        return cambiar_estado_usuario(db, id_usuario, activo=False, usuario_actual=usuario_actual)
    except UsuarioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un usuario con id_usuario={id_usuario}",
        )
    except FueraDeTuParcela as e:
        raise _error_parcela(e)
    except NoPuedeDesactivarseASiMismo:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No puedes desactivar tu propia cuenta. Pídeselo a otro Supervisor.",
        )
    except UltimaCuentaActivaDelRol as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No puedes desactivar la última cuenta activa de {e.rol}. "
                   f"Crea o activa otra cuenta de {e.rol} antes de desactivar esta.",
        )


@router.patch("/usuarios/{id_usuario}/activar", response_model=UsuarioResponse)
def activar_usuario_endpoint(
    id_usuario: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role(*GESTORES_DE_CUENTAS)),
):
    try:
        return cambiar_estado_usuario(db, id_usuario, activo=True, usuario_actual=usuario_actual)
    except UsuarioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un usuario con id_usuario={id_usuario}",
        )
    except FueraDeTuParcela as e:
        raise _error_parcela(e)


@router.patch("/usuarios/{id_usuario}", response_model=UsuarioResponse)
def actualizar_usuario_endpoint(
    id_usuario: int,
    data: UsuarioUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role(*GESTORES_DE_CUENTAS)),
):
    try:
        return actualizar_usuario(db, id_usuario, data, usuario_actual)
    except UsuarioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un usuario con id_usuario={id_usuario}",
        )
    except FueraDeTuParcela as e:
        raise _error_parcela(e)
    except CorreoYaRegistrado:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El correo {data.correo} ya está registrado",
        )
