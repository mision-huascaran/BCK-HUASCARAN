"""Sesiones autenticadas (tabla `sesion`, CU003, CU006, CU007, CU008).

Cada login crea una fila en `sesion` y el JWT lleva su id en el claim `jti`. El token
solo vale mientras su sesión siga abierta (`fin IS NULL`) y vigente (`expira > ahora`):
así el logout, el restablecimiento de contraseña y la expiración cierran la sesión de
verdad, y no solo en el navegador.

Una sesión dura exactamente DURACION_SESION desde el login y nunca se renueva.

Ninguna función hace commit: participan en la transacción de quien las llama.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, literal, update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from app.core.security import create_access_token
from app.core.tiempo import UTCDateTime, ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad

# Regla de negocio (CU003, CU007), no configuración: por eso no viene del .env.
DURACION_SESION = timedelta(hours=8)

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

def _cerrar_actividades(
    db: Session, ids_sesion, fin: ColumnElement, tipo_cierre: str, ahora: datetime
) -> None:
    """Cierra las actividades activas de las sesiones indicadas, en un solo UPDATE.

    `ids_sesion` es una lista de ids o un SELECT de ids; `fin` es la expresión SQL del
    instante de cierre. Solo los Docentes tienen actividades: para el resto no toca filas.
    Si el cliente registró un inicio posterior al cierre (reloj adelantado), el cierre
    usa el inicio para no violar el CHECK fin >= inicio y no tumbar la operación.
    """
    db.execute(
        update(Actividad)
        .where(col(Actividad.id_sesion).in_(ids_sesion), col(Actividad.fin).is_(None))
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
        db, ids_sesion, literal(ahora, UTCDateTime()), CIERRE_ACTIVIDAD_FORZADO, ahora
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
        select(Sesion.id_sesion).where(*filtro),
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
