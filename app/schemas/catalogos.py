"""Catálogos de solo lectura, para la precarga del frontend (sin paginación).

Fechas como días de calendario (AAAA-MM-DD). `vigente` y `cerrada` se calculan con el
día de Lima.
"""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel


class GradoResponse(BaseModel):
    id: int
    # `id_grado` se conserva por compatibilidad con los clientes que ya lo leen.
    id_grado: int
    nombre: str
    id_ciclo: int


class ProgramaResponse(BaseModel):
    id: int
    # `id_programa` se conserva por compatibilidad con los clientes que ya lo leen.
    id_programa: int
    nombre: str


class AnioEscolarResponse(BaseModel):
    id: int
    nombre: str
    fecha_inicio: date
    fecha_fin: date
    vigente: bool


class PeriodoAcademicoResponse(BaseModel):
    """Un PA con las fechas del calendario consultado (general o el del colegio)."""

    id: int
    id_anio_escolar: int
    numero: int
    # Siempre "PA": los recesos no son periodos académicos (ver GET /calendarios).
    tipo: Literal["PA"]
    # Null si el año no tiene calendario configurado.
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    vigente: bool


class SemanaResponse(BaseModel):
    id: int
    id_semana: int
    numero_semana: int
    fecha_inicio: date
    fecha_fin: date
    # La semana ya terminó (su último día es anterior a hoy).
    cerrada: bool


class NivelRazkidsResponse(BaseModel):
    id: int
    letra: str


class NivelRubricaResponse(BaseModel):
    id: int
    id_programa: int
    dimension: str
    orden: int
    # Código 0-4 con que se evalúa (0 Pre-inicio … 4 Avanzado).
    valor: int
    nombre_nivel: str
