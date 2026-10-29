"""Contratos del Dashboard (CU033, CU034 y CU035).

La primera parte es la base común de la serie D1–D7 (filtros, contexto y selección
inicial). La segunda es el contrato provisional de Romina, que se reemplaza bloque a
bloque en las tandas D2 a D5.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.comun import ItemCatalogo

# ── Base común ──────────────────────────────────────────────────────────────────────

AlcanceColegios = Literal["todos", "uno", "varios"]


class PeriodoVisible(BaseModel):
    """PA con su nombre para mostrar: `{"id": 9, "numero": 1, "nombre": "Primer periodo"}`."""

    id: int
    numero: int
    nombre: str


class IntervaloFechas(BaseModel):
    desde: date
    hasta: date


class ContextoDashboard(BaseModel):
    """Qué representa la respuesta de un bloque: año, periodo (null en los anuales),
    colegios incluidos, cómo se eligieron, cuándo se consultó y qué fechas cubre.
    `parcial` indica que el año o el periodo todavía está en curso."""

    anio_escolar: ItemCatalogo
    periodo: Optional[PeriodoVisible]
    colegios: list[ItemCatalogo]
    alcance: AlcanceColegios
    fecha_consulta: datetime
    intervalo: IntervaloFechas
    parcial: bool


class AnioConPeriodos(BaseModel):
    id: int
    nombre: str
    periodos: list[PeriodoVisible]


class SeleccionInicial(BaseModel):
    id_anio_escolar: int
    id_periodo_academico: int


class FiltrosDisponibles(BaseModel):
    """Carga inicial de CU033 y CU035 (paso 3). `seleccion_inicial` es null cuando no hay
    un único PA común a los colegios del alcance."""

    colegios: list[ItemCatalogo]
    anios: list[AnioConPeriodos]
    seleccion_inicial: Optional[SeleccionInicial]


# ── Bloques del periodo con corte por colegio (D2) ──────────────────────────────────

class FechaCorte(BaseModel):
    """Fecha en que se mira la pertenencia de los alumnos de un colegio: el fin del PA en
    su calendario o, si sigue en curso, hoy."""

    colegio: ItemCatalogo
    fecha: date


class ColegioSinInformacion(BaseModel):
    """Colegio incluido en la selección que no se suma, con el motivo."""

    colegio: ItemCatalogo
    motivo: Literal["periodo_no_iniciado"]


class SubprogramaAlumnos(BaseModel):
    subprograma: ItemCatalogo
    alumnos: int
    # Sobre el total, con 1 decimal.
    porcentaje: float


class DistribucionSubprograma(BaseModel):
    """CU033: alumnos por subprograma según su pertenencia a la fecha de corte de su
    colegio. Sin alumnos: `sin_datos`, `total` null y `subprogramas` vacío."""

    contexto: ContextoDashboard
    fechas_corte: list[FechaCorte]
    colegios_sin_informacion: list[ColegioSinInformacion]
    total: Optional[int]
    subprogramas: list[SubprogramaAlumnos]
    sin_datos: bool
    mensaje: Optional[str] = None


class NivelDesempeno(BaseModel):
    nombre: str
    alumnos: int
    # Sobre los evaluados, con 1 decimal; null si no hay evaluados.
    porcentaje: Optional[float]


class DistribucionResultado(BaseModel):
    """Una distribución de un subprograma: el resultado global (Registro de Vuelo) o una
    dimensión de la rúbrica. Todos los niveles de la escala, también los que tienen 0."""

    resultado: Literal["global", "fluidez", "comprension"]
    fuente: Literal["registro_vuelo", "rubrica"]
    niveles: list[NivelDesempeno]
    evaluados: int
    pendientes: int
    base_porcentaje: Literal["evaluados"] = "evaluados"
    # "No hay evaluaciones completas para esta selección." cuando no hay evaluados.
    mensaje: Optional[str] = None


class ResultadosSubprograma(BaseModel):
    subprograma: ItemCatalogo
    total_a_evaluar: int
    distribuciones: list[DistribucionResultado]


class ResultadosAprendizaje(BaseModel):
    """CU033 (paso 6) y CU035 (paso 6): resultados del PA por subprograma."""

    contexto: ContextoDashboard
    fechas_corte: list[FechaCorte]
    colegios_sin_informacion: list[ColegioSinInformacion]
    subprogramas: list[ResultadosSubprograma]
    sin_datos: bool
    mensaje: Optional[str] = None


# ── Contrato provisional (se reemplaza en las tandas siguientes) ─────────────────

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
