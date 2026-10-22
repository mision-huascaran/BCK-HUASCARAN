"""Permisos excepcionales de corrección (CU028). Solo el Supervisor, con conexión."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.comun import ItemCatalogo

ModuloPermiso = Literal["asistencias", "rubrica", "registro_vuelo", "lectura"]
# Calculado al consultar: revocado si se revocó; si no, programado (antes del inicio),
# vigente (entre inicio y fin) o vencido (desde el fin).
EstadoPermiso = Literal["programado", "vigente", "vencido", "revocado"]


# ── Requests ────────────────────────────────────────────────────────────────────────

class PermisoCrear(BaseModel):
    id_anio_escolar: int
    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    modulo: ModuloPermiso
    # Futuro (o igual al momento del otorgamiento). Con `desde_ahora`, lo fija el servidor
    # y se ignora el enviado.
    inicio: Optional[datetime] = None
    desde_ahora: bool = False
    fin: datetime
    # Obligatorio; se valida en el servicio para devolver el mensaje del caso de uso.
    motivo: str = ""


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
