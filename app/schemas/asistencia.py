"""Asistencia por clase de los alumnos (CU022).

La clase es colegio × grado × sección en una fecha; el subprograma es del alumno (el
filtro de subprograma es solo visual: cada alumno trae su `id_programa`). Una fila de
`asistencia` es lo registrado para un alumno de la lista de la clase; sin fila (o con
`estado` null), el alumno aún no está marcado.
"""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.clase import TipoClase
from app.schemas.comun import ParcheSinNulos
from app.schemas.sincronizacion import ItemOperacion, ResultadoOperacion

EstadoAsistencia = Literal["presente", "tardanza", "ausente"]
EtiquetaClase = Literal["Clase programada", "Clase reprogramada", "Clase adicional"]


class ClaseAsistencia(BaseModel):
    # null si la clase todavía no tiene fila.
    id_clase: Optional[int] = None
    # null si no hay clase en la fecha.
    tipo: Optional[TipoClase] = None
    etiqueta: Optional[EtiquetaClase] = None
    fecha_original: date
    fecha: date
    aviso: Optional[str] = None


class AlumnoAsistencia(BaseModel):
    id_alumno: int
    nombre: str
    # Subprograma del alumno en la fecha de la clase.
    id_programa: int
    # Hoy inactivo, pero en la lista de la clase.
    inactivo: bool
    # null: aún sin marcar.
    estado: Optional[EstadoAsistencia] = None
    observacion: Optional[str] = None
    # null si todavía no tiene fila.
    version: Optional[int] = None


class Escritura(BaseModel):
    permitida: bool
    # Por qué no (null si se permite).
    motivo: Optional[str] = None
    # El permiso de corrección con que se escribiría (CU028).
    id_permiso: Optional[int] = None
    aviso_permiso: Optional[str] = None


class AsistenciaClase(BaseModel):
    clase: ClaseAsistencia
    # La lista congelada de la clase si ya existe; si no, los alumnos que pertenecían
    # al grupo en la fecha. Por apellidos y nombres.
    alumnos: list[AlumnoAsistencia]
    escritura: Escritura


# ── Registro ────────────────────────────────────────────────────────────────────────

class RegistroAsistencia(ItemOperacion, ParcheSinNulos):
    """Lo que cambia de un alumno: el clic manda `estado`; el modal, `observacion`. Un
    campo omitido no se cambia; `observacion` vacía o null la borra. `estado` no admite
    null (no se "desmarca"). Una observación sin estado se acepta, pero no completa la
    fila."""

    CAMPOS_NULABLES = frozenset({"observacion", "version", "realizado_en", "id_actividad"})

    id_alumno: int
    estado: Optional[EstadoAsistencia] = None
    observacion: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _algo_que_cambiar(self) -> "RegistroAsistencia":
        if not {"estado", "observacion"} & self.model_fields_set:
            raise ValueError("Debes indicar el estado o la observación.")
        if self.observacion is not None:
            self.observacion = self.observacion.strip() or None
        return self


class AsistenciaGuardar(BaseModel):
    """`id_clase`, o la clave de la clase (`id_colegio`, `id_grado`, `id_seccion`,
    `fecha`). `adicional: true` registra una clase adicional si ese día no hay clase."""

    id_clase: Optional[int] = None
    id_colegio: Optional[int] = None
    id_grado: Optional[int] = None
    id_seccion: Optional[int] = None
    fecha: Optional[date] = None
    adicional: bool = False
    registros: list[RegistroAsistencia] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _clase_identificada(self) -> "AsistenciaGuardar":
        clave = (self.id_colegio, self.id_grado, self.id_seccion, self.fecha)
        if self.id_clase is None and any(v is None for v in clave):
            raise ValueError("Indica id_clase o id_colegio, id_grado, id_seccion y fecha.")
        return self


class AsistenciaGuardada(BaseModel):
    clase: ClaseAsistencia
    resultados: list[ResultadoOperacion]
