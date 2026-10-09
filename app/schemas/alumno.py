"""Alumnos (CU014) y su vista de detalle (CU015).

Supervisor: todos. Docente: los de su alcance (colegio y grado de sus asignaciones
vigentes). El Directivo no tiene acceso.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.comun import ItemCatalogo, ParcheSinNulos, Texto


# ── Requests ────────────────────────────────────────────────────────────────────────

class AlumnoCrear(BaseModel):
    nombres: Texto
    apellidos: Texto
    id_colegio: int
    id_grado: int
    id_programa: int


class AlumnoEditar(ParcheSinNulos):
    """Campos opcionales; un null se rechaza en el campo. El Docente no puede cambiar
    `id_colegio` (solo el Supervisor rota alumnos entre colegios)."""

    nombres: Optional[Texto] = None
    apellidos: Optional[Texto] = None
    id_colegio: Optional[int] = None
    id_grado: Optional[int] = None
    id_programa: Optional[int] = None


# ── Listado y cabecera ──────────────────────────────────────────────────────────────

class AlumnoItem(BaseModel):
    id: int
    nombres: str
    apellidos: str
    colegio: ItemCatalogo
    grado: ItemCatalogo
    programa: ItemCatalogo
    # Calculado: Alfabetización -> III; Comprensión Lectora -> ciclo de su grado.
    ciclo: str
    # La del colegio: el alumno no la elige.
    seccion: str
    activo: bool


class AlumnoDetalle(AlumnoItem):
    fecha_registro: date


# ── Pestañas del detalle (año escolar en curso) ─────────────────────────────────────

class NivelActual(BaseModel):
    nombre: str
    mes: int


class Resumen(BaseModel):
    nivel_actual: Optional[NivelActual] = None
    # LSL + LSB del año; null si el alumno no tiene ningún reporte semanal en el año.
    libros_leidos_anio: Optional[int] = None
    # Semanas asistidas sobre semanas registradas, en %, con 1 decimal; null sin registros.
    asistencia_porcentaje: Optional[float] = None


class RegistroVuelo(BaseModel):
    corte: int
    fecha: date
    ciclo_evaluado: str
    nivel_entrada: str
    aciertos: Optional[int] = None
    total: Optional[int] = None
    nivel_colocado: Optional[str] = None
    nivel_general: Optional[str] = None
    observacion: Optional[str] = None


class RubricaSemanal(BaseModel):
    semana: int
    fecha_inicio: date
    fluidez: Optional[str] = None
    comprension: Optional[str] = None
    observacion: Optional[str] = None


class RubricaMensual(BaseModel):
    mes: int
    nivel_final: str
    ajustado: bool
    justificacion: Optional[str] = None


class Rubrica(BaseModel):
    semanales: list[RubricaSemanal]
    mensuales: list[RubricaMensual]


class LibroLsb(BaseModel):
    titulo: str
    aciertos: int
    total: int


class LecturaSemanal(BaseModel):
    semana: int
    fecha_inicio: date
    cantidad_lsl: Optional[int] = None
    observaciones: Optional[str] = None
    libros_lsb: list[LibroLsb]


class CambioHistorial(BaseModel):
    campo: str
    anterior: Optional[str] = None
    nuevo: Optional[str] = None


class EventoHistorial(BaseModel):
    fecha: datetime
    usuario: str
    rol: str
    accion: str
    cambios: list[CambioHistorial]
