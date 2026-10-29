"""Dashboard del Supervisor y del Directivo (CU033, CU034 y CU035)."""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_db
from app.schemas.dashboard import (
    AsistenciaDelPeriodo, AsistenciaPorClase, ComparacionResultados, DesgloseLibros, DistribucionSubprograma,
    FiltrosDisponibles, LibrosDelAnio, LibrosDelColegio, ResultadosAprendizaje, TipoResultado, VistaComparacion,
)
from app.services.dashboard.aprendizaje import distribucion_subprograma, resultados_aprendizaje
from app.services.dashboard.asistencia import asistencia_del_periodo, asistencia_por_clase
from app.services.dashboard.carga_inicial import filtros_disponibles
from app.services.dashboard.comparacion import ConsultaComparacion, comparacion_resultados
from app.services.dashboard.filtros import FiltrosAnuales, FiltrosDelPeriodo
from app.services.dashboard.libros import desglose_libros, libros_del_anio, libros_del_colegio
from app.services.dashboard.roles import SoloSupervisor, SupervisorODirectivo

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Db = Annotated[Session, Depends(get_db)]


@router.get("/filtros", response_model=FiltrosDisponibles)
def filtros(db: Db, usuario: SupervisorODirectivo):
    """Carga inicial de CU033 y CU035 (paso 3): colegios del alcance, años escolares con
    sus PA (nombre visible "Primer periodo"...) y la selección inicial.

    `seleccion_inicial` es el PA en que están hoy todos los colegios del alcance (en un
    receso, el PA anterior y su año). Es null si no coinciden o si hoy no cae en ningún PA
    ni PR configurado: el front pide elegir año y periodo."""
    return filtros_disponibles(db, usuario)


@router.get("/distribucion-subprograma", response_model=DistribucionSubprograma)
def distribucion(db: Db, _usuario: SoloSupervisor, filtros: FiltrosDelPeriodo):
    """CU033: alumnos por subprograma (cantidad y porcentaje sobre el total) según su
    pertenencia en la fecha de corte de su colegio: el fin del PA en su calendario o, si
    sigue en curso, hoy (`fechas_corte`). Un colegio con el PA sin iniciar va en
    `colegios_sin_informacion`. Sin alumnos: `sin_datos` con el mensaje del CU.

    Solo Supervisor. Errores: los de los filtros comunes del Dashboard."""
    return distribucion_subprograma(db, filtros)


@router.get("/resultados-aprendizaje", response_model=ResultadosAprendizaje)
def resultados(db: Db, _usuario: SupervisorODirectivo, filtros: FiltrosDelPeriodo):
    """CU033 (paso 6) y CU035 (paso 6): por subprograma configurado, el resultado global
    (nivel del programa de la decisión vigente del Registro de Vuelo) y las dimensiones
    Fluidez y Comprensión (última evaluación mensual completa del PA). Todos los niveles de
    cada escala, también los de 0; porcentajes sobre los evaluados y los pendientes aparte.

    Supervisor y Directivo (solo agregados). Errores: los de los filtros comunes."""
    return resultados_aprendizaje(db, filtros)


@router.get("/comparacion-resultados", response_model=ComparacionResultados)
def comparacion(
    db: Db,
    _usuario: SoloSupervisor,
    filtros: FiltrosDelPeriodo,
    id_programa: int,
    todos_los_grados: Annotated[bool, Query(description="Todos los grados. Excluye id_grados.")] = False,
    id_grados: Annotated[Optional[list[int]], Query(description="Grados elegidos (repetido).")] = None,
    vista: VistaComparacion = "resumen_colegio",
    resultado: TipoResultado = "global",
):
    """CU033 (paso 7): compara un subprograma entre los colegios (`resumen_colegio`) o
    entre colegio y grado (`detalle_grado`) en los grados elegidos.

    `global`: índice 1-4 del nivel del programa del Registro de Vuelo sobre los evaluados
    del grupo (sin promediar grados), de mayor a menor por el valor exacto, con empates
    1, 1, 3 en orden alfabético; con pendientes, "Evaluación incompleta" y posición
    provisional. `fluidez`, `comprension` y `nivel_alfabetico`: solo distribuciones, en
    orden alfabético. Aparte, `sin_evaluaciones` y `no_aplica` (subprograma no
    configurado en el colegio).

    Solo Supervisor. Errores: los de los filtros comunes; 422 en `id_programa` si no
    existe y en `id_grados` si la selección está vacía, tiene repetidos o se combina con
    `todos_los_grados`."""
    consulta = ConsultaComparacion(id_programa, todos_los_grados, id_grados, vista, resultado)
    return comparacion_resultados(db, filtros, consulta)


