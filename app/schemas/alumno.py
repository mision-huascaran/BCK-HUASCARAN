"""Alumnos (CU014) y su vista de detalle (CU015).

Supervisor: todos. Docente: los de su alcance (colegio y grado de sus asignaciones
vigentes). El Directivo no tiene acceso.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.asistencia import EstadoAsistencia
from app.schemas.comun import ItemCatalogo, ParcheSinNulos, Texto
from app.schemas.lectura import EstadoFila, EstadoTabla


# ── Requests ────────────────────────────────────────────────────────────────────────

class AlumnoCrear(BaseModel):
    nombres: Texto
    apellidos: Texto
    id_colegio: int
    id_grado: int
    # Obligatoria si el grado tiene varias secciones activas; con una sola, se asigna sola.
    id_seccion: Optional[int] = None
    id_programa: int
    # "Pertenece al grupo desde" (CU014): hoy o una fecha anterior.
    pertenece_desde: date


class AlumnoEditar(ParcheSinNulos):
    """Campos opcionales; un null se rechaza en el campo. El Docente no puede cambiar
    `id_colegio` (solo el Supervisor rota alumnos entre colegios).

    Si cambia el grupo (colegio, grado, sección o subprograma) de un alumno activo,
    `cambio_grupo_desde` es obligatorio. Si cambia el colegio o el grado sin `id_seccion`,
    se asigna la única sección activa del grado nuevo (422 si tiene varias).

    Un alumno activo sin intervalos de pertenencia confirma en `pertenece_desde` desde qué
    día forma parte de su grupo (el final, con los demás cambios aplicados): es obligatorio
    para él y se rechaza para cualquier otro (CU014)."""

    nombres: Optional[Texto] = None
    apellidos: Optional[Texto] = None
    id_colegio: Optional[int] = None
    id_grado: Optional[int] = None
    id_seccion: Optional[int] = None
    id_programa: Optional[int] = None
    cambio_grupo_desde: Optional[date] = None
    pertenece_desde: Optional[date] = None


class CambioEstadoAlumno(BaseModel):
    """Activar o inactivar: desde qué día (hoy o antes) el alumno vuelve a su grupo o deja
    de pertenecer a él (CU014)."""

    aplicar_desde: date


# ── Listado y cabecera ──────────────────────────────────────────────────────────────

class AlumnoItem(BaseModel):
    id: int
    nombres: str
    apellidos: str
    colegio: ItemCatalogo
    grado: ItemCatalogo
    programa: ItemCatalogo
    # Calculado: Alfabetización -> III; Comprensión Lectora -> ciclo de su grado.
    ciclo: str
    seccion: ItemCatalogo
    activo: bool


class AlumnoDetalle(AlumnoItem):
    fecha_registro: date
    # Desde cuándo está en su grupo actual; null si está inactivo. Un cambio de grupo no
    # puede tener una fecha anterior.
    pertenece_desde: Optional[date] = None


# ── Pestañas del detalle (año escolar en curso) ─────────────────────────────────────

class NivelColocado(BaseModel):
    """Letra Raz-Kids en que el docente colocó al alumno en un Registro de Vuelo."""

    letra: str
    id_nivel_rk: int
    # Periodo (corte) del Registro de Vuelo del que sale.
    numero_periodo: int
    # Inicio, En proceso, Logrado o Avanzado según el grado actual del alumno; null si
    # su grado no tiene escala.
    nivel_descriptivo: Optional[str] = None


class AsistenciaDelAlumno(BaseModel):
    """% de asistencia del año (CU015): asistidas (presente o tardanza) sobre las clases
    contabilizadas, con 1 decimal; null ("Sin datos de asistencia") si no hay ninguna."""

    porcentaje: Optional[float] = None
    asistidas: int = 0
    contabilizadas: int = 0


class Resumen(BaseModel):
    # El del último PA del año con nivel colocado (Registro de Vuelo); null si ninguno.
    nivel_colocado: Optional[NivelColocado] = None
    # Suma del total de libros (LSN + LSL) de sus filas en las tablas de lectura del año
    # que se realizaron; 0 si no tiene ninguna.
    libros_leidos_anio: int = 0
    asistencia: AsistenciaDelAlumno = AsistenciaDelAlumno()


class PeriodoDeVuelo(BaseModel):
    """Un PA del Registro de Vuelo del alumno (CU015, gráfica): su decisión vigente en
    cualquier grupo (V1)."""

    numero: int
    id_periodo_academico: int
    # Letra de entrada; null si viene de un PA anterior sin nivel colocado.
    entrada: Optional[str] = None
    origen_entrada: Literal["grado", "periodo_anterior", "pendiente_periodo_anterior"]
    # null = "Sin registrar".
    nivel_colocado: Optional[str] = None
    # Para el eje Y de la gráfica (ids ascendentes = niveles ascendentes).
    id_nivel_colocado: Optional[int] = None
    proyeccion: Optional[str] = None
    # Nivel del programa según el grado de la decisión y el nivel colocado.
    nivel_programa: Optional[str] = None


class EvaluacionMensual(BaseModel):
    """Evaluación de la rúbrica mensual (CU029): nombres de los niveles; null = sin evaluar."""

    mes: int
    fluidez: Optional[str] = None
    comprension: Optional[str] = None
    estado: Literal["pendiente", "incompleta", "completada"]
    resultado_decimas: Optional[int] = None


class Rubrica(BaseModel):
    evaluaciones: list[EvaluacionMensual]


class LecturaDeLaSemana(BaseModel):
    """La fila del alumno en una tabla de lectura semanal (CU015)."""

    semana: int
    fecha_inicio: date
    fecha_fin: date
    mes: int
    id_programa: int
    estado_tabla: EstadoTabla
    motivo_no_realizada: Optional[str] = None
    # Asistencia en la clase de referencia; null = sin marcar.
    asistencia: Optional[EstadoAsistencia] = None
    lsn_titulo: Optional[str] = None
    lsn_nota: Optional[int] = None
    lsn_maximo: Optional[int] = None
    cantidad_lsl: Optional[int] = None
    total_libros: int
    observacion: Optional[str] = None
    estado_fila: EstadoFila


class CambioHistorial(BaseModel):
    campo: str
    anterior: Optional[str] = None
    nuevo: Optional[str] = None


class EventoHistorial(BaseModel):
    fecha: datetime
    usuario: str
    rol: str
    accion: str
    cambios: list[CambioHistorial]
