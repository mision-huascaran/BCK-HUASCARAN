"""Asistencia por clase de los alumnos (CU022).

Una clase es fecha + colegio + grado + subprograma. Una fila de `asistencia` es lo
registrado para un alumno en una clase; sin fila (o con `estado` null), el alumno aún no
está marcado.
"""
from datetime import date
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.schemas.comun import ParcheSinNulos

EstadoAsistencia = Literal["presente", "tardanza", "ausente"]


# ── Consulta de la clase ────────────────────────────────────────────────────────────

class AlumnoAsistencia(BaseModel):
    id_alumno: int
    nombres: str
    apellidos: str
    # False: hoy inactivo, pero con registro en la clase consultada.
    activo: bool
    # False: tiene registro en esta clase, pero hoy pertenece a otra aula (colegio, grado o
    # subprograma distintos); el registro sigue siendo de esta clase.
    en_aula: bool
    # null: aún sin marcar.
    estado: Optional[EstadoAsistencia] = None
    observacion: Optional[str] = None


class AsistenciaClase(BaseModel):
    fecha: date
    # El usuario es Docente y la fecha es un día lectivo del periodo vigente.
    editable: bool
    # Los que tienen registro en la clase, más los activos del aula aún sin registro esa
    # fecha; por apellidos y nombres.
    alumnos: list[AlumnoAsistencia]


# ── Registro ────────────────────────────────────────────────────────────────────────

class RegistroAsistencia(ParcheSinNulos):
    """Lo que cambia de un alumno: el clic manda `estado`; el modal, `observacion`. Un
    campo omitido no se cambia; `observacion` vacía o null la borra. `estado` no admite
    null (no se "desmarca")."""

    CAMPOS_NULABLES = frozenset({"observacion"})

    id_alumno: int
    estado: Optional[EstadoAsistencia] = None
    observacion: Optional[str] = None

    @model_validator(mode="after")
    def _algo_que_cambiar(self) -> "RegistroAsistencia":
        if not {"estado", "observacion"} & self.model_fields_set:
            raise ValueError("Debe indicar el estado o la observación.")
        if self.observacion is not None:
            self.observacion = self.observacion.strip() or None
        return self


class AsistenciaGuardar(BaseModel):
    """Un registro (clic o modal) o varios ("Marcar todos como presentes")."""

    fecha: date
    registros: list[RegistroAsistencia] = Field(min_length=1)
    # Momento real de la captura (modo sin conexión); por defecto, ahora. Con dos
    # dispositivos, gana el más reciente.
    fecha_registro: Optional[AwareDatetime] = None


class RegistroGuardado(BaseModel):
    id_alumno: int
    # False: ya había un registro capturado después (otro dispositivo); no se aplicó y
    # `estado` y `observacion` son los vigentes.
    aplicado: bool
    estado: Optional[EstadoAsistencia] = None
    observacion: Optional[str] = None


class AsistenciaGuardada(BaseModel):
    fecha: date
    registros: list[RegistroGuardado]
