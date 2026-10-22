"""Seguimiento de Lectura (CU023 a CU026).

Una tabla por grupo (colegio, grado, sección), subprograma y semana, atada a una clase
de referencia de esa semana. Cada fila es un alumno de la lista de esa clase. Las
escrituras son operaciones (`id_operacion`, `version`, `realizado_en`): ver
app/schemas/sincronizacion.py.
"""
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.asistencia import EstadoAsistencia, Escritura
from app.schemas.clase import TipoClase
from app.schemas.comun import ParcheSinNulos
from app.schemas.sincronizacion import ItemOperacion

EstadoTabla = Literal["normal", "no_realizada"]
EstadoFila = Literal["completada", "pendiente", "no_aplica"]
Mes = Annotated[int, Field(ge=1, le=12)]
MAX_FILAS_POR_LOTE = 200


class ClaseReferencia(BaseModel):
    # null si es una habitual que todavía no tiene fila (solo en la sugerida).
    id_clase: Optional[int] = None
    fecha: date
    tipo: TipoClase
    reprogramada: bool = False


class FilaLectura(BaseModel):
    id_alumno: int
    nombre: str
    # Hoy inactivo, pero en la lista de la clase de referencia.
    inactivo: bool
    # Asistencia en la clase de referencia; null = sin marcar.
    asistencia: Optional[EstadoAsistencia] = None
    # Último nivel colocado del Registro de Vuelo de un PA anterior; null = "Sin evaluar".
    nivel_academico: Optional[str] = None
    lsn_titulo: Optional[str] = None
    lsn_nota: Optional[int] = None
    lsn_maximo: Optional[int] = None
    # null = no registrado (≠ 0).
    cantidad_lsl: Optional[int] = None
    # (1 si hay LSN) + LSL.
    total_libros: int
    observacion: Optional[str] = None
    estado_fila: EstadoFila
    # Tiene datos de lectura guardados pero hoy la asistencia es ausente.
    revisar_asistencia: bool
    version: int


class TablaLectura(BaseModel):
    id: int
    version: int
    estado: EstadoTabla
    motivo_no_realizada: Optional[str] = None
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int
    id_anio_escolar: int
    mes: int
    id_semana: int
    clase_referencia: ClaseReferencia
    # La lista histórica de la clase de referencia con el subprograma de la tabla, por
    # apellidos y nombres.
    filas: list[FilaLectura]
    escritura: Escritura


# ── Escrituras ──────────────────────────────────────────────────────────────────────

def _a_lo_sumo_una(id_clase: Optional[int], fecha: Optional[date]) -> None:
    """La clase de referencia se elige por id o por fecha (una habitual calculada todavía
    no tiene id), no por ambos."""
    if id_clase is not None and fecha is not None:
        raise ValueError("Indica la clase por su id o por su fecha, no ambas.")


class TablaLecturaCrear(ItemOperacion):
    """Sin clase indicada, la referencia es la habitual o reprogramada de la semana."""

    id_colegio: int
    id_programa: int
    id_grado: int
    id_seccion: int
    id_anio_escolar: int
    mes: Mes
    id_semana: int
    id_clase_referencia: Optional[int] = None
    fecha_clase_referencia: Optional[date] = None

    @model_validator(mode="after")
    def _una_clase(self) -> "TablaLecturaCrear":
        _a_lo_sumo_una(self.id_clase_referencia, self.fecha_clase_referencia)
        return self


class ClaseReferenciaCambiar(ItemOperacion):
    id_clase: Optional[int] = None
    fecha: Optional[date] = None

    @model_validator(mode="after")
    def _una_clase(self) -> "ClaseReferenciaCambiar":
        _a_lo_sumo_una(self.id_clase, self.fecha)
        if self.id_clase is None and self.fecha is None:
            raise ValueError("Indica la clase por su id o por su fecha.")
        return self


class FilaLecturaGuardar(ItemOperacion, ParcheSinNulos):
    """Lo que cambia de un alumno: un campo omitido no se cambia; en null se borra. Las
    reglas del LSN (todo o nada, máximo 5 o 10, nota entre 0 y el máximo) se validan con
    el resultado de aplicar los cambios a lo guardado, y se rechazan por ítem con el
    mensaje del CU."""

    CAMPOS_NULABLES = frozenset({
        "version", "realizado_en", "id_actividad", "lsn_titulo", "lsn_nota", "lsn_maximo", "cantidad_lsl",
        "observacion",
    })

    id_alumno: int
    lsn_titulo: Optional[str] = Field(default=None, max_length=300)
    lsn_nota: Optional[int] = None
    lsn_maximo: Optional[int] = None
    cantidad_lsl: Optional[Annotated[int, Field(strict=True, ge=0)]] = None
    observacion: Optional[str] = Field(default=None, max_length=1000)


class NoRealizadaMarcar(ItemOperacion):
    # Obligatorio: el servicio responde el 422 con el mensaje del CU.
    motivo: Optional[str] = Field(default=None, max_length=1000)
