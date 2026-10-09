"""Historial de cambios de las entidades de negocio (tabla `auditoria`, diseño v3 §8).

Una operación del usuario puede escribir varias filas (una por campo editado); todas
llevan el mismo `fecha`, que el llamador captura una sola vez con `ahora_utc()`, para que
se lean como un solo evento ("editó la información del alumno el ... a las ...").

Nunca se auditan secretos (hashes de contraseña, códigos de verificación).
"""
import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from sqlmodel import Session, col, select

from app.models.trazabilidad import Actividad, Auditoria

CREAR = "crear"
EDITAR = "editar"
ACTIVAR = "activar"
INACTIVAR = "inactivar"
ACCIONES = (CREAR, EDITAR, ACTIVAR, INACTIVAR)

# Columnas que nunca deben quedar en el historial, aunque un llamador las pase.
CAMPOS_SENSIBLES = frozenset(
    {
        "password_hash",
        "codigo_verificacion",
        "codigo_verificacion_expira",
        "codigo_verificacion_intentos",
        "llave_hash",
    }
)


def serializar(valor: Any) -> Optional[str]:
    """Texto estable para guardar en `valor_anterior` / `valor_nuevo`.

    None -> NULL, booleanos 'true'/'false', fechas e instantes en ISO 8601.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Enum):
        return str(valor.value)
    return str(valor)


def registrar_auditoria(
    db: Session,
    *,
    tabla: str,
    id_registro: Any,
    accion: str,
    cambios: Optional[dict[str, tuple[Any, Any]]] = None,
    id_usuario: int,
    fecha: datetime,
    id_actividad: Optional[uuid.UUID] = None,
) -> int:
    """Agrega las filas de historial de una operación. No hace commit.

    - `crear`: una fila sin campo ni valores.
    - `editar`: una fila por campo de `cambios` ({campo: (anterior, nuevo)}) cuyo valor
      realmente cambió; si ninguno cambió, no escribe nada.
    - `activar` / `inactivar`: una fila con `campo = 'activo'`.

    Devuelve cuántas filas agregó.
    """
    if accion not in ACCIONES:
        raise ValueError(f"Acción de auditoría desconocida: {accion}")

    base = {
        "tabla": tabla,
        "id_registro": str(id_registro),
        "accion": accion,
        "id_usuario": id_usuario,
        "fecha": fecha,
        "id_actividad": id_actividad,
    }
    if accion == CREAR:
        filas = [Auditoria(**base)]
    elif accion in (ACTIVAR, INACTIVAR):
        filas = [
            Auditoria(
                **base,
                campo="activo",
                valor_anterior=serializar(accion == INACTIVAR),
                valor_nuevo=serializar(accion == ACTIVAR),
            )
        ]
    else:
        filas = [
            Auditoria(
                **base,
                campo=campo,
                valor_anterior=serializar(anterior),
                valor_nuevo=serializar(nuevo),
            )
            for campo, (anterior, nuevo) in (cambios or {}).items()
            if campo not in CAMPOS_SENSIBLES and serializar(anterior) != serializar(nuevo)
        ]
    db.add_all(filas)
    return len(filas)


def id_actividad_activa(db: Session, id_docente: int) -> Optional[uuid.UUID]:
    """Actividad en curso del docente (para vincularla a sus cambios), o None."""
    return db.exec(
        select(Actividad.id_actividad).where(
            Actividad.id_docente == id_docente, col(Actividad.fin).is_(None)
        )
    ).first()