@router.get("/asistencia", response_model=AsistenciaDelPeriodo)
def asistencia(db: Db, _usuario: SupervisorODirectivo, filtros: FiltrosDelPeriodo):
    """CU033 (paso 8) y CU035 (paso 7): asistencia de las clases del PA hasta hoy, con las
    fechas del PA en el calendario de cada colegio. Solo cuentan los estados confirmados:
    porcentaje = (P + T) / (P + T + A) × 100 sobre el conjunto; sin estados, null y
    "Sin información". `provisional` si hay clases dictadas con alumnos sin marcar; las
    clases programadas ya transcurridas sin ninguna asistencia van en
    `clases_sin_registro` (no son dictadas ni ausencias). Incluye la serie mensual
    (`futuro`, `en_curso`, `sin_informacion`, `con_datos`) y el resumen por colegio.

    Supervisor y Directivo. Errores: los de los filtros comunes."""
    return asistencia_del_periodo(db, filtros)


@router.get("/asistencia/clases", response_model=AsistenciaPorClase)
def asistencia_clases(
    db: Db,
    usuario: SoloSupervisor,
    filtros: FiltrosDelPeriodo,
    id_colegio: int,
    id_grado: Optional[int] = None,
    id_seccion: Optional[int] = None,
):
    """CU033: detalle por grado y sección de un colegio de la selección. Una fila por clase
    dictada (fecha efectiva, lista histórica, estados, porcentaje y `completa` o
    `registro_incompleto`) y, aparte, las clases sin registro, sin conteos.

    Solo Supervisor. Errores: los de los filtros comunes; 422 en `id_colegio` si no existe
    o no está en la selección (403 `colegio_fuera_de_alcance` si está fuera del alcance),
    422 en `id_grado` o `id_seccion` si no son de ese colegio."""
    return asistencia_por_clase(db, usuario, filtros, id_colegio, id_grado, id_seccion)


@router.get("/libros", response_model=LibrosDelAnio)
def libros(db: Db, _usuario: SupervisorODirectivo, filtros: FiltrosAnuales):
    """CU034 y CU035: libros leídos del año escolar (LSN + LSL), atribuidos al mes de la
    clase de referencia, en el año de cada colegio hasta hoy. Total y meses con su estado
    (`completo`, `incompleto`, `sin_informacion`, `futuro`; total null sin información) y
    colegios por promedio de libros por alumno (alumnos de las listas históricas), con
    empates 1, 1, 3; los que no tienen promedio van aparte, sin posición. La completitud
    sale de los registros esperados de Seguimiento de Lectura (la regla de CU011).

    Supervisor y Directivo (sin nombres de alumnos). Errores: los de los filtros comunes."""
    return libros_del_anio(db, filtros)


@router.get("/libros/colegios/{id_colegio}", response_model=LibrosDelColegio)
def libros_colegio(db: Db, usuario: SoloSupervisor, filtros: FiltrosAnuales, id_colegio: int):
    """CU034 (paso 6): el colegio por grado (de la pertenencia en la fecha de cada clase de
    referencia), ordenados por promedio, y en cada grado sus alumnos con sus meses.

    Solo Supervisor. Errores: los de los filtros comunes; 422 en `id_colegio` si no existe
    o no está en la selección, 403 `colegio_fuera_de_alcance` si está fuera del alcance."""
    return libros_del_colegio(db, usuario, filtros, id_colegio)


@router.get("/libros/desglose", response_model=DesgloseLibros)
def libros_desglose(
    db: Db,
    usuario: SoloSupervisor,
    filtros: FiltrosAnuales,
    id_alumno: int,
    anio: int,
    mes: Annotated[int, Query(ge=1, le=12)],
):
    """CU034 (paso 8): los registros de Seguimiento de Lectura que componen la cantidad de
    un alumno en un mes (por la fecha de la clase de referencia), en los colegios
    seleccionados, con su clase, subprograma, grupo y cantidades.

    Solo Supervisor. Errores: los de los filtros comunes; 422 en `id_alumno` si no existe o
    no perteneció en el año a ningún colegio seleccionado, 403 `colegio_fuera_de_alcance` si
    solo perteneció a colegios fuera del alcance."""
    return desglose_libros(db, usuario, filtros, id_alumno, anio, mes)
