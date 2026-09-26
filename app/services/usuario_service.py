import secrets
from datetime import date
from typing import Optional

from sqlmodel import Session, func, select

from app.core.email import enviar_correo_bienvenida_profesor
from app.core.security import hash_password
from app.models.organizacion import Docente, Rol, Usuario
from app.schemas.usuario import UsuarioCreate, UsuarioUpdate

# Roles administrativos: se crean con POST /usuarios y no tienen ficha de docente.
ROLES_ADMINISTRATIVOS = ("Supervisor", "Directivo")

# Que cuentas puede gestionar cada rol (listar, crear, editar, activar, desactivar).
# El Supervisor lleva Docentes y Supervisores; el Directivo, solo otros Directivos.
# Sin este techo, un Supervisor podria crearse un Directivo con una peticion directa
# aunque la interfaz no le ofrezca el boton.
PARCELA_POR_ROL = {
    "Supervisor": ("Docente", "Supervisor"),
    "Directivo": ("Directivo",),
}

# Roles que no pueden quedarse sin ninguna cuenta activa: si se desactivara la ultima,
# nadie podria volver a entrar a administrar el sistema.
ROLES_CON_MINIMO_UNA_CUENTA = ("Supervisor", "Directivo")


class CorreoYaRegistrado(Exception):
    """El correo ya está en uso por otro usuario."""


class RolNoExiste(Exception):
    """El id_rol no corresponde a ningún rol del catálogo."""


class RolNoAdministrable(Exception):
    """Se intentó crear un Docente por /usuarios; para eso existe POST /profesores."""


class UsuarioNoExiste(Exception):
    """No existe un usuario con ese id."""


class FueraDeTuParcela(Exception):
    """El rol de quien llama no gestiona cuentas de ese otro rol."""

    def __init__(self, rol_objetivo: str, rol_actor: str):
        self.rol_objetivo = rol_objetivo
        self.rol_actor = rol_actor
        super().__init__(rol_objetivo)


class NoPuedeDesactivarseASiMismo(Exception):
    """Desactivar la propia cuenta invalida la sesion en el acto y deja fuera al usuario."""


class UltimaCuentaActivaDelRol(Exception):
    """Desactivarla dejaría al rol sin ninguna cuenta activa."""

    def __init__(self, rol: str):
        self.rol = rol
        super().__init__(rol)


def nombre_rol(db: Session, id_rol: int) -> str:
    rol = db.get(Rol, id_rol)
    return rol.nombre if rol is not None else ""


def parcela_de(db: Session, usuario_actual: Usuario) -> tuple[str, ...]:
    """Nombres de rol que `usuario_actual` puede gestionar."""
    return PARCELA_POR_ROL.get(nombre_rol(db, usuario_actual.id_rol), ())


def _exigir_parcela(db: Session, usuario_actual: Usuario, rol_objetivo: str) -> None:
    if rol_objetivo not in parcela_de(db, usuario_actual):
        raise FueraDeTuParcela(rol_objetivo, nombre_rol(db, usuario_actual.id_rol))


def _contar_activos_por_rol(db: Session, id_rol: int) -> int:
    return db.exec(
        select(func.count())
        .select_from(Usuario)
        .where(Usuario.id_rol == id_rol, Usuario.activo.is_(True))
    ).one()


