"""Estado de sincronización de actividades y docentes (CU017 a CU021).

La sincronización sin conexión llega en la Tanda 8. Hasta entonces todo lo que el
servidor tiene está sincronizado: ninguna actividad ni docente tiene registros
pendientes. Este módulo es el único lugar que la Tanda 8 debe reemplazar: el resto del
código usa estas expresiones SQL para mostrar, filtrar y ordenar, así que al contar los
pendientes de verdad los listados siguen funcionando y paginando en la BD.
"""
from typing import Literal

from sqlalchemy import Integer, String, case, cast, literal
from sqlalchemy.sql.elements import ColumnElement

SINCRONIZADA = "sincronizada"
PENDIENTE = "pendiente"
ERROR = "error"
AL_DIA = "al_dia"

EstadoSincronizacionActividad = Literal["sincronizada", "pendiente", "error"]
EstadoSincronizacionDocente = Literal["al_dia", "pendiente"]


def _sin_pendientes() -> ColumnElement[int]:
    # CAST para que sea una expresión y no un número suelto: en un ORDER BY, un entero
    # literal se interpretaría como posición de columna.
    return cast(literal(0), Integer)


def pendientes_actividad_sql() -> ColumnElement[int]:
    """Registros de la actividad (`Actividad.id_actividad`) que el servidor sabe que
    faltan sincronizar. Tanda 8: subconsulta correlacionada sobre los registros."""
    return _sin_pendientes()


def estado_actividad_sql(pendientes: ColumnElement[int]) -> ColumnElement[str]:
    """`sincronizada` o `pendiente` (la Tanda 8 agrega `error`). Independiente de que la
    actividad esté en curso o finalizada."""
    return cast(case((pendientes > 0, literal(PENDIENTE)), else_=literal(SINCRONIZADA)), String)


def pendientes_docente_sql() -> ColumnElement[int]:
    """Registros del docente (`Usuario.id_docente`) pendientes de sincronizar."""
    return _sin_pendientes()


def estado_docente_sql(pendientes: ColumnElement[int]) -> ColumnElement[str]:
    """`pendiente` si tiene registros pendientes; si no, `al_dia`."""
    return cast(case((pendientes > 0, literal(PENDIENTE)), else_=literal(AL_DIA)), String)
