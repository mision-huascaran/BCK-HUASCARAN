"""Catálogos de solo lectura, para la precarga del frontend (sin paginación).

Fechas como días de calendario (AAAA-MM-DD). `vigente` y `cerrada` se calculan con el
día de Lima.
"""
from datetime import date

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
    id: int
    id_anio_escolar: int
    numero: int
    fecha_inicio: date
    fecha_fin: date
    vigente: bool


class SemanaResponse(BaseModel):
    id: int
    id_periodo_academico: int
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
    nombre_nivel: str


class NivelGeneralResponse(BaseModel):
    id: int
    orden: int
    nombre_nivel: str
