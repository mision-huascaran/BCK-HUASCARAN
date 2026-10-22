"""Pantalla de Inicio de cada rol (CU010, CU011, CU012)."""
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
    # Avisos de operaciones sin enviar que informó el Docente (POST /sincronizacion/pendientes).
    registros_offline_pendientes: int = 0


TipoAlerta = Literal["sincronizacion_olvidada", "inactividad_operativa", "incumplimiento_sede"]


class AlertaOperativa(BaseModel):
    """Alerta operativa de CU011. `critica`: el front la resalta (incumplimiento de sede)."""

    tipo: TipoAlerta
    mensaje: str
    critica: bool


class RegistrosPorModulo(BaseModel):
    asistencias: int = 0
    lectura: int = 0
    rubrica: int = 0
    registro_vuelo: int = 0


class InicioSupervisor(BaseModel):
    colegios_registrados: int
    # Con alguna asignación habilitada hoy según el calendario de su colegio (en un PA, la
    # de ese PA; en un PR, las retroactivas habilitadas y las del PA siguiente).
    docentes_activos: int
    docentes_con_actividad_activa: int
    # Obligaciones de los cuatro módulos: vencidas sin ningún registro e incompletas.
    registros_vencidos: int
    registros_incompletos: int
    # El mismo total desglosado por módulo.
    vencidos_por_modulo: RegistrosPorModulo = RegistrosPorModulo()
    incompletos_por_modulo: RegistrosPorModulo = RegistrosPorModulo()
    # Primero las de sincronización, luego las de inactividad y al final las de sede.
    alertas: list[AlertaOperativa]


class InicioDirectivo(BaseModel):
    """Indicadores institucionales, sin datos personales de alumnos."""

    beneficiarios_activos: int
    colegios_operando: int
    # % de registros sincronizados; null = "Sin datos disponibles".
    salud_sistema: Optional[int] = None
