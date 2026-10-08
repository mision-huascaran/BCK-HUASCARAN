"""Pantalla de Inicio de cada rol (CU010, CU011, CU012).

Los indicadores que dependen de las grillas y de la sincronización sin conexión llegan
en la Tanda 8; hasta entonces se devuelven en null.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.actividad import ActividadActiva
from app.schemas.asignacion import MiAsignacion


class InicioDocente(BaseModel):
    asignaciones: list[MiAsignacion]
    actividad_activa: Optional[ActividadActiva] = None
    sesion_expira: datetime
    puede_iniciar_actividad: bool
    registros_offline_pendientes: Optional[int] = None


class AlertaInactividad(BaseModel):
    tipo: Literal["docente_inactivo"] = "docente_inactivo"
    id_docente: int
    docente: str
    dias_habiles: int
    mensaje: str


class InicioSupervisor(BaseModel):
    colegios_registrados: int
    docentes_activos: int
    docentes_con_actividad_activa: int
    registros_pendientes: Optional[int] = None
    registros_incompletos: Optional[int] = None
    alertas: list[AlertaInactividad]


class InicioDirectivo(BaseModel):
    """Indicadores institucionales, sin datos personales de alumnos."""

    beneficiarios_activos: int
    colegios_operando: int
    salud_sistema: None = None
