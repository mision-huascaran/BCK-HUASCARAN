"""Contrato de transición para CU033, CU034 y CU035."""
from typing import Optional
from pydantic import BaseModel, Field


class FiltrosDashboard(BaseModel):
    id_colegio: Optional[int] = None  # Compatibilidad con clientes anteriores
    id_colegios: Optional[list[int]] = None  # Selección múltiple (CU033/CU035)
    id_anio_escolar: Optional[int] = None
    id_grado: Optional[int] = None
    id_seccion: Optional[int] = None
    id_programa: Optional[int] = None
    id_periodo_academico: Optional[int] = None


class AsistenciaDashboard(BaseModel):
    id_alumno: Optional[int] = None
    id_grado: Optional[int] = None
    asistidas: int
    contabilizadas: int
    porcentaje: Optional[float] = None
    presentes: int = 0
    tardanzas: int = 0
    ausentes: int = 0
    sin_marcar: int = 0
    clases_incompletas: int = 0
    clases_sin_registro: int = 0
    provisional: bool = False


class LecturaDashboard(BaseModel):
    total_libros: int
    libros_lsn: int
    libros_lsl: int
    # Solo alumnos con alguna lectura contabilizable; no equivale a la matrícula del año.
    alumnos_con_lecturas: int = 0
    promedio_por_alumno_con_lecturas: Optional[float] = None
    por_mes: dict[int, int] = Field(default_factory=dict)


class NivelDashboard(BaseModel):
    id_periodo_academico: int
    id_nivel_rk: int
    letra: str
    alumnos: int


class DistribucionAprendizaje(BaseModel):
    dimension: str  # Global, Fluidez, Comprensión
    niveles: dict[str, int]
    evaluados: int
    pendientes: int
    total_alumnos: int
    porcentajes: Optional[dict[str, float]] = None


class AprendizajeSubprograma(BaseModel):
    id_programa: int
    nombre_programa: str
    distribuciones: list[DistribucionAprendizaje]


class AprendizajePeriodoDashboard(BaseModel):
    id_periodo_academico: int
    id_anio_escolar: int
    fecha_corte_por_colegio: dict[int, str]
    resultados: list[AprendizajeSubprograma]


class CumplimientoModulo(BaseModel):
    modulo: str
    vencidos: int
    incompletos: int


class CumplimientoDashboard(BaseModel):
    alcance: str = "obligaciones vencidas o incompletas al día de consulta; CU011"
    modulos: list[CumplimientoModulo]
    # No se calcula un porcentaje sin denominador definido por el CU del Dashboard.