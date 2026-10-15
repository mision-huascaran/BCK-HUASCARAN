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

class NivelColocado(BaseModel):
    """Letra Raz-Kids en que el docente colocó al alumno en un Registro de Vuelo."""

    letra: str
    id_nivel_rk: int
    # Periodo (corte) del Registro de Vuelo del que sale.
    numero_periodo: int
    # Inicio, En proceso, Logrado o Avanzado según el grado actual del alumno; null si
    # su grado no tiene escala.
    nivel_descriptivo: Optional[str] = None


class Resumen(BaseModel):
    # Del Registro de Vuelo más reciente del año que lo tenga; null si ninguno.
    nivel_colocado: Optional[NivelColocado] = None
    # LSL + LSB del año; 0 si el alumno no tiene reportes semanales en el año.
    libros_leidos_anio: int = 0
    # Asistencias (presente o tardanza) sobre clases dictadas a su colegio, grado y
    # subprograma en el año, en %, con 1 decimal; 0 sin clases dictadas.
    asistencia_porcentaje: float = 0


class RegistroVuelo(BaseModel):
    corte: int
    fecha: date
    ciclo_evaluado: str
    nivel_entrada: str
    aciertos: Optional[int] = None
    total: Optional[int] = None
    nivel_colocado: Optional[str] = None
    # Nivel descriptivo de la letra colocada (grado actual del alumno); null sin letra.
    nivel_descriptivo: Optional[str] = None
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
