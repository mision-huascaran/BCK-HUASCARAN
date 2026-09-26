import secrets
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.core.email import enviar_correo_bienvenida_profesor
from app.core.security import hash_password
from app.models.organizacion import Docente, Rol, Usuario
from app.schemas.profesor import ProfesorCreate, ProfesorUpdate


class CorreoYaRegistrado(Exception):
    """El correo ya está en uso por otro usuario."""


class RolDocenteNoConfigurado(Exception):
    """No existe un Rol con nombre 'Docente' en el catálogo (debería estar sembrado)."""


class ProfesorNoExiste(Exception):
    """No existe un usuario con ese id, o existe pero no es un profesor (no tiene id_docente)."""


def crear_profesor(
    db: Session, data: ProfesorCreate, usuario_actual: Usuario
) -> tuple[Usuario, Optional[str]]:
    ya_existe = db.exec(select(Usuario).where(Usuario.correo == data.correo)).first()
    if ya_existe is not None:
        raise CorreoYaRegistrado()

    rol_docente = db.exec(select(Rol).where(Rol.nombre == "Docente")).first()
    if rol_docente is None:
        raise RolDocenteNoConfigurado()

    contraseña_temporal = secrets.token_urlsafe(9)
    hoy = date.today()

    docente = Docente(
        nombres=data.nombres,
        apellidos=data.apellidos,
        activo=data.activo,
        creado_por=usuario_actual.id_usuario,
        creado_en=hoy,
    )
    db.add(docente)
    db.flush()

    usuario = Usuario(
        id_rol=rol_docente.id_rol,
        correo=data.correo,
        password_hash=hash_password(contraseña_temporal),
        id_docente=docente.id_docente,
        nombres=data.nombres,
        apellidos=data.apellidos,
        activo=data.activo,
        creado_por=usuario_actual.id_usuario,
        creado_en=hoy,
    )
    db.add(usuario)
    db.commit()
    db.refresh(docente)
    db.refresh(usuario)

    correo_enviado = enviar_correo_bienvenida_profesor(
        data.correo, contraseña_temporal, data.nombres
    )
    if correo_enviado:
        return usuario, None
    return usuario, contraseña_temporal


def cambiar_estado_profesor(
    db: Session, id_usuario: int, activo: bool, usuario_actual: Usuario
) -> Usuario:
    usuario = db.get(Usuario, id_usuario)
    if usuario is None or usuario.id_docente is None:
        raise ProfesorNoExiste()

    docente = db.get(Docente, usuario.id_docente)

    usuario.activo = activo
    usuario.modificado_por = usuario_actual.id_usuario
    usuario.modificado_en = date.today()

    docente.activo = activo
    docente.modificado_por = usuario_actual.id_usuario
    docente.modificado_en = date.today()

    db.add(usuario)
    db.add(docente)
    db.commit()
    db.refresh(usuario)
    return usuario


def listar_profesores(db: Session) -> list[Usuario]:
    return db.exec(
        select(Usuario).where(Usuario.id_docente.is_not(None)).order_by(Usuario.id_usuario)
    ).all()


def actualizar_profesor(
    db: Session, id_usuario: int, data: ProfesorUpdate, usuario_actual: Usuario
) -> Usuario:
    """Actualiza al profesor manteniendo `usuario` y `docente` en sincronia.

    Los nombres viven duplicados en las dos tablas, asi que se escriben en ambas:
    si solo se tocara `usuario`, el listado de docentes seguiria mostrando el nombre viejo.
    """
    usuario = db.get(Usuario, id_usuario)
    if usuario is None or usuario.id_docente is None:
        raise ProfesorNoExiste()

    cambios = data.model_dump(exclude_unset=True)

    nuevo_correo = cambios.get("correo")
    if nuevo_correo is not None and nuevo_correo != usuario.correo:
        ocupado = db.exec(select(Usuario).where(Usuario.correo == nuevo_correo)).first()
        if ocupado is not None:
            raise CorreoYaRegistrado()

    hoy = date.today()
    for campo, valor in cambios.items():
        setattr(usuario, campo, valor)
    usuario.modificado_por = usuario_actual.id_usuario
    usuario.modificado_en = hoy
    db.add(usuario)

    docente = db.get(Docente, usuario.id_docente)
    if docente is not None:
        if "nombres" in cambios:
            docente.nombres = cambios["nombres"]
        if "apellidos" in cambios:
            docente.apellidos = cambios["apellidos"]
        docente.modificado_por = usuario_actual.id_usuario
        docente.modificado_en = hoy
        db.add(docente)

    db.commit()
    db.refresh(usuario)
    return usuario
