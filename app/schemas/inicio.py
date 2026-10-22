"""Pantalla de Inicio de cada rol (CU010, CU011, CU012).

Los indicadores que dependen de la sincronización sin conexión (registros offline
pendientes, salud del sistema) se devuelven en null hasta que exista.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.actividad import ActividadActiva
from app.schemas.asignacion import MiAsignacion, TotalesACargo
from app.schemas.permiso import PermisoVigente


class InicioDocente(BaseModel):
    # Por colegio y, dentro, por asignación (colegio + grado).
    asignaciones: list[MiAsignacion]
    # Lo mismo sumado sobre todas sus asignaciones vigentes.
    totales: TotalesACargo
    actividad_activa: Optional[ActividadActiva] = None
    # Vencimiento de la sesión del token; viene siempre, haya o no actividad (CU010).
    sesion_expira: datetime
    puede_iniciar_actividad: bool
    # Por qué no puede (null si puede): el código, como el `motivo` del error de POST
    # /actividades (salvo `actividad_activa` = `actividad_activa_existente`), y el texto
    # que el front muestra con el botón deshabilitado.
    motivo_no_puede_iniciar: Optional[Literal["actividad_activa", "sin_asignaciones"]] = None
    motivo_no_puede: Optional[str] = None
    # Permisos de corrección vigentes (CU028). Los grupos a los que solo llega por un
    # permiso no suman a `asignaciones` ni a `totales`.
    permisos_vigentes: list[PermisoVigente] = []
    registros_offline_pendientes: Optional[int] = None


TipoAlerta = Literal["sincronizacion_olvidada", "inactividad_operativa", "incumplimiento_sede"]


class AlertaOperativa(BaseModel):
    """Alerta operativa de CU011. `critica`: el front la resalta (incumplimiento de sede)."""

    tipo: TipoAlerta
    mensaje: str
    critica: bool


class InicioSupervisor(BaseModel):
    colegios_registrados: int
    # Con asignación en el periodo vigente (0 sin periodo vigente).
    docentes_activos: int
    docentes_con_actividad_activa: int
    # Clases de Asistencias del periodo vigente (un grado en una fecha): vencidas sin
    # ningún registro e iniciadas con algún alumno activo sin marcar.
    registros_vencidos: int
    registros_incompletos: int
    # Primero las de sincronización, luego las de inactividad y al final las de sede.
    alertas: list[AlertaOperativa]


class InicioDirectivo(BaseModel):
    """Indicadores institucionales, sin datos personales de alumnos."""

    beneficiarios_activos: int
    colegios_operando: int
    salud_sistema: None = None
