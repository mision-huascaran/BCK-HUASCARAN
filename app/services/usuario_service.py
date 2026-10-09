"""Gestión de cuentas de usuario (CU016). Solo el Supervisor gestiona cuentas, de los 3 roles.

- Supervisor original: no se puede desactivar ni cambiar de rol (sí editar sus datos).
  Garantiza que siempre haya al menos un Supervisor activo.
- Nadie puede desactivarse a sí mismo.
- Desactivar cierra todas las sesiones del usuario y su actividad activa.
- Cada cambio queda en `auditoria` (tabla `usuario`).
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import status
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, or_, select

from app.core.email import enmascarar_correo, enviar_correo_bienvenida_profesor
from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.core.politica_contrasena import generar_contrasena_temporal
from app.core.security import hash_password
from app.core.tiempo import ahora_utc
from app.models.organizacion import Docente, Rol, Usuario
from app.schemas.comun import ItemCatalogo
from app.schemas.usuario import (
    AsignacionEditar,
    UsuarioCrear,
    UsuarioDetalle,
    UsuarioEditar,
    UsuarioItem,
)
from app.services import asignacion_service as asignaciones
from app.services.auditoria import ACTIVAR, CREAR, EDITAR, INACTIVAR, registrar_auditoria
from app.services.sesiones import cerrar_sesiones_usuario

logger = logging.getLogger(__name__)

DOCENTE = "Docente"
SUPERVISOR = "Supervisor"
DIRECTIVO = "Directivo"
ROLES = (DOCENTE, SUPERVISOR, DIRECTIVO)
# Alcance que se muestra para las cuentas que no son de Docente.
ALCANCE_GLOBAL = "Global"
CIERRE_POR_DESACTIVACION = "Invalidada por desactivación"
TABLA = "usuario"

# Campos simples de PATCH /usuarios que se copian tal cual a la fila.
CAMPOS_SIMPLES = ("nombres", "apellidos", "dni", "correo")


def nombre_rol(db: Session, id_rol: int) -> str:
    rol = db.get(Rol, id_rol)
    return rol.nombre if rol is not None else ""


# ── Errores ─────────────────────────────────────────────────────────────────────────

def _no_existe() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el usuario.", motivo="usuario_no_encontrado")


def _rol_valido(db: Session, id_rol: int) -> Rol:
    rol = db.get(Rol, id_rol)
    if rol is None:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "No existe el rol indicado.", motivo="rol_inexistente"
        )
    return rol


def _verificar_unicos(
    db: Session, correo: Optional[str], dni: Optional[str], excepto: Optional[int] = None
) -> None:
    """409 si el correo o el DNI ya los usa otra cuenta."""
    for campo, valor, motivo, texto in (
        ("correo", correo, "correo_duplicado", "El correo ya está registrado."),
        ("dni", dni, "dni_duplicado", "El DNI ya está registrado."),
    ):
        if valor is None:
            continue
        otra = db.exec(
            select(Usuario.id_usuario).where(getattr(Usuario, campo) == valor)
        ).first()
        if otra is not None and otra != excepto:
            raise ErrorNegocio(status.HTTP_409_CONFLICT, texto, motivo=motivo)


def _confirmar(db: Session) -> None:
    """Commit que traduce las violaciones de unicidad a 409 en vez de un 500.

    Las validaciones previas cubren el caso normal; esto cubre dos peticiones
    simultáneas que pasan la validación a la vez y chocan en la BD.
    """
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        mensaje = str(error.orig)
        if "docente_colegio_grado" in mensaje:
            raise ErrorNegocio(
                status.HTTP_409_CONFLICT,
                "Uno de los grados ya fue asignado a otro docente en ese periodo.",
                motivo="grado_asignado",
            ) from error
        if "dni" in mensaje:
            raise ErrorNegocio(status.HTTP_409_CONFLICT, "El DNI ya está registrado.", motivo="dni_duplicado") from error
        if "correo" in mensaje:
            raise ErrorNegocio(
                status.HTTP_409_CONFLICT, "El correo ya está registrado.", motivo="correo_duplicado"
            ) from error
        raise


# ── Respuestas ──────────────────────────────────────────────────────────────────────

def _item(usuario: Usuario, rol: str, colegios: dict[int, list[ItemCatalogo]]) -> UsuarioItem:
    if rol == DOCENTE:
        asignados = [c.nombre for c in colegios.get(usuario.id_docente, [])]
    else:
        asignados = [ALCANCE_GLOBAL]
    return UsuarioItem(
        id=usuario.id_usuario,
        nombres=usuario.nombres,
        apellidos=usuario.apellidos,
        dni=usuario.dni,
        correo=usuario.correo,
        rol=rol,
        activo=usuario.activo,
        es_supervisor_original=usuario.es_supervisor_original,
        colegios_asignados=asignados,
    )


def item_de(db: Session, usuario: Usuario) -> UsuarioItem:
    rol = nombre_rol(db, usuario.id_rol)
    ids = [usuario.id_docente] if rol == DOCENTE and usuario.id_docente is not None else []
    return _item(usuario, rol, asignaciones.colegios_vigentes_por_docente(db, ids))


def _usuario(db: Session, id_usuario: int) -> Usuario:
    usuario = db.get(Usuario, id_usuario)
    if usuario is None:
        raise _no_existe()
    return usuario


# ── Listado y detalle ───────────────────────────────────────────────────────────────

def listar_usuarios(
    db: Session, rol: Optional[str], activo: bool, q: Optional[str], page: int
) -> Paginado[UsuarioItem]:
    """Docentes, Supervisores y Directivos, en ese orden; dentro de cada grupo por
    apellidos y nombres. `q` busca en nombres y apellidos sin distinguir mayúsculas."""
    orden_rol = case({DOCENTE: 0, SUPERVISOR: 1, DIRECTIVO: 2}, value=Rol.nombre, else_=3)
    consulta = (
        select(Usuario, Rol.nombre)
        .join(Rol, Rol.id_rol == Usuario.id_rol)
        .where(Usuario.activo == activo)
    )
    if rol is not None:
        consulta = consulta.where(Rol.nombre == rol)
    if q and q.strip():
        texto = q.strip()
        consulta = consulta.where(
            or_(
                col(Usuario.nombres).icontains(texto, autoescape=True),
                col(Usuario.apellidos).icontains(texto, autoescape=True),
                (Usuario.nombres + " " + Usuario.apellidos).icontains(texto, autoescape=True),
            )
        )
    consulta = consulta.order_by(orden_rol, Usuario.apellidos, Usuario.nombres, Usuario.id_usuario)

    filas, total = paginar(db, consulta, page)
    colegios = asignaciones.colegios_vigentes_por_docente(
        db, [u.id_docente for u, r in filas if r == DOCENTE and u.id_docente is not None]
    )
    return pagina([_item(u, r, colegios) for u, r in filas], total, page)


def obtener_usuario(db: Session, id_usuario: int) -> UsuarioDetalle:
    usuario = _usuario(db, id_usuario)
    item = item_de(db, usuario)
    detalle = None
    if item.rol == DOCENTE and usuario.id_docente is not None:
        detalle = asignaciones.detalle_asignacion(db, usuario.id_docente)
    return UsuarioDetalle(**item.model_dump(), asignacion=detalle)


# ── Alta ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UsuarioCreadoResultado:
    usuario: Usuario
    correo_enviado: bool
    contrasena_temporal: Optional[str]


def _exigir_asignacion_segun_rol(rol: str, tiene_asignacion: bool) -> None:
    if rol == DOCENTE and not tiene_asignacion:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La asignación es obligatoria para un Docente.",
            motivo="asignacion_requerida",
        )
    if rol != DOCENTE and tiene_asignacion:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Un {rol} no tiene asignación de colegio.",
            motivo="asignacion_no_permitida",
        )


def crear_usuario(db: Session, data: UsuarioCrear, actor: Usuario) -> UsuarioCreadoResultado:
    """Crea la cuenta (y, si es Docente, su ficha y sus asignaciones) en una transacción.

    Después del commit envía el correo de bienvenida con la contraseña temporal; si no
    sale, la contraseña se devuelve en la respuesta para entregarla por otro medio.
    """
    rol = _rol_valido(db, data.id_rol)
    _exigir_asignacion_segun_rol(rol.nombre, data.asignacion is not None)
    _verificar_unicos(db, data.correo, data.dni)
    plan = None
    if data.asignacion is not None:
        plan = asignaciones.planificar_asignacion_nueva(
            db, data.asignacion.id_colegio, data.asignacion.id_anio_escolar, data.asignacion.grados
        )

    ahora = ahora_utc()
    auditoria = {"creado_por": actor.id_usuario, "creado_en": ahora}
    docente = None
    if plan is not None:
        docente = Docente(nombres=data.nombres, apellidos=data.apellidos, activo=True, **auditoria)
        db.add(docente)
        db.flush()

    contrasena_temporal = generar_contrasena_temporal()
    usuario = Usuario(
        id_rol=rol.id_rol,
        correo=data.correo,
        password_hash=hash_password(contrasena_temporal),
        id_docente=docente.id_docente if docente is not None else None,
        dni=data.dni,
        nombres=data.nombres,
        apellidos=data.apellidos,
        activo=True,
        **auditoria,
    )
    db.add(usuario)
    db.flush()
    if plan is not None:
        asignaciones.reemplazar_asignaciones(db, docente.id_docente, plan, actor.id_usuario, ahora)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=usuario.id_usuario, accion=CREAR,
        id_usuario=actor.id_usuario, fecha=ahora,
    )
    _confirmar(db)
    db.refresh(usuario)

    enviado = enviar_correo_bienvenida_profesor(usuario.correo, contrasena_temporal, usuario.nombres)
    if not enviado:
        logger.warning(
            "Cuenta %s creada sin correo de bienvenida (%s): la contraseña temporal va en la respuesta",
            usuario.id_usuario, enmascarar_correo(usuario.correo),
        )
    return UsuarioCreadoResultado(usuario, enviado, None if enviado else contrasena_temporal)


# ── Edición ─────────────────────────────────────────────────────────────────────────

def _docente_de(db: Session, usuario: Usuario) -> Optional[Docente]:
    return db.get(Docente, usuario.id_docente) if usuario.id_docente is not None else None


def cambiar_rol(
    db: Session,
    usuario: Usuario,
    rol_anterior: str,
    rol_nuevo: Rol,
    asignacion: Optional[AsignacionEditar],
    actor: Usuario,
    ahora,
) -> Optional[str]:
    """Efectos de pasar de Docente a otro rol o al revés (regla provisional, aislada aquí).

    - De Docente a otro rol: se liberan sus grados desde el periodo vigente (las
      asignaciones de periodos terminados quedan como historial) y la ficha `docente`
      se desactiva; `usuario.id_docente` se conserva para no perder el historial.
      Devuelve el resumen de lo liberado, para la auditoría.
    - De otro rol a Docente: la asignación es obligatoria; se crea la ficha `docente` o,
      si ya tenía una, se reactiva. La asignación en sí la aplica el llamador.
    """
    usuario.id_rol = rol_nuevo.id_rol
    if rol_anterior == DOCENTE and rol_nuevo.nombre != DOCENTE:
        docente = _docente_de(db, usuario)
        if docente is None:
            return None
        liberado = asignaciones.liberar_asignaciones(db, docente.id_docente)
        docente.activo = False
        docente.modificado_por = actor.id_usuario
        docente.modificado_en = ahora
        db.add(docente)
        return liberado

    if rol_anterior != DOCENTE and rol_nuevo.nombre == DOCENTE:
        if asignacion is None:
            _exigir_asignacion_segun_rol(DOCENTE, False)
        docente = _docente_de(db, usuario)
        if docente is None:
            docente = Docente(
                nombres=usuario.nombres, apellidos=usuario.apellidos, activo=usuario.activo,
                creado_por=actor.id_usuario, creado_en=ahora,
            )
            db.add(docente)
            db.flush()
            usuario.id_docente = docente.id_docente
        else:
            docente.activo = usuario.activo
            docente.modificado_por = actor.id_usuario
            docente.modificado_en = ahora
            db.add(docente)
    return None


def _aplicar_asignacion(
    db: Session, usuario: Usuario, cambio: AsignacionEditar, actor: Usuario, ahora
) -> tuple[str, str]:
    """Aplica el cambio de asignación y devuelve (antes, después) para la auditoría."""
    plan = asignaciones.planificar_cambio(
        db, usuario.id_docente, cambio.id_colegio, cambio.grados,
        cambio.id_anio_escolar, cambio.desde_periodo,
    )
    antes = asignaciones.resumen_actual(db, usuario.id_docente, plan.periodos, plan.anio)
    asignaciones.reemplazar_asignaciones(db, usuario.id_docente, plan, actor.id_usuario, ahora)
    return antes, plan.resumen()


def _rol_nuevo(db: Session, usuario: Usuario, data: UsuarioEditar, actor: Usuario) -> Optional[Rol]:
    """El rol al que pasa la cuenta, o None si no cambia. Valida que exista, que no sea la
    propia cuenta de quien edita y que no sea la del Supervisor original."""
    if "id_rol" not in data.model_fields_set or data.id_rol == usuario.id_rol:
        return None
    rol = _rol_valido(db, data.id_rol)
    if usuario.id_usuario == actor.id_usuario:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT, "No puede cambiar el rol de su propia cuenta.", motivo="auto_cambio_rol"
        )
    if usuario.es_supervisor_original:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "No se puede cambiar el rol del Supervisor original.",
            motivo="supervisor_original_protegido",
        )
    return rol


def _sincronizar_ficha(db: Session, usuario: Usuario, actor: Usuario, ahora) -> None:
    """Los nombres viven también en la ficha `docente`: se mantienen iguales."""
    docente = _docente_de(db, usuario)
    if docente is None:
        return
    docente.nombres = usuario.nombres
    docente.apellidos = usuario.apellidos
    docente.modificado_por = actor.id_usuario
    docente.modificado_en = ahora
    db.add(docente)


def actualizar_usuario(db: Session, id_usuario: int, data: UsuarioEditar, actor: Usuario) -> UsuarioItem:
    usuario = _usuario(db, id_usuario)
    enviados = data.model_fields_set
    rol_anterior = nombre_rol(db, usuario.id_rol)
    rol_nuevo = _rol_nuevo(db, usuario, data, actor)
    rol_final = rol_nuevo.nombre if rol_nuevo is not None else rol_anterior
    if data.asignacion is not None and rol_final != DOCENTE:
        _exigir_asignacion_segun_rol(rol_final, True)

    _verificar_unicos(
        db,
        data.correo if "correo" in enviados else None,
        data.dni if "dni" in enviados else None,
        excepto=usuario.id_usuario,
    )

    ahora = ahora_utc()
    cambios: dict[str, tuple[Any, Any]] = {}
    for campo in CAMPOS_SIMPLES:
        if campo in enviados:
            cambios[campo] = (getattr(usuario, campo), getattr(data, campo))
            setattr(usuario, campo, getattr(data, campo))

    if rol_nuevo is not None:
        cambios["id_rol"] = (usuario.id_rol, rol_nuevo.id_rol)
        liberado = cambiar_rol(db, usuario, rol_anterior, rol_nuevo, data.asignacion, actor, ahora)
        if liberado is not None:
            cambios["asignacion"] = (liberado, asignaciones.SIN_ASIGNACION)

    if data.asignacion is not None:
        cambios["asignacion"] = _aplicar_asignacion(db, usuario, data.asignacion, actor, ahora)

    if {"nombres", "apellidos"} & enviados:
        _sincronizar_ficha(db, usuario, actor, ahora)

    if registrar_auditoria(
        db, tabla=TABLA, id_registro=usuario.id_usuario, accion=EDITAR, cambios=cambios,
        id_usuario=actor.id_usuario, fecha=ahora,
    ):
        usuario.modificado_por = actor.id_usuario
        usuario.modificado_en = ahora
    db.add(usuario)
    _confirmar(db)
    db.refresh(usuario)
    return item_de(db, usuario)


# ── Activar / desactivar ────────────────────────────────────────────────────────────

def _actualizar_ficha_por_estado(
    db: Session, usuario: Usuario, activo: bool, actor: Usuario, ahora
) -> None:
    """Lleva la ficha `docente` al mismo estado de la cuenta.

    Al desactivar, además libera sus grados desde el periodo vigente (para que otro
    docente pueda tomarlos) y lo deja en la auditoría. Reactivar no restaura las
    asignaciones: el Supervisor las vuelve a asignar con PATCH /usuarios.
    """
    docente = _docente_de(db, usuario)
    if docente is None or nombre_rol(db, usuario.id_rol) != DOCENTE:
        return
    docente.activo = activo
    docente.modificado_por = actor.id_usuario
    docente.modificado_en = ahora
    db.add(docente)
    if not activo:
        liberado = asignaciones.liberar_asignaciones(db, docente.id_docente)
        registrar_auditoria(
            db, tabla=TABLA, id_registro=usuario.id_usuario, accion=EDITAR,
            cambios={"asignacion": (liberado, asignaciones.SIN_ASIGNACION)},
            id_usuario=actor.id_usuario, fecha=ahora,
        )


def cambiar_estado(db: Session, id_usuario: int, activo: bool, actor: Usuario) -> UsuarioItem:
    """Activa o desactiva la cuenta (y su ficha de docente). Idempotente: si ya estaba
    en ese estado, no cambia nada ni deja auditoría."""
    usuario = _usuario(db, id_usuario)
    if not activo and usuario.id_usuario == actor.id_usuario:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT, "No puede desactivar su propia cuenta.", motivo="auto_desactivacion"
        )
    if not activo and usuario.es_supervisor_original:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "No se puede desactivar al Supervisor original.",
            motivo="supervisor_original_protegido",
        )
    if usuario.activo == activo:
        return item_de(db, usuario)

    ahora = ahora_utc()
    usuario.activo = activo
    usuario.modificado_por = actor.id_usuario
    usuario.modificado_en = ahora
    db.add(usuario)
    _actualizar_ficha_por_estado(db, usuario, activo, actor, ahora)
    if not activo:
        cerrar_sesiones_usuario(db, usuario.id_usuario, CIERRE_POR_DESACTIVACION)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=usuario.id_usuario, accion=ACTIVAR if activo else INACTIVAR,
        id_usuario=actor.id_usuario, fecha=ahora,
    )
    _confirmar(db)
    db.refresh(usuario)
    return item_de(db, usuario)
