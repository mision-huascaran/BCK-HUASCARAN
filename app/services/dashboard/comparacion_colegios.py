"""Comparación entre colegios del Directivo (CU035, paso 9): un indicador a la vez, sin
combinarlos en una puntuación general.

Cada indicador sale de la misma función que su bloque, así que el valor de un colegio es
el mismo que muestra ese bloque con los mismos filtros:
- Nivel global de Alfabetización o de Comprensión Lectora: el índice de 1 a 4 de la
  comparación de resultados (`comparacion.indices_globales_por_colegio`, "Resumen por
  colegio" con todos los grados) en el PA seleccionado. Población: alumnos evaluados.
- Asistencia: el porcentaje del bloque de asistencia por colegio en el PA seleccionado.
  Población: asistencias registradas (P + T + A).
- Promedio de libros por alumno: el de CU034, acumulado del año. Población: alumnos
  incluidos.

Orden de mayor a menor por el valor exacto, empates 1, 1, 3 en orden alfabético. Con
información pendiente, la posición es provisional. Los colegios sin información van aparte,
sin cero ni posición. Con un solo colegio seleccionado, o con uno solo que tenga
información, no hay posiciones.
"""
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from sqlmodel import Session, select

from app.core.texto import comparable
from app.core.tiempo import hoy_lima
from app.models.organizacion import Colegio, Programa
from app.schemas.dashboard import (
    ColegioComparado,
    ColegioSinComparacion,
    ComparacionColegios,
    PoblacionComparada,
)
from app.services.dashboard.asistencia import clases_por_colegio
from app.services.dashboard.comparacion import indices_globales_por_colegio
from app.services.dashboard.corte import PERIODO_NO_INICIADO, fechas_de_corte
from app.services.dashboard.filtros import FiltrosResueltos, item_colegio
from app.services.dashboard.libros import INCOMPLETO, motivo_sin_promedio, promedios_por_colegio
from app.services.dashboard.numeros import dos_decimales, ranking
from app.services.motor_sugerencias import ALFABETIZACION, COMPRENSION_LECTORA

NIVEL_ALFABETIZACION, NIVEL_COMPRENSION = "nivel_global_alfabetizacion", "nivel_global_comprension"
ASISTENCIA, PROMEDIO_LIBROS = "asistencia", "promedio_libros"
PROGRAMA_DEL_INDICADOR = {NIVEL_ALFABETIZACION: ALFABETIZACION, NIVEL_COMPRENSION: COMPRENSION_LECTORA}

MENSAJE_UN_COLEGIO = "Selecciona al menos dos colegios para compararlos."
MENSAJE_UNO_CON_DATOS = "No hay información suficiente de otros colegios para establecer una comparación."


def requiere_periodo(indicador: str) -> bool:
    """Los resultados de aprendizaje y la asistencia son del PA; los libros, del año."""
    return indicador != PROMEDIO_LIBROS


@dataclass(frozen=True)
class ResultadoColegio:
    id_colegio: int
    exacto: Fraction
    valor: float
    poblacion: int
    advertencias: list[str]
    provisional: bool


@dataclass(frozen=True)
class Resultados:
    """Los colegios con el indicador y el motivo de cada uno que no lo tiene."""

    con_informacion: list[ResultadoColegio]
    motivos: dict[int, str]


# ── Un medidor por indicador ────────────────────────────────────────────────────────

def _nivel_global(db: Session, filtros: FiltrosResueltos, nombre_programa: str) -> Resultados:
    programa = db.exec(select(Programa).where(Programa.nombre == nombre_programa)).first()
    if programa is None:
        return Resultados([], dict.fromkeys(filtros.ids_colegio, "no_aplica"))
    medicion = indices_globales_por_colegio(db, filtros, programa)
    motivos = {c.id_colegio: PERIODO_NO_INICIADO for c in medicion.corte.sin_informacion}
    motivos.update({g.colegio.id_colegio: "no_aplica" for g in medicion.no_aplica})
    motivos.update({m.grupo.colegio.id_colegio: "sin_evaluaciones" for m in medicion.sin_evaluaciones})
    resultados = [
        ResultadoColegio(
            m.grupo.colegio.id_colegio, m.indice(), dos_decimales(m.indice()), m.evaluados,
            ["evaluacion_incompleta"] if m.pendientes else [], m.pendientes > 0,
        )
        for m in medicion.con_evaluaciones
    ]
    return Resultados(resultados, motivos)


