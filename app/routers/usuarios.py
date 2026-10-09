"""Gestión de cuentas de usuario (CU016). Todos los endpoints son solo del Supervisor."""
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.usuario import UsuarioCrear, UsuarioCreado, UsuarioDetalle, UsuarioEditar, UsuarioItem
from app.services import usuario_service

router = APIRouter(tags=["usuarios"])

Db = Annotated[Session, Depends(get_db)]
Supervisor = Annotated[Usuario, Depends(require_role("Supervisor"))]


@router.get("/usuarios", response_model=Paginado[UsuarioItem])
def listar_usuarios(
    db: Db,
    _: Supervisor,
    rol: Optional[Literal["Docente", "Supervisor", "Directivo"]] = None,
    activo: bool = True,
    q: Annotated[Optional[str], Query(description="Busca en nombres y apellidos")] = None,
    page: Pagina = 1,
):
    return usuario_service.listar_usuarios(db, rol, activo, q, page)


@router.get("/usuarios/{id_usuario}", response_model=UsuarioDetalle)
def obtener_usuario(id_usuario: int, db: Db, _: Supervisor):
    return usuario_service.obtener_usuario(db, id_usuario)


@router.post("/usuarios", response_model=UsuarioCreado, status_code=status.HTTP_201_CREATED)
def crear_usuario(data: UsuarioCrear, db: Db, actor: Supervisor):
    """Crea la cuenta con una contraseña temporal que se envía por correo. Si el correo no
    sale, `correo_enviado` es false y la contraseña viene en `contraseña_temporal`."""
    resultado = usuario_service.crear_usuario(db, data, actor)
    return UsuarioCreado(
        usuario=usuario_service.item_de(db, resultado.usuario),
        correo_enviado=resultado.correo_enviado,
        contraseña_temporal=resultado.contrasena_temporal,
    )


@router.patch("/usuarios/{id_usuario}", response_model=UsuarioItem)
def editar_usuario(id_usuario: int, data: UsuarioEditar, db: Db, actor: Supervisor):
    return usuario_service.actualizar_usuario(db, id_usuario, data, actor)


@router.patch("/usuarios/{id_usuario}/activar", response_model=UsuarioItem)
def activar_usuario(id_usuario: int, db: Db, actor: Supervisor):
    return usuario_service.cambiar_estado(db, id_usuario, True, actor)


@router.patch("/usuarios/{id_usuario}/desactivar", response_model=UsuarioItem)
def desactivar_usuario(id_usuario: int, db: Db, actor: Supervisor):
    """Desactiva la cuenta y cierra todas sus sesiones (y su actividad activa)."""
    return usuario_service.cambiar_estado(db, id_usuario, False, actor)