def crear_usuario(
    db: Session, data: UsuarioCreate, usuario_actual: Usuario
) -> tuple[Usuario, Optional[str], bool]:
    """Crea una cuenta administrativa (Supervisor o Directivo).

    Devuelve (usuario, contraseña_temporal, correo_enviado). La contraseña temporal
    solo se devuelve si el correo fallo; si se envio, vuelve como None.
    """
    ya_existe = db.exec(select(Usuario).where(Usuario.correo == data.correo)).first()
    if ya_existe is not None:
        raise CorreoYaRegistrado()

    rol = db.get(Rol, data.id_rol)
    if rol is None:
        raise RolNoExiste()

    _exigir_parcela(db, usuario_actual, rol.nombre)

    if rol.nombre not in ROLES_ADMINISTRATIVOS:
        raise RolNoAdministrable()

    contraseña_temporal = secrets.token_urlsafe(9)
    hoy = date.today()

    usuario = Usuario(
        id_rol=rol.id_rol,
        correo=data.correo,
        password_hash=hash_password(contraseña_temporal),
        id_docente=None,
        nombres=data.nombres,
        apellidos=data.apellidos,
        activo=data.activo,
        creado_por=usuario_actual.id_usuario,
        creado_en=hoy,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    correo_enviado = enviar_correo_bienvenida_profesor(
        data.correo, contraseña_temporal, data.nombres
    )
    if correo_enviado:
        return usuario, None, True
    return usuario, contraseña_temporal, False


def cambiar_estado_usuario(
    db: Session, id_usuario: int, activo: bool, usuario_actual: Usuario
) -> Usuario:
    """Activa o desactiva cualquier cuenta.

    Si la cuenta es de un docente, mantiene `docente.activo` en el mismo estado, igual
    que hace /profesores. Al desactivar, protege la ultima cuenta activa de Supervisor
    y de Directivo, y tambien la cuenta de quien hace la peticion: get_current_user
    rechaza a los usuarios inactivos, asi que desactivarse a uno mismo cierra la sesion
    en la siguiente peticion y deja al usuario fuera sin manera de revertirlo.
    """
    usuario = db.get(Usuario, id_usuario)
    if usuario is None:
        raise UsuarioNoExiste()

    rol_objetivo = nombre_rol(db, usuario.id_rol)
    _exigir_parcela(db, usuario_actual, rol_objetivo)

    if not activo and usuario.id_usuario == usuario_actual.id_usuario:
        raise NoPuedeDesactivarseASiMismo()

    if not activo and usuario.activo and rol_objetivo in ROLES_CON_MINIMO_UNA_CUENTA:
        if _contar_activos_por_rol(db, usuario.id_rol) <= 1:
            raise UltimaCuentaActivaDelRol(rol_objetivo)

    hoy = date.today()
    usuario.activo = activo
    usuario.modificado_por = usuario_actual.id_usuario
    usuario.modificado_en = hoy
    db.add(usuario)

    if usuario.id_docente is not None:
        docente = db.get(Docente, usuario.id_docente)
        if docente is not None:
            docente.activo = activo
            docente.modificado_por = usuario_actual.id_usuario
            docente.modificado_en = hoy
            db.add(docente)

    db.commit()
    db.refresh(usuario)
    return usuario


def listar_usuarios(db: Session, usuario_actual: Usuario, rol: Optional[str] = None) -> list[dict]:
    """Lista las cuentas que `usuario_actual` puede gestionar, con el nombre del rol resuelto.

    El recorte por parcela va en la consulta, no en la respuesta: un Directivo no recibe
    cuentas de Docente ni de Supervisor aunque pida `?rol=Docente`.
    """
    parcela = parcela_de(db, usuario_actual)
    consulta = select(Usuario, Rol).join(Rol, Usuario.id_rol == Rol.id_rol)
    consulta = consulta.where(Rol.nombre.in_(parcela))
    if rol is not None:
        consulta = consulta.where(Rol.nombre == rol)
    consulta = consulta.order_by(Usuario.id_usuario)

    return [
        {
            "id_usuario": usuario.id_usuario,
            "id_rol": usuario.id_rol,
            "rol": rol_fila.nombre,
            "correo": usuario.correo,
            "id_docente": usuario.id_docente,
            "nombres": usuario.nombres,
            "apellidos": usuario.apellidos,
            "activo": usuario.activo,
        }
        for usuario, rol_fila in db.exec(consulta).all()
    ]


def actualizar_usuario(
    db: Session, id_usuario: int, data: UsuarioUpdate, usuario_actual: Usuario
) -> Usuario:
    """Corrige nombres, apellidos o correo de una cuenta dentro de la parcela.

    Si la cuenta es de un docente, los nombres se escriben tambien en su ficha
    `docente`, igual que hace PATCH /profesores: viven duplicados en las dos tablas.
    """
    usuario = db.get(Usuario, id_usuario)
    if usuario is None:
        raise UsuarioNoExiste()

    _exigir_parcela(db, usuario_actual, nombre_rol(db, usuario.id_rol))

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

    if usuario.id_docente is not None:
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
