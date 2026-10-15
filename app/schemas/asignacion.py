"""Asignaciones docente-colegio-grado-periodo (solo lectura; se editan con PATCH /usuarios)."""
from pydantic import BaseModel

from app.schemas.usuario import GradoAsignado


class AsignacionItem(BaseModel):
    """Fila de `docente_colegio_grado` con los nombres resueltos."""

    id: int
    id_docente: int
    docente: str
    id_colegio: int
    colegio: str
    id_grado: int
    grado: str
    id_periodo_academico: int
    periodo: int
    id_anio_escolar: int
    anio: str
    vigente: bool


class GradoACargo(GradoAsignado):
    """Una asignación (colegio + grado) con lo que tiene en la práctica: sus alumnos
    activos, los subprogramas de esos alumnos y sus ciclos (misma regla que Alumnos)."""

    ciclos: list[str]
    subprogramas: list[str]
    cantidad_alumnos: int


class MiAsignacion(BaseModel):
    """Lo que el Docente tiene a cargo en el periodo vigente, por colegio."""

    id_colegio: int
    colegio: str
    grados: list[GradoACargo]
    ciclos: list[str]
    subprogramas: list[str]
    cantidad_alumnos: int


class TotalesACargo(BaseModel):
    """Lo mismo, sumado sobre todas sus asignaciones vigentes."""

    ciclos: list[str]
    subprogramas: list[str]
    cantidad_alumnos: int
