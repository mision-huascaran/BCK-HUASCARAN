"""Seguimiento de docentes por el Supervisor (CU020, CU021)."""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.comun import ItemCatalogo
from app.services.sincronizacion import EstadoSincronizacionDocente


class DocenteSeguimiento(BaseModel):
    """Fila de CU020 y cabecera de CU021."""

    id_docente: int
    nombres: str
    apellidos: str
    activo: bool
    # Colegios de sus asignaciones del periodo vigente, sin repetir; vacía si no tiene.
    colegios: list[ItemCatalogo]
    # Inicio de su sesión autenticada más reciente; null si nunca entró.
    ultima_conexion: Optional[datetime] = None
    sincronizacion: EstadoSincronizacionDocente
    # "Registros pendientes informados": null si el servidor no puede determinarlos.
    registros_pendientes: Optional[int] = None


ModuloRegistros = Literal["asistencias", "lectura", "rubrica", "registro_vuelo", "alumnos"]


class RegistroDeActividad(BaseModel):
    """Un registro tocado en una actividad (atajos de CU021), con el contexto para abrirlo
    en solo lectura. Asistencias: una fila por clase; Lectura: por tabla semanal;
    Rúbrica y Registro de Vuelo: por alumno y mes o PA; Alumnos: por alumno."""

    modulo: ModuloRegistros
    # id de la clase, la tabla de lectura, la rúbrica, el registro de vuelo o el alumno.
    id: int
    colegio: ItemCatalogo
    grado: ItemCatalogo
    seccion: ItemCatalogo
    programa: Optional[ItemCatalogo] = None
    alumno: Optional[ItemCatalogo] = None
    # Asistencias: fecha de la clase.
    fecha: Optional[date] = None
    # Lectura: semana (y el mes en que se ubica la tabla); Rúbrica: mes.
    id_semana: Optional[int] = None
    semana_inicio: Optional[date] = None
    mes: Optional[int] = None
    id_anio_escolar: Optional[int] = None
    # Registro de Vuelo.
    id_periodo_academico: Optional[int] = None
    # Asistencias: alumnos de la clase con cambios; los demás: veces que se guardó.
    cambios: int
