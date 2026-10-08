"""Control de abuso por correo (tabla `control_acceso_correo`, CU002, CU005, CU001).

Se aplica igual a correos registrados, no registrados y cuentas desactivadas: la fila se
crea con el correo tal como llegó (normalizado), exista o no la cuenta, para que el
comportamiento no revele qué correos tienen cuenta.

Dos controles independientes sobre la misma fila:

- Límite de solicitudes de PIN: 5 por ventana fija de una hora.
- Bloqueo por intentos fallidos: 5 fallos consecutivos bloquean el correo 15 minutos.
  Hoy lo usa la Recovery Key; en la Tanda 2 lo usará también el login.

Ninguna función hace commit: participan en la transacción de quien las llama.
"""
from datetime import timedelta

from fastapi import status
from sqlalchemy import case, literal, or_
from sqlmodel import Session, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.tiempo import UTCDateTime, ahora_utc
from app.models.seguridad import ControlAccesoCorreo

MAX_SOLICITUDES_PIN = 5
VENTANA_SOLICITUDES_PIN = timedelta(hours=1)

MAX_INTENTOS_FALLIDOS = 5
DURACION_BLOQUEO = timedelta(minutes=15)
MOTIVO_BLOQUEO = "bloqueo_temporal"
MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Por seguridad, intente nuevamente en 15 minutos."

_tabla = ControlAccesoCorreo.__table__


def _insert(db: Session):
    return insert_con_conflicto(db, _tabla)


# ── Límite de solicitudes de PIN ────────────────────────────────────────────────────

def registrar_solicitud_pin(db: Session, correo: str) -> bool:
    """Cuenta una solicitud de PIN para el correo. True si sigue dentro del límite.

    Una sola sentencia INSERT ... ON CONFLICT DO UPDATE ... RETURNING: la base de datos
    lee y escribe la fila de forma atómica, así que dos solicitudes simultáneas nunca
    leen el mismo valor del contador. Si la ventana no existe o ya pasó una hora, se
    reinicia en `ahora` con el contador en 1; si no, el contador suma 1.
    """
    ahora = ahora_utc()
    ventana_vencida = or_(
        _tabla.c.ventana_pin_inicio.is_(None),
        _tabla.c.ventana_pin_inicio <= literal(ahora - VENTANA_SOLICITUDES_PIN, UTCDateTime()),
    )
    sentencia = _insert(db).values(correo=correo, solicitudes_pin=1, ventana_pin_inicio=ahora)
    sentencia = sentencia.on_conflict_do_update(
        index_elements=[_tabla.c.correo],
        set_={
            "solicitudes_pin": case((ventana_vencida, 1), else_=_tabla.c.solicitudes_pin + 1),
            "ventana_pin_inicio": case(
                (ventana_vencida, literal(ahora, UTCDateTime())),
                else_=_tabla.c.ventana_pin_inicio,
            ),
        },
    ).returning(_tabla.c.solicitudes_pin)
    solicitudes = db.execute(sentencia).scalar_one()
    return solicitudes <= MAX_SOLICITUDES_PIN


# ── Bloqueo por intentos fallidos ───────────────────────────────────────────────────

def bloquear_y_verificar(db: Session, correo: str) -> ControlAccesoCorreo:
    """Toma la fila del correo con bloqueo de escritura y verifica que no esté bloqueada.

    La fila queda bloqueada (SELECT ... FOR UPDATE) hasta el commit o rollback de quien
    llama, así los intentos sobre un mismo correo se evalúan de a uno y no se pueden
    lanzar en paralelo para probar más de 5 valores antes del bloqueo. SQLite (tests)
    ignora FOR UPDATE, pero ahí ya hay un único escritor.

    Si el bloqueo anterior ya venció, el contador vuelve a 0 antes de evaluar el intento.
    Con un bloqueo vigente lanza 429 sin evaluar nada más.
    """
    db.execute(_insert(db).values(correo=correo).on_conflict_do_nothing(index_elements=[_tabla.c.correo]))
    control = db.exec(
        select(ControlAccesoCorreo)
        .where(ControlAccesoCorreo.correo == correo)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()

    if control.bloqueado_hasta is not None:
        if control.bloqueado_hasta > ahora_utc():
            raise ErrorNegocio(status.HTTP_429_TOO_MANY_REQUESTS, MENSAJE_BLOQUEO, motivo=MOTIVO_BLOQUEO)
        control.intentos_fallidos = 0
        control.bloqueado_hasta = None
        db.add(control)
    return control


def registrar_intento_fallido(db: Session, control: ControlAccesoCorreo) -> None:
    """Suma un fallo; al llegar al máximo, bloquea el correo durante DURACION_BLOQUEO."""
    ahora = ahora_utc()
    control.intentos_fallidos += 1
    control.ultimo_intento_fallido = ahora
    if control.intentos_fallidos >= MAX_INTENTOS_FALLIDOS:
        control.bloqueado_hasta = ahora + DURACION_BLOQUEO
    db.add(control)


def registrar_intento_exitoso(db: Session, control: ControlAccesoCorreo) -> None:
    control.intentos_fallidos = 0
    control.bloqueado_hasta = None
    db.add(control)
