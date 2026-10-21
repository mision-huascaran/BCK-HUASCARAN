"""Estado de sincronización de actividades y docentes (CU017 a CU021).

La sincronización sin conexión llega en la Tanda 5. Hasta entonces el servidor no recibe
información de lo que queda pendiente en los dispositivos, así que las cantidades de
pendientes y rechazos son desconocidas (NULL, nunca 0: CU019 y CU020 piden no mostrar
como cero una cantidad que no se puede determinar):

- una actividad figura `sincronizada`: todo lo que el servidor recibió está aplicado;
- un docente figura `sin_informacion` y sus "Registros pendientes informados" en null.

Este módulo es el único lugar que la Tanda 5 debe reemplazar: el resto del código usa
estas expresiones SQL para mostrar, filtrar y ordenar, así que al contar los pendientes
y rechazos de verdad los listados siguen funcionando y paginando en la BD.
"""
from typing import Literal

from sqlalchemy import Integer, String, case, cast, null
from sqlalchemy.sql.elements import ColumnElement

SINCRONIZADA = "sincronizada"
PENDIENTE = "pendiente"
ERROR = "error"
AL_DIA = "al_dia"
SIN_INFORMACION = "sin_informacion"

EstadoSincronizacionActividad = Literal["sincronizada", "pendiente", "error"]
EstadoSincronizacionDocente = Literal["error", "pendiente", "sin_informacion", "al_dia"]

# Orden del listado de CU020: primero los que requieren atención.
PRIORIDAD_ESTADO_DOCENTE = {ERROR: 0, PENDIENTE: 1, SIN_INFORMACION: 2, AL_DIA: 3}


def _cantidad_desconocida() -> ColumnElement[int]:
    """NULL tipado: el servidor no puede determinar la cantidad."""
    return cast(null(), Integer)


def pendientes_actividad_sql() -> ColumnElement[int]:
    """Registros de la actividad (`Actividad.id_actividad`) que el servidor sabe que
    faltan sincronizar; NULL si no lo puede determinar. Tanda 5: subconsulta
    correlacionada sobre la cola de operaciones."""
    return _cantidad_desconocida()


def errores_actividad_sql() -> ColumnElement[int]:
    """Operaciones de la actividad rechazadas en forma definitiva; NULL si se desconoce."""
    return _cantidad_desconocida()


def estado_actividad_sql(pendientes: ColumnElement[int], errores: ColumnElement[int]) -> ColumnElement[str]:
    """`error` si hay rechazos, `pendiente` si hay pendientes; si no, `sincronizada`.
    Independiente de que la actividad esté en curso o finalizada."""
    return cast(
        case((errores > 0, ERROR), (pendientes > 0, PENDIENTE), else_=SINCRONIZADA),
        String,
    )


def pendientes_docente_sql() -> ColumnElement[int]:
    """Registros del docente (`Usuario.id_docente`) pendientes de sincronizar que el
    servidor conoce; NULL si no lo puede determinar."""
    return _cantidad_desconocida()


def errores_docente_sql() -> ColumnElement[int]:
    """Operaciones del docente rechazadas en forma definitiva; NULL si se desconoce."""
    return _cantidad_desconocida()


def estado_docente_sql(pendientes: ColumnElement[int], errores: ColumnElement[int]) -> ColumnElement[str]:
    """`error` si conoce rechazos, `pendiente` si conoce pendientes, `sin_informacion` si
    no puede determinar los pendientes y `al_dia` si sabe que no hay ninguno."""
    return cast(
        case(
            (errores > 0, ERROR),
            (pendientes > 0, PENDIENTE),
            (pendientes.is_(None), SIN_INFORMACION),
            else_=AL_DIA,
        ),
        String,
    )


def prioridad_estado_docente_sql(estado: ColumnElement[str]) -> ColumnElement[int]:
    """Posición del estado en el orden de CU020 (error → pendiente → sin información →
    al día), para ordenar en la consulta y que la paginación sea correcta."""
    return case(PRIORIDAD_ESTADO_DOCENTE, value=estado, else_=len(PRIORIDAD_ESTADO_DOCENTE))
