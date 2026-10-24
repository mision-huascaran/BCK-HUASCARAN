"""Calendario académico (CU027). Fechas como días de calendario (AAAA-MM-DD), inclusivas:
un receso o día no lectivo de un solo día tiene la misma fecha de inicio y fin."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.comun import ItemCatalogo, Texto

TipoPeriodo = Literal["PA", "PR"]
OrigenCalendario = Literal["general", "particular"]
OrigenDiaNoLectivo = Literal["automatico", "manual"]


# ── Requests ────────────────────────────────────────────────────────────────────────

class PeriodoEntrada(BaseModel):
    """Un periodo del calendario. PR n es el receso posterior a PA n."""

    tipo: TipoPeriodo
    numero: int = Field(ge=1, le=4)
    fecha_inicio: date
    fecha_fin: date


class DiaNoLectivoEntrada(BaseModel):
    """Feriado o suspensión: dentro de los periodos (PA o PR) y sin superponerse con otro."""

    motivo: Texto
    fecha_inicio: date
    fecha_fin: date


class CalendarioCrear(BaseModel):
    """Calendario general de un año: los 8 periodos y sus días no lectivos."""

    id_anio_escolar: int
    periodos: list[PeriodoEntrada]
    dias_no_lectivos: list[DiaNoLectivoEntrada] = []


class CalendarioEditar(BaseModel):
    """Configuración completa que reemplaza a la actual. `version` es la que se obtuvo al
    abrir la edición: si otro Supervisor guardó antes, 409 `modificacion_concurrente`."""

    version: int
    periodos: list[PeriodoEntrada]
    dias_no_lectivos: list[DiaNoLectivoEntrada] = []


class CalendarioParticularCrear(BaseModel):
    """Fechas particulares de un colegio. Sin `dias_no_lectivos` (omitido o null) se
    copian los del calendario general, con su origen."""

    id_colegio: int
    id_anio_escolar: int
    periodos: list[PeriodoEntrada]
    dias_no_lectivos: Optional[list[DiaNoLectivoEntrada]] = None


class UsarGeneral(BaseModel):
    """`version` del calendario particular que se consultó; si se envía y cambió, 409."""

    version: Optional[int] = None


# ── Responses ───────────────────────────────────────────────────────────────────────

class PeriodoSalida(BaseModel):
    tipo: TipoPeriodo
    numero: int
    # PA n del año; para un PR n, el del PA anterior.
    id_periodo_academico: Optional[int] = None
    fecha_inicio: date
    fecha_fin: date


class DiaNoLectivoSalida(BaseModel):
    motivo: str
    fecha_inicio: date
    fecha_fin: date
    origen: OrigenDiaNoLectivo


class CalendarioRespuesta(BaseModel):
    """Calendario aplicable a un año y alcance (general o un colegio)."""

    id_calendario: int
    id_anio_escolar: int
    # Colegio consultado; null en el alcance General.
    colegio: Optional[ItemCatalogo] = None
    # 'particular' si el colegio tiene fechas propias; si no, 'general'.
    origen: OrigenCalendario
    version: int
    # PA1, PR1, ..., PA4, PR4, en orden cronológico.
    periodos: list[PeriodoSalida]
    dias_no_lectivos: list[DiaNoLectivoSalida]
    # Periodo que contiene hoy (hora de Lima); null = "Sin periodo aplicable a la fecha actual".
    periodo_actual: Optional[PeriodoSalida] = None
    fecha_referencia: date


class PeriodoEnFecha(BaseModel):
    """Qué es una fecha en el calendario de un colegio (GET /calendarios/periodo)."""

    fecha: date
    # null fuera de todo periodo.
    tipo: Optional[TipoPeriodo] = None
    numero: Optional[int] = None
    id_periodo_academico: Optional[int] = None
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    es_lectivo: bool
    # null si es lectivo; "fuera_de_periodo", "receso", "fin_de_semana" o el motivo del
    # día no lectivo.
    motivo_no_lectivo: Optional[str] = None


class CalendarioDeColegio(CalendarioRespuesta):
    """El calendario aplicable a un colegio en un año (GET /me/habilitaciones)."""

    id_colegio: int
