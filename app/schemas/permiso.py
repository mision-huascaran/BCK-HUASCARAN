"""Permisos excepcionales de corrección (CU028). Solo el Supervisor, con conexión."""
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field

from app.core.tiempo import ZONA_LIMA
from app.schemas.comun import ItemCatalogo

ModuloPermiso = Literal["asistencias", "rubrica", "registro_vuelo", "lectura"]
# Calculado al consultar: revocado si se revocó; si no, programado (antes del inicio),
# vigente (entre inicio y fin) o vencido (desde el fin).
EstadoPermiso = Literal["programado", "vigente", "vencido", "revocado"]
MAXIMO_COMBINACIONES = 24
MENSAJE_COMBINACIONES_REPETIDAS = "Hay combinaciones de grado y módulo repetidas."


def _con_zona(valor: datetime) -> datetime:
    """Sin zona (p. ej. "2026-10-26T12:39" de un input datetime-local) es hora de Lima;
    con zona se respeta. Así el servicio siempre compara instantes con zona."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=ZONA_LIMA)
    return valor


FechaHoraLima = Annotated[datetime, AfterValidator(_con_zona)]


# ── Requests ────────────────────────────────────────────────────────────────────────

class PermisoCrear(BaseModel):
    id_anio_escolar: int
    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    modulo: ModuloPermiso
    # Futuro (o igual al momento del otorgamiento). Con `desde_ahora`, lo fija el servidor
    # y se ignora el enviado.
    inicio: Optional[FechaHoraLima] = None
    desde_ahora: bool = False
    fin: FechaHoraLima
    # Obligatorio; se valida en el servicio para devolver el mensaje del caso de uso.
    motivo: str = ""


class CombinacionPermiso(BaseModel):
    id_grado: int
    modulo: ModuloPermiso


def _sin_combinaciones_repetidas(combinaciones: list[CombinacionPermiso]) -> list[CombinacionPermiso]:
    if len({(c.id_grado, c.modulo) for c in combinaciones}) < len(combinaciones):
        raise ValueError(MENSAJE_COMBINACIONES_REPETIDAS)
    return combinaciones


class PermisoLoteCrear(BaseModel):
    """Varios permisos del mismo docente, año, colegio y PA, con la misma vigencia y el
    mismo motivo: uno por cada combinación de grado y módulo. Todo o nada."""

    id_anio_escolar: int
    id_colegio: int
    id_periodo_academico: int
    inicio: Optional[FechaHoraLima] = None
    desde_ahora: bool = False
    fin: FechaHoraLima
    motivo: str = ""
    combinaciones: Annotated[
        list[CombinacionPermiso],
        Field(min_length=1, max_length=MAXIMO_COMBINACIONES),
        AfterValidator(_sin_combinaciones_repetidas),
    ]


class PermisoRevocar(BaseModel):
    motivo: str = ""


# ── Responses ───────────────────────────────────────────────────────────────────────

class PermisoItem(BaseModel):
    id: int
    anio_escolar: ItemCatalogo
    colegio: ItemCatalogo
    grado: ItemCatalogo
    # "PA2".
    periodo: ItemCatalogo
    modulo: ModuloPermiso
    inicio: datetime
    fin: datetime
    estado: EstadoPermiso
    otorgado_en: datetime


class PermisoDetalle(PermisoItem):
    motivo: str
    otorgado_por: ItemCatalogo
    revocado_por: Optional[ItemCatalogo] = None
    revocado_en: Optional[datetime] = None
    motivo_revocacion: Optional[str] = None


class PermisoVigente(BaseModel):
    """Permiso vigente del Docente, para su Inicio (CU010)."""

    id_permiso: int
    colegio: ItemCatalogo
    grado: ItemCatalogo
    anio_escolar: ItemCatalogo
    # "PA2".
    periodo: str
    modulo: ModuloPermiso
    fin: datetime


class OpcionesPermiso(BaseModel):
    """Para el modal de otorgar: los PA del colegio cuyo plazo ordinario ya terminó (pasó
    el fin de su PR) y los grados que ofrece. No depende de las asignaciones del docente."""

    periodos: list[ItemCatalogo]
    grados: list[ItemCatalogo]