def _asistencia(db: Session, filtros: FiltrosResueltos) -> Resultados:
    _, sin_iniciar = fechas_de_corte(filtros, hoy_lima())
    motivos = {c.id_colegio: PERIODO_NO_INICIADO for c in sin_iniciar}
    resultados = []
    for id_colegio, clases in clases_por_colegio(db, filtros).items():
        conteo = clases.conteo()
        if conteo.proporcion is None:
            motivos.setdefault(id_colegio, "sin_estados_registrados")
            continue
        advertencias = [
            nombre for nombre, hay in (("registro_incompleto", conteo.clases_incompletas),
                                       ("clases_sin_registro", conteo.clases_sin_registro)) if hay
        ]
        resultados.append(ResultadoColegio(
            id_colegio, conteo.proporcion, conteo.porcentaje, conteo.completadas, advertencias, conteo.provisional
        ))
    return Resultados(resultados, motivos)


def _libros(db: Session, filtros: FiltrosResueltos) -> Resultados:
    resultados, motivos = [], {}
    for id_colegio, acumulado in promedios_por_colegio(db, filtros).items():
        promedio = acumulado.promedio
        if promedio is None:
            motivos[id_colegio] = motivo_sin_promedio(filtros, id_colegio, acumulado)
            continue
        incompleto = acumulado.estado == INCOMPLETO
        resultados.append(ResultadoColegio(
            id_colegio, promedio, dos_decimales(promedio), len(acumulado.alumnos),
            ["informacion_incompleta"] if incompleto else [], incompleto,
        ))
    return Resultados(resultados, motivos)


MEDIDORES: dict[str, Callable[[Session, FiltrosResueltos], Resultados]] = {
    NIVEL_ALFABETIZACION: lambda db, f: _nivel_global(db, f, ALFABETIZACION),
    NIVEL_COMPRENSION: lambda db, f: _nivel_global(db, f, COMPRENSION_LECTORA),
    ASISTENCIA: _asistencia,
    PROMEDIO_LIBROS: _libros,
}
POBLACIONES = {
    NIVEL_ALFABETIZACION: "alumnos evaluados",
    NIVEL_COMPRENSION: "alumnos evaluados",
    ASISTENCIA: "asistencias registradas",
    PROMEDIO_LIBROS: "alumnos incluidos",
}


# ── Comparación ─────────────────────────────────────────────────────────────────────

def _comparado(
    colegio: Colegio, resultado: ResultadoColegio, posicion: Optional[int], descripcion: str
) -> ColegioComparado:
    return ColegioComparado(
        posicion=posicion, provisional=resultado.provisional, colegio=item_colegio(colegio), valor=resultado.valor,
        poblacion=PoblacionComparada(descripcion=descripcion, cantidad=resultado.poblacion),
        advertencias=resultado.advertencias,
    )


def comparacion_colegios(db: Session, filtros: FiltrosResueltos, indicador: str) -> ComparacionColegios:
    resultados = MEDIDORES[indicador](db, filtros)
    colegios = {c.id_colegio: c for c in filtros.colegios}
    alfabetico = {c.id_colegio: (comparable(c.nombre), c.id_colegio) for c in filtros.colegios}
    descripcion = POBLACIONES[indicador]
    ordenados = ranking(resultados.con_informacion, lambda r: r.exacto, lambda r: alfabetico[r.id_colegio])
    comparable_entre_colegios = len(filtros.colegios) > 1 and len(ordenados) > 1
    mensaje = None
    if len(filtros.colegios) == 1:
        mensaje = MENSAJE_UN_COLEGIO
    elif len(ordenados) == 1:
        mensaje = MENSAJE_UNO_CON_DATOS
    con_informacion = {r.id_colegio for r in resultados.con_informacion}
    return ComparacionColegios(
        contexto=filtros.contexto(),
        indicador=indicador,
        intervalo="periodo" if requiere_periodo(indicador) else "anio",
        ranking=[
            _comparado(colegios[r.id_colegio], r, posicion if comparable_entre_colegios else None, descripcion)
            for posicion, r in ordenados
        ],
        sin_informacion=[
            ColegioSinComparacion(colegio=item_colegio(c), motivo=resultados.motivos.get(c.id_colegio, "sin_alumnos"))
            for c in filtros.colegios if c.id_colegio not in con_informacion
        ],
        mensaje=mensaje,
    )
