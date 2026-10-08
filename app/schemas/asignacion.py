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


class MiAsignacion(BaseModel):
    """Lo que el Docente tiene a cargo en el periodo vigente, por colegio."""

    id_colegio: int
    colegio: str
    grados: list[GradoAsignado]
    ciclos: list[str]
    subprogramas: list[str]
    cantidad_alumnos: int
