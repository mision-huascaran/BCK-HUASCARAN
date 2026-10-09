"""Actividades de trabajo del Docente (CU009, CU010, CU017, CU018, CU019).

Lo que los CU017 a CU021 llaman "sesión" es una actividad; su "ID de sesión" es
`id_actividad`. Los estados viajan como valores estables en snake_case y el frontend
pone las etiquetas; el tipo de cierre viaja con el texto guardado en la BD y en null
mientras la actividad está en curso (el frontend muestra "No aplica").
"""
import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel

from app.schemas.comun import ItemCatalogo
from app.services.sincronizacion import EstadoSincronizacionActividad

EstadoActividad = Literal["en_curso", "finalizada"]
TipoCierre = Literal[
    "Manual por finalización de actividad",
    "Forzado por cierre de sesión",
    "Automático por expiración de sesión",
]


class ActividadIniciar(BaseModel):
    # Lo genera el cliente: reenviar el mismo id no duplica la actividad.
    id_actividad: uuid.UUID
    # Hora real de inicio (con zona). Por defecto, el instante en que llega al servidor;
    # obligatoria si `id_sesion` es otra sesión.
    inicio: Optional[AwareDatetime] = None
    # Sesión en que ocurrió, si no es la del token: una actividad iniciada sin conexión
    # que se sincroniza después de un nuevo login (CU008, CU009). Por defecto, la del token.
    id_sesion: Optional[uuid.UUID] = None


class ActividadFinalizar(BaseModel):
    # Hora real de cierre (con zona), p. ej. si finalizó sin conexión. Por defecto, ahora.
    fin: Optional[AwareDatetime] = None


class ActividadItem(BaseModel):
    id: uuid.UUID
    # Sesión en que ocurrió la actividad (puede no ser la de la petición).
    id_sesion: uuid.UUID
    inicio: datetime
    fin: Optional[datetime] = None
    tipo_cierre: Optional[TipoCierre] = None
    estado: EstadoActividad


class ActividadRespuesta(BaseModel):
    actividad: ActividadItem
    # Vencimiento de la sesión de la petición (el que usa el Inicio).
    sesion_expira: datetime
    # Vencimiento de la sesión de la actividad: su fin no puede pasar de ese instante.
    # Igual a `sesion_expira` salvo en actividades de otra sesión.
    actividad_sesion_expira: datetime


class ActividadActiva(BaseModel):
    id: uuid.UUID
    inicio: datetime


# ── Consulta (CU017 a CU019, CU021) ─────────────────────────────────────────────────

class ProductividadModulo(BaseModel):
    # rubrica | seguimiento_lectura | registro_vuelo | alumnos | asistencia | otros
    modulo: str
    # Registros distintos afectados en la actividad.
    cantidad: int


class ActividadResumen(BaseModel):
    """Fila del listado de actividades (CU017) y del historial de un docente (CU021)."""

    id: uuid.UUID
    id_docente: int
    # Día de Lima en que empezó: con `id_docente`, los filtros de los atajos de CU021.
    fecha: date
    inicio: datetime
    fin: Optional[datetime] = None
    # Hasta el fin o, en curso, hasta el momento de la consulta.
    duracion_segundos: int
    estado: EstadoActividad
    tipo_cierre: Optional[TipoCierre] = None
    sincronizacion: EstadoSincronizacionActividad
    # Registros distintos afectados (crear un alumno con 6 campos cuenta 1).
    cambios: int
    # Solo los módulos con cambios; dicen también qué atajos mostrar.
    productividad: list[ProductividadModulo]
    # "15 Rúbricas, 2 Registros de Vuelo" o "Sin registros".
    productividad_texto: str


class CambiosModulo(BaseModel):
    modulo: str
    total: int
    creados: int
    editados: int
    activados: int
    inactivados: int


class AsignacionInvolucrada(BaseModel):
    colegio: ItemCatalogo
    grado: ItemCatalogo
    ciclo: str
    seccion: str


class ActividadDetalle(BaseModel):
    """Detalle de una actividad propia (CU019)."""

    id: uuid.UUID
    fecha: date
    inicio: datetime
    fin: Optional[datetime] = None
    duracion_segundos: int
    estado: EstadoActividad
    tipo_cierre: Optional[TipoCierre] = None
    sincronizacion: EstadoSincronizacionActividad
    # Última vez que el servidor recibió datos de la actividad.
    sincronizado_en: datetime
    registros_pendientes: int
    cambios: int
    cambios_por_modulo: list[CambiosModulo]
    asignaciones: list[AsignacionInvolucrada]
