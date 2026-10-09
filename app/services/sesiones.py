"""Sesiones autenticadas (tabla `sesion`, CU003, CU006, CU007, CU008).

Cada login crea una fila en `sesion` y el JWT lleva su id en el claim `jti`. El token
solo vale mientras su sesión siga abierta (`fin IS NULL`) y vigente (`expira > ahora`):
así el logout, el restablecimiento de contraseña y la expiración cierran la sesión de
verdad, y no solo en el navegador.

Una sesión dura exactamente DURACION_SESION desde el login y nunca se renueva.

Sin conexión (CU008), el "Cerrar sesión" queda guardado en el cliente y llega después
de un nuevo login con la hora real (`cerrar_sesion_diferida`): se registra como cierre
manual en ese instante, aunque el servidor ya la hubiera cerrado por expiración.

Ninguna función hace commit: participan en la transacción de quien las llama.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import status
from sqlalchemy import and_, case, literal, update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.security import create_access_token
from app.core.tiempo import UTCDateTime, ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad

# Regla de negocio (CU003, CU007), no configuración: por eso no viene del .env.
DURACION_SESION = timedelta(hours=8)

# Tolerancia para el reloj del cliente: una hora enviada hasta 2 minutos en el futuro
# se acepta (relojes que se adelantan un poco); más allá es un dato inválido.
MARGEN_RELOJ = timedelta(minutes=2)

CIERRE_MANUAL = "Manual"
CIERRE_POR_EXPIRACION = "Automático por expiración"
CIERRE_POR_RESTABLECIMIENTO = "Invalidada por restablecimiento de contraseña"

CIERRE_ACTIVIDAD_MANUAL = "Manual por finalización de actividad"
CIERRE_ACTIVIDAD_FORZADO = "Forzado por cierre de sesión"
CIERRE_ACTIVIDAD_POR_EXPIRACION = "Automático por expiración de sesión"


# ── Apertura ────────────────────────────────────────────────────────────────────────

def crear_sesion_y_token(db: Session, usuario: Usuario) -> tuple[str, Sesion]:
    """Abre una sesión para el usuario y devuelve su JWT. La usa el login.

    No hace commit: el token solo sirve cuando la fila de `sesion` queda guardada, así
    que quien llama debe confirmar la transacción. También sirve para que las pruebas
    obtengan un token válido sin pasar por POST /login:

        token, _ = crear_sesion_y_token(session, usuario)
        session.commit()
        headers = {"Authorization": f"Bearer {token}"}

    Los instantes se truncan al segundo: el claim `exp` del JWT es un entero de
    segundos y debe coincidir exactamente con `sesion.expira`.
    """
    inicio = ahora_utc().replace(microsecond=0)
    sesion = Sesion(
        id_sesion=uuid.uuid4(),
        id_usuario=usuario.id_usuario,
        inicio=inicio,
        expira=inicio + DURACION_SESION,
    )
    db.add(sesion)
    db.flush()
    token = create_access_token(
        {
            "sub": str(usuario.id_usuario),
            "jti": str(sesion.id_sesion),
            "iat": int(sesion.inicio.timestamp()),
            "exp": int(sesion.expira.timestamp()),
        }
    )
    return token, sesion


# ── Cierre ──────────────────────────────────────────────────────────────────────────

def _en_curso(ids_sesion) -> ColumnElement[bool]:
    """Actividades activas de las sesiones indicadas (lista de ids o SELECT de ids)."""
    return and_(col(Actividad.id_sesion).in_(ids_sesion), col(Actividad.fin).is_(None))


def _cerrar_actividades(
    db: Session, cuales: ColumnElement[bool], fin: ColumnElement, tipo_cierre: str, ahora: datetime
) -> None:
    """Cierra las actividades que cumplen `cuales`, en un solo UPDATE.

    `fin` es la expresión SQL del instante de cierre. Solo los Docentes tienen
    actividades: para el resto no toca filas. Si el cliente registró un inicio posterior
    al cierre (reloj adelantado), el cierre usa el inicio para no violar el CHECK
    fin >= inicio y no tumbar la operación.
    """
    db.execute(
        update(Actividad)
        .where(cuales)
        .values(
            fin=case((col(Actividad.inicio) > fin, col(Actividad.inicio)), else_=fin),
            tipo_cierre=tipo_cierre,
            sincronizado_en=ahora,
        )
        .execution_options(synchronize_session=False)
    )


def _cerrar_ahora(db: Session, ids_sesion: list[uuid.UUID], tipo_cierre: str) -> None:
    """Cierra esas sesiones en este instante y fuerza el cierre de sus actividades."""
    ahora = ahora_utc()
    db.execute(
        update(Sesion)
        .where(col(Sesion.id_sesion).in_(ids_sesion))
        .values(fin=ahora, tipo_cierre=tipo_cierre)
        .execution_options(synchronize_session=False)
    )
    _cerrar_actividades(
        db, _en_curso(ids_sesion), literal(ahora, UTCDateTime()), CIERRE_ACTIVIDAD_FORZADO, ahora
    )


def cerrar_sesiones_usuario(
    db: Session,
    id_usuario: int,
    tipo_cierre: str,
    excepto_id_sesion: Optional[uuid.UUID] = None,
) -> int:
    """Cierra todas las sesiones abiertas del usuario y las actividades activas en ellas.

    `excepto_id_sesion` deja abierta una sesión (la del propio usuario cuando cambia su
    contraseña estando dentro). Devuelve cuántas sesiones cerró.
    """
    filtro = [Sesion.id_usuario == id_usuario, col(Sesion.fin).is_(None)]
    if excepto_id_sesion is not None:
        filtro.append(Sesion.id_sesion != excepto_id_sesion)
    ids_sesion = list(db.exec(select(Sesion.id_sesion).where(*filtro).with_for_update()).all())
    if ids_sesion:
        _cerrar_ahora(db, ids_sesion, tipo_cierre)
    return len(ids_sesion)


def cerrar_sesiones_vencidas(db: Session, id_usuario: Optional[int] = None) -> int:
    """Cierra las sesiones abiertas cuyo vencimiento ya pasó, con UPDATE en bloque.

    La sesión se cierra en el instante en que venció (`fin = expira`), no en el momento
    en que el servidor lo nota, y sus actividades activas también (`fin = expira` de su
    sesión). Con `id_usuario` se limita a ese usuario; sin él, cierra las de todos (para
    calcular indicadores sobre datos al día). Devuelve cuántas sesiones cerró.
    """
    ahora = ahora_utc()
    filtro = [col(Sesion.fin).is_(None), col(Sesion.expira) <= ahora]
    if id_usuario is not None:
        filtro.append(Sesion.id_usuario == id_usuario)

    # Primero las actividades: después del UPDATE de `sesion` el filtro ya no las vería.
    vencimiento_de_su_sesion = (
        select(Sesion.expira).where(Sesion.id_sesion == Actividad.id_sesion).scalar_subquery()
    )
    _cerrar_actividades(
        db,
        _en_curso(select(Sesion.id_sesion).where(*filtro)),
        vencimiento_de_su_sesion,
        CIERRE_ACTIVIDAD_POR_EXPIRACION,
        ahora,
    )
    resultado = db.execute(
        update(Sesion)
        .where(*filtro)
        .values(fin=Sesion.expira, tipo_cierre=CIERRE_POR_EXPIRACION)
        .execution_options(synchronize_session=False)
    )
    return resultado.rowcount


def cerrar_sesion_por_logout(db: Session, id_usuario: int, id_sesion: uuid.UUID) -> None:
    """Logout idempotente (CU003).

    - Abierta y vigente: se cierra ahora como 'Manual' (y su actividad, como forzada).
    - Abierta pero ya vencida: se cierra como vencida, en el instante en que venció.
    - Ya cerrada, inexistente o de otro usuario: no hace nada.
    """
    sesion = db.exec(
        select(Sesion)
        .where(Sesion.id_sesion == id_sesion, Sesion.id_usuario == id_usuario)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if sesion is None or sesion.fin is not None:
        return
    if sesion.expira <= ahora_utc():
        cerrar_sesiones_vencidas(db, id_usuario)
        return
    _cerrar_ahora(db, [id_sesion], CIERRE_MANUAL)


# ── Cierre diferido (CU008, CU009: "Cerrar sesión" sin conexión) ──────────────────────

def sesion_propia_bloqueada(db: Session, id_usuario: int, id_sesion: uuid.UUID) -> Sesion:
    """La sesión `id_sesion` del usuario, bloqueada (FOR UPDATE) para que un logout, una
    invalidación o un registro simultáneos sobre ella esperen y no se crucen. Abierta,
    cerrada o vencida. Inexistente o ajena: el mismo 422, para no revelar sesiones ajenas."""
    sesion = db.exec(
        select(Sesion)
        .where(Sesion.id_sesion == id_sesion, Sesion.id_usuario == id_usuario)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if sesion is None:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La sesión indicada no existe.",
            motivo="id_sesion_invalido",
        )
    return sesion


def _validar_fin_de_sesion(sesion: Sesion, fin: datetime, ahora: datetime) -> datetime:
    """`fin` dentro de la sesión: desde su inicio hasta su vencimiento, y no más allá de
    la hora actual más el margen de reloj."""
    fin = fin.astimezone(timezone.utc)
    if not sesion.inicio <= fin <= min(sesion.expira, ahora + MARGEN_RELOJ):
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La hora de cierre debe estar entre el inicio de la sesión y su vencimiento, "
            "y no ser posterior a la hora actual.",
            motivo="fin_invalido",
        )
    return fin


def _actividad_forzada(db: Session, id_sesion: uuid.UUID) -> Optional[Actividad]:
    """La actividad de la sesión que terminó con el cierre de la sesión, si la hay."""
    return db.exec(
        select(Actividad)
        .where(Actividad.id_sesion == id_sesion, Actividad.tipo_cierre == CIERRE_ACTIVIDAD_FORZADO)
        .order_by(col(Actividad.inicio).desc())
        .limit(1)
        .execution_options(populate_existing=True)
    ).first()


def cerrar_sesion_diferida(
    db: Session, id_usuario: int, id_sesion_actual: uuid.UUID, id_sesion: uuid.UUID, fin: datetime
) -> tuple[Sesion, Optional[Actividad]]:
    """Registra un "Cerrar sesión" hecho sin conexión, con su hora real `fin` (CU008).

    Llega con el token de una sesión posterior. Según el estado de `id_sesion` (después
    de cerrar las vencidas del usuario, para evaluar el estado real):

    - Abierta y vigente: se cierra en `fin` como 'Manual', y su actividad activa, como
      forzada por cierre de sesión en ese instante (o en su inicio, si es posterior).
    - Cerrada por expiración y `fin` anterior al vencimiento: se corrige a `fin` y
      'Manual', y sus actividades cerradas por expiración pasan a forzadas en `fin`.
    - Ya cerrada como 'Manual' (el logout sí llegó, o es un reintento) o invalidada
      (restablecimiento de contraseña, desactivación): sin cambios. La invalidación es
      un evento real que registró el servidor y no se reemplaza.

    Una actividad finalizada por el docente nunca se toca. Converge con el registro de
    actividades en otra sesión (`actividad._registrar_en_otra_sesion`) en cualquier
    orden: lo que llega después hereda (o corrige) el cierre de lo que llegó antes.
    `sincronizado_en` de las actividades tocadas es la hora de llegada, aparte de `fin`.

    Devuelve la sesión y la actividad que terminó con el cierre de la sesión (por esta
    petición o por una anterior), o None. Quien llama confirma todo en un solo commit.
    """
    if id_sesion == id_sesion_actual:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La sesión indicada es la sesión actual: para cerrarla use Cerrar sesión.",
            motivo="sesion_actual",
        )
    cerrar_sesiones_vencidas(db, id_usuario)
    sesion = sesion_propia_bloqueada(db, id_usuario, id_sesion)
    ahora = ahora_utc()
    fin = _validar_fin_de_sesion(sesion, fin, ahora)
    instante = literal(fin, UTCDateTime())

    if sesion.fin is None:
        _cerrar_actividades(db, _en_curso([id_sesion]), instante, CIERRE_ACTIVIDAD_FORZADO, ahora)
        sesion.fin, sesion.tipo_cierre = fin, CIERRE_MANUAL
    elif sesion.tipo_cierre == CIERRE_POR_EXPIRACION and fin < sesion.fin:
        cerradas_por_expiracion = and_(
            Actividad.id_sesion == id_sesion,
            Actividad.tipo_cierre == CIERRE_ACTIVIDAD_POR_EXPIRACION,
            col(Actividad.fin) > fin,
        )
        _cerrar_actividades(db, cerradas_por_expiracion, instante, CIERRE_ACTIVIDAD_FORZADO, ahora)
        sesion.fin, sesion.tipo_cierre = fin, CIERRE_MANUAL
    db.add(sesion)
    db.flush()
    return sesion, _actividad_forzada(db, id_sesion)
