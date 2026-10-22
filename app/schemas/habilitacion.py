"""Habilitaciones del Docente para validar sin conexión (GET /me/habilitaciones)."""
from datetime import datetime

from pydantic import BaseModel

from app.schemas.calendario import CalendarioDeColegio
from app.schemas.permiso import ModuloPermiso


class AsignacionUtilizable(BaseModel):
    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    retroactiva: bool


class PermisoUtilizable(BaseModel):
    id_permiso: int
    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    modulo: ModuloPermiso
    inicio: datetime
    fin: datetime


class MisHabilitaciones(BaseModel):
    generado_en: datetime
    asignaciones: list[AsignacionUtilizable]
    permisos: list[PermisoUtilizable]
    # Uno por cada (colegio, año escolar) de las asignaciones y permisos, sin repetir.
    calendarios: list[CalendarioDeColegio]
