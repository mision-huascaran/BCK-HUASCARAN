"""Asistencia semanal de los alumnos (CU022).

Una fila de `asistencia_semanal` es la asistencia de un alumno en una semana; sin fila, la
asistencia de esa semana todavía no se tomó (`asistio: null`).
"""
from datetime import date
from typing import Optional

from pydantic import AwareDatetime, BaseModel, Field


# ── Consulta del aula ───────────────────────────────────────────────────────────────

class SemanaAsistencia(BaseModel):
    id_semana: int
    numero: int
    fecha_inicio: date
    fecha_fin: date


class AlumnoAsistencia(BaseModel):
    id_alumno: int
    nombres: str
    apellidos: str
    # null: la asistencia de la semana aún no se tomó.
    asistio: Optional[bool] = None


class AsistenciaAula(BaseModel):
    semana: SemanaAsistencia
    # Si la semana admite registro: es del periodo vigente y ya empezó.
    editable: bool
    # Alumnos activos del colegio, grado y subprograma, por apellidos y nombres.
    alumnos: list[AlumnoAsistencia]


# ── Registro ────────────────────────────────────────────────────────────────────────

class RegistroAsistencia(BaseModel):
    id_alumno: int
    asistio: bool


class AsistenciaGuardar(BaseModel):
    """Un registro (clic individual) o varios ("Marcar todos como presentes")."""

    id_semana: int
    registros: list[RegistroAsistencia] = Field(min_length=1)
    # Momento real de la captura (modo sin conexión); por defecto, ahora.
    fecha_registro: Optional[AwareDatetime] = None


class AsistenciaGuardada(BaseModel):
    id_semana: int
    registros: list[RegistroAsistencia]
