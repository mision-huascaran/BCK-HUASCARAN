"""Cambio y recuperación de contraseña con PIN (CU005, CU006).

Reglas del PIN: 6 dígitos, 15 minutos de vigencia, 5 intentos fallidos como máximo y uno
solo vigente por cuenta. Deja de servir con lo primero que ocurra: vencimiento, quinto
fallo, uso exitoso o reemplazo por uno nuevo. En la BD se guarda su HMAC (ver app/core/pin.py).

Agotar los intentos deja la cuenta en estado "agotado" (sin PIN y con el contador en el
máximo) hasta que se pida un PIN nuevo: aunque el usuario recargue la página, cada
verificación responde `intentos_agotados` (CU006). Pedir un PIN nuevo, usarlo con éxito o
que venza dejan el contador en 0.

Los errores se lanzan como `ErrorNegocio`, con un `motivo` estable para el frontend.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import status
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.email import (
    enmascarar_correo,
    enviar_correo_codigo_verificacion,
    enviar_correo_confirmacion_cambio,
)
from app.core.correo import normalizar_correo
from app.core.errores import ErrorNegocio
from app.core.pin import MAX_INTENTOS_PIN, VIGENCIA_PIN, generar_pin, hmac_pin, pin_coincide
from app.core.politica_contrasena import DESCRIPCIONES, requisitos_incumplidos
from app.core.security import hash_password, verify_password
from app.core.tiempo import ahora_utc
from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION
from app.models.organizacion import Usuario
from app.services.control_acceso import registrar_solicitud_pin
from app.services.sesiones import CIERRE_POR_RESTABLECIMIENTO, cerrar_sesiones_usuario

logger = logging.getLogger(__name__)

PIN_INCORRECTO = "incorrecto"
PIN_EXPIRADO = "expirado"
PIN_INTENTOS_AGOTADOS = "intentos_agotados"

MENSAJES_PIN = {
    PIN_INCORRECTO: "El código ingresado es incorrecto. Inténtalo nuevamente.",
    PIN_EXPIRADO: "El código ha expirado. Solicita un nuevo PIN.",
    PIN_INTENTOS_AGOTADOS: "Has agotado los intentos permitidos. Solicita un nuevo PIN para continuar.",
}

MENSAJE_PIN_ENVIADO = "Si el correo está registrado y activo, recibirás un PIN."
MENSAJE_PIN_ENVIADO_CON_SESION = "Se envió un PIN a tu correo."
MENSAJE_CONTRASENA_ACTUALIZADA = "Contraseña actualizada correctamente."
MENSAJE_NO_COINCIDEN = "Las contraseñas no coinciden."

# Campos de las peticiones de contraseña nueva (los mismos en los tres flujos).
CAMPO_NUEVA = "contraseña_nueva"
CAMPO_CONFIRMACION = "confirmar_contraseña_nueva"


# ── Correo de la cuenta ─────────────────────────────────────────────────────────────

def buscar_usuario_para_actualizar(db: Session, correo: str) -> Optional[Usuario]:
    """Usuario con ese correo, con su fila bloqueada hasta el fin de la transacción.

    El bloqueo (FOR UPDATE) hace que dos verificaciones simultáneas del PIN se evalúen
    de a una: sin él, varias peticiones en paralelo podrían probar más de 5 PIN antes de
    que el contador de intentos las alcance. Igualdad exacta: el correo se guarda
    normalizado (CHECK ck_usuario_correo_normalizado), así la búsqueda usa el índice.
    """
    return db.exec(
        select(Usuario)
        .where(Usuario.correo == normalizar_correo(correo))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _bloquear_usuario(db: Session, usuario: Usuario) -> Usuario:
    return db.exec(
        select(Usuario)
        .where(Usuario.id_usuario == usuario.id_usuario)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


# ── PIN ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EnvioPin:
    """Lo necesario para enviar el PIN después de responder (BackgroundTasks)."""

    id_usuario: int
    correo: str
    nombres: str
    pin: str
    pin_hmac: str


def _invalidar_pin(usuario: Usuario) -> None:
    """Sin PIN y con el contador en 0 (vencido, usado con éxito o descartado)."""
    usuario.codigo_verificacion = None
    usuario.codigo_verificacion_expira = None
    usuario.codigo_verificacion_intentos = 0


def _agotar_pin(usuario: Usuario) -> None:
    """Sin PIN, pero con el contador en el máximo: así se sigue respondiendo
    `intentos_agotados` hasta que se pida uno nuevo (`_asignar_pin_nuevo` lo vuelve a 0)."""
    usuario.codigo_verificacion = None
    usuario.codigo_verificacion_expira = None
    usuario.codigo_verificacion_intentos = MAX_INTENTOS_PIN


def _agotado(usuario: Usuario) -> bool:
    return usuario.codigo_verificacion_intentos >= MAX_INTENTOS_PIN


def _asignar_pin_nuevo(usuario: Usuario) -> EnvioPin:
    """Genera un PIN y reemplaza al anterior (si había uno, deja de servir)."""
    pin = generar_pin()
    pin_hmac = hmac_pin(usuario.id_usuario, pin)
    usuario.codigo_verificacion = pin_hmac
    usuario.codigo_verificacion_expira = ahora_utc() + VIGENCIA_PIN
    usuario.codigo_verificacion_intentos = 0
    return EnvioPin(usuario.id_usuario, usuario.correo, usuario.nombres, pin, pin_hmac)


def _error_pin(motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_400_BAD_REQUEST, MENSAJES_PIN[motivo], motivo=motivo)


def _rechazar_pin(
    db: Session, usuario: Usuario, motivo: str, cambio: Optional[Callable[[Usuario], None]] = None
) -> ErrorNegocio:
    """Guarda el intento (y el `cambio` del PIN, si hay) aunque la petición termine en error."""
    if cambio is not None:
        cambio(usuario)
    db.add(usuario)
    db.commit()
    return _error_pin(motivo)


def validar_pin(db: Session, usuario: Optional[Usuario], codigo: str) -> None:
    """Verifica el PIN sin consumirlo. Lanza ErrorNegocio si no sirve.

    `usuario` debe venir bloqueado (ver buscar_usuario_para_actualizar). Orden:
    1. Sin cuenta o cuenta desactivada: `incorrecto`, sin contar intento (no revela si la
       cuenta existe).
    2. Sin PIN vigente: `intentos_agotados` si se agotaron (hasta que pida otro); si no,
       `incorrecto`, sin contar intento.
    3. Vencido: se invalida (contador en 0) y responde `expirado`.
    4. Ya tiene el máximo de intentos: se agota y responde `intentos_agotados`.
    5. No coincide: suma un intento; al llegar al máximo se agota (`intentos_agotados`),
       si no, `incorrecto`. Un PIN reemplazado por otro simplemente no coincide.
    """
    if usuario is None or not usuario.activo:
        raise _error_pin(PIN_INCORRECTO)

    if usuario.codigo_verificacion is None or usuario.codigo_verificacion_expira is None:
        raise _error_pin(PIN_INTENTOS_AGOTADOS if _agotado(usuario) else PIN_INCORRECTO)

    if usuario.codigo_verificacion_expira <= ahora_utc():
        raise _rechazar_pin(db, usuario, PIN_EXPIRADO, _invalidar_pin)

    if _agotado(usuario):
        raise _rechazar_pin(db, usuario, PIN_INTENTOS_AGOTADOS, _agotar_pin)

    if not pin_coincide(usuario.id_usuario, codigo, usuario.codigo_verificacion):
        usuario.codigo_verificacion_intentos += 1
        if _agotado(usuario):
            raise _rechazar_pin(db, usuario, PIN_INTENTOS_AGOTADOS, _agotar_pin)
        raise _rechazar_pin(db, usuario, PIN_INCORRECTO)


def descartar_pin_no_enviado(db: Session, envio: EnvioPin) -> None:
    """Invalida el PIN cuyo correo no salió, solo si sigue siendo el guardado.

    Si mientras tanto se generó otro PIN (otra solicitud), el WHERE no encuentra la fila
    y el PIN nuevo queda intacto.
    """
    resultado = db.execute(
        update(Usuario)
        .where(Usuario.id_usuario == envio.id_usuario, Usuario.codigo_verificacion == envio.pin_hmac)
        .values(codigo_verificacion=None, codigo_verificacion_expira=None, codigo_verificacion_intentos=0)
    )
    db.commit()
    if resultado.rowcount:
        logger.warning(
            "PIN invalidado: no se pudo enviar el correo a %s", enmascarar_correo(envio.correo)
        )


def enviar_pin(fabrica_sesion: Callable[[], Session], envio: EnvioPin) -> None:
    """Tarea en segundo plano: envía el PIN y, si el correo falla, lo invalida.

    Usa una sesión propia: la de la petición ya se cerró cuando corre la tarea.
    """
    if enviar_correo_codigo_verificacion(envio.correo, envio.pin, envio.nombres):
        return
    with fabrica_sesion() as db:
        descartar_pin_no_enviado(db, envio)


def enviar_confirmacion_cambio(correo: str, nombres: str) -> None:
    """Aviso best-effort: la contraseña ya cambió; si el correo falla, queda en el log."""
    enviar_correo_confirmacion_cambio(correo, nombres)


def solicitar_pin_publico(db: Session, correo: str) -> Optional[EnvioPin]:
    """Primer paso del flujo público. Devuelve el PIN a enviar, o None si no corresponde.

    La solicitud cuenta para el límite exista o no la cuenta. Solo se genera PIN si la
    cuenta existe, está activa y no se superó el límite; en cualquier caso el endpoint
    responde lo mismo.
    """
    dentro_del_limite = registrar_solicitud_pin(db, correo)
    usuario = buscar_usuario_para_actualizar(db, correo)
    if not dentro_del_limite:
        logger.info("Límite de solicitudes de PIN alcanzado para %s", enmascarar_correo(correo))
    if not dentro_del_limite or usuario is None or not usuario.activo:
        db.commit()
        return None

    envio = _asignar_pin_nuevo(usuario)
    db.add(usuario)
    db.commit()
    return envio


def solicitar_pin_con_sesion(db: Session, usuario_actual: Usuario) -> None:
    """Envía un PIN al correo del usuario que ya inició sesión.

    Respeta el mismo límite de 5 solicitudes por hora del correo. Aquí el envío es
    síncrono: el usuario ya está identificado y necesita saber si el correo salió.
    """
    if not registrar_solicitud_pin(db, normalizar_correo(usuario_actual.correo)):
        db.commit()
        raise ErrorNegocio(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Se alcanzó el límite de solicitudes de PIN. Inténtalo nuevamente más tarde.",
            motivo="limite_solicitudes",
        )
    usuario = _bloquear_usuario(db, usuario_actual)
    envio = _asignar_pin_nuevo(usuario)
    db.add(usuario)
    db.commit()

    if not enviar_correo_codigo_verificacion(envio.correo, envio.pin, envio.nombres):
        descartar_pin_no_enviado(db, envio)
        raise ErrorNegocio(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No pudimos enviar el código, intenta de nuevo",
            motivo="envio_fallido",
        )


def verificar_pin_publico(db: Session, correo: str, codigo: str) -> None:
    validar_pin(db, buscar_usuario_para_actualizar(db, correo), codigo)


def verificar_pin_con_sesion(db: Session, usuario_actual: Usuario, codigo: str) -> None:
    validar_pin(db, _bloquear_usuario(db, usuario_actual), codigo)


# ── Contraseña nueva ────────────────────────────────────────────────────────────────

def _error_validacion(errores: list[tuple[str, str]], detail: str = MENSAJE_GENERAL, **extra) -> ErrorNegocio:
    """422 con el formato único de validación (app/core/validacion.py)."""
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail,
        motivo=MOTIVO_VALIDACION,
        extra={"errores": [{"campo": campo, "mensaje": mensaje} for campo, mensaje in errores], **extra},
    )


def validar_contrasena_nueva(nueva: str, confirmacion: str, password_hash_actual: str) -> None:
    """Coincidencia, política de CU006 y distinta de la actual, en ese orden.

    Los dos primeros salen como 422 `validacion`, con un error por campo: la política,
    uno por requisito incumplido, y además `requisitos_incumplidos` con sus códigos.
    """
    if nueva != confirmacion:
        raise _error_validacion([(CAMPO_CONFIRMACION, MENSAJE_NO_COINCIDEN)], detail=MENSAJE_NO_COINCIDEN)

    incumplidos = requisitos_incumplidos(nueva)
    if incumplidos:
        raise _error_validacion(
            [(CAMPO_NUEVA, DESCRIPCIONES[codigo].capitalize() + ".") for codigo in incumplidos],
            detail="La contraseña no cumple la política de seguridad.",
            requisitos_incumplidos=incumplidos,
        )

    if verify_password(nueva, password_hash_actual):
        raise ErrorNegocio(
            status.HTTP_400_BAD_REQUEST,
            "La nueva contraseña debe ser diferente de la contraseña anterior.",
            motivo="password_igual",
        )


def aplicar_contrasena_nueva(
    db: Session,
    usuario: Usuario,
    nueva: str,
    excepto_id_sesion: Optional[uuid.UUID] = None,
) -> None:
    """Cambia el hash, invalida el PIN, quita el cambio obligatorio de la contraseña
    temporal (CU016) y cierra las sesiones abiertas. No hace commit."""
    usuario.password_hash = hash_password(nueva)
    usuario.debe_cambiar_contrasena = False
    usuario.contrasena_temporal_expira = None
    _invalidar_pin(usuario)
    usuario.modificado_por = usuario.id_usuario
    usuario.modificado_en = ahora_utc()
    db.add(usuario)
    cerrar_sesiones_usuario(
        db, usuario.id_usuario, CIERRE_POR_RESTABLECIMIENTO, excepto_id_sesion=excepto_id_sesion
    )


def confirmar_cambio(db: Session) -> None:
    """Commit del cambio de contraseña: o se guarda todo, o nada (CU006)."""
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Error al actualizar la contraseña")
        raise ErrorNegocio(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error al actualizar la contraseña. Inténtalo nuevamente.",
            motivo="error_actualizacion",
        ) from error


def restablecer_contrasena(
    db: Session, correo: str, codigo: str, nueva: str, confirmacion: str
) -> Usuario:
    """Cierre del flujo público: PIN válido + contraseña nueva, en una sola transacción.

    Los errores de la contraseña nueva no consumen intentos del PIN.
    """
    usuario = buscar_usuario_para_actualizar(db, correo)
    validar_pin(db, usuario, codigo)
    validar_contrasena_nueva(nueva, confirmacion, usuario.password_hash)
    aplicar_contrasena_nueva(db, usuario, nueva)
    confirmar_cambio(db)
    return usuario


def cambiar_contrasena(
    db: Session,
    usuario_actual: Usuario,
    codigo: str,
    nueva: str,
    confirmacion: str,
    id_sesion_actual: Optional[uuid.UUID] = None,
) -> Usuario:
    """Cambio con sesión iniciada: mismas reglas; cierra las demás sesiones del usuario."""
    usuario = _bloquear_usuario(db, usuario_actual)
    validar_pin(db, usuario, codigo)
    validar_contrasena_nueva(nueva, confirmacion, usuario.password_hash)
    aplicar_contrasena_nueva(db, usuario, nueva, excepto_id_sesion=id_sesion_actual)
    confirmar_cambio(db)
    return usuario
