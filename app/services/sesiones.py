"""Sesiones autenticadas (tabla `sesion`, CU003, CU006, CU007, CU008).

Hoy el login todavía no crea filas en `sesion` (Tanda 2), así que cerrar las sesiones de
un usuario no encuentra nada que cerrar. La función ya queda en uso en los flujos que
cambian la contraseña para que, cuando existan sesiones, se invaliden sin tocar esos flujos.
"""
import uuid
from typing import Optional

from sqlalchemy import case, literal, update
from sqlmodel import Session, col, select

from app.core.tiempo import UTCDateTime, ahora_utc
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad

CIERRE_POR_RESTABLECIMIENTO = "Invalidada por restablecimiento de contraseña"
CIERRE_ACTIVIDAD_FORZADO = "Forzado por cierre de sesión"


def cerrar_sesiones_usuario(
    db: Session,
    id_usuario: int,
    tipo_cierre: str,
    excepto_id_sesion: Optional[uuid.UUID] = None,
) -> int:
    """Cierra todas las sesiones abiertas del usuario y las actividades activas en ellas.

    `excepto_id_sesion` deja abierta una sesión (la del propio usuario cuando cambia su
    contraseña estando dentro). No hace commit: participa en la transacción de quien
    llama, para que el cambio de contraseña y el cierre de sesiones se guarden juntos o
    no se guarde ninguno. Devuelve cuántas sesiones cerró.
    """
    filtro = [Sesion.id_usuario == id_usuario, col(Sesion.fin).is_(None)]
    if excepto_id_sesion is not None:
        filtro.append(Sesion.id_sesion != excepto_id_sesion)
    ids_sesion = list(db.exec(select(Sesion.id_sesion).where(*filtro).with_for_update()).all())
    if not ids_sesion:
        return 0

    ahora = ahora_utc()
    db.execute(
        update(Sesion)
        .where(col(Sesion.id_sesion).in_(ids_sesion))
        .values(fin=ahora, tipo_cierre=tipo_cierre)
    )

    # Solo los Docentes tienen actividades; para el resto esta sentencia no toca filas.
    # Si el cliente registró un inicio posterior a `ahora` (reloj adelantado), el cierre
    # usa el inicio para no violar el CHECK fin >= inicio y no tumbar el cambio de clave.
    ahora_sql = literal(ahora, UTCDateTime())
    db.execute(
        update(Actividad)
        .where(col(Actividad.id_sesion).in_(ids_sesion), col(Actividad.fin).is_(None))
        .values(
            fin=case((col(Actividad.inicio) > ahora_sql, col(Actividad.inicio)), else_=ahora_sql),
            tipo_cierre=CIERRE_ACTIVIDAD_FORZADO,
            sincronizado_en=ahora,
        )
        .execution_options(synchronize_session=False)
    )
    return len(ids_sesion)
