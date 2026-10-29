"""Distribución por subprograma y resultados de aprendizaje del PA (CU033 pasos 4 a 6,
CU035 paso 6).

El corte por colegio, los alumnos a evaluar y el resultado de cada alumno son los de
`app/services/dashboard/corte.py`. Un alumno sin resultado queda "Pendiente de evaluar"
y no entra en la base de los porcentajes.
"""
from collections import Counter, defaultdict

from sqlmodel import Session, col, select

from app.models.organizacion import ColegioPrograma, Programa
from app.schemas.dashboard import (
    DistribucionResultado,
    DistribucionSubprograma,
    NivelDesempeno,
    ResultadosAprendizaje,
    ResultadosSubprograma,
    SubprogramaAlumnos,
)
from app.services.dashboard.corte import (
    GLOBAL,
    Evaluaciones,
    cargar_evaluaciones,
    corte_del_periodo,
    item_programa,
    programas,
)
from app.services.dashboard.filtros import FiltrosResueltos
from app.services.dashboard.numeros import porcentaje
from app.services.rubrica_service import DIMENSIONES

MENSAJE_SIN_INFORMACION = "No hay información disponible para esta selección."
MENSAJE_SIN_EVALUACIONES = "No hay evaluaciones completas para esta selección."


# ── Distribución por subprograma (CU033) ────────────────────────────────────────────

def distribucion_subprograma(db: Session, filtros: FiltrosResueltos) -> DistribucionSubprograma:
    """Alumnos por subprograma. Con total > 0 aparecen todos los subprogramas, también
    los que tienen 0."""
    corte = corte_del_periodo(db, filtros)
    comunes = corte.encabezado(filtros)
    conteo = Counter(p.id_programa for p in corte.pertenencias.values())
    total = sum(conteo.values())
    if total == 0:
        return DistribucionSubprograma(
            **comunes, total=None, subprogramas=[], sin_datos=True, mensaje=MENSAJE_SIN_INFORMACION
        )
    return DistribucionSubprograma(
        **comunes,
        total=total,
        subprogramas=[
            SubprogramaAlumnos(
                subprograma=item_programa(p),
                alumnos=conteo[p.id_programa],
                porcentaje=porcentaje(conteo[p.id_programa], total),
            )
            for p in programas(db)
        ],
        sin_datos=False,
    )


# ── Resultados de aprendizaje (CU033 paso 6, CU035 paso 6) ──────────────────────────

def _distribucion(evaluaciones: Evaluaciones, id_programa: int, alumnos: list[int], tipo: str) -> DistribucionResultado:
    escala = evaluaciones.escala(id_programa, tipo)
    conteo = evaluaciones.conteo(alumnos, id_programa, tipo)
    evaluados = sum(conteo[clave] for clave, _ in escala)
    return DistribucionResultado(
        resultado=tipo,
        fuente="registro_vuelo" if tipo == GLOBAL else "rubrica",
        niveles=[
            NivelDesempeno(
                nombre=nombre, alumnos=conteo[clave], porcentaje=porcentaje(conteo[clave], evaluados) if evaluados else None
            )
            for clave, nombre in escala
        ],
        evaluados=evaluados,
        pendientes=len(alumnos) - evaluados,
        mensaje=None if evaluados else MENSAJE_SIN_EVALUACIONES,
    )


def _resultados_del_programa(evaluaciones: Evaluaciones, programa: Programa, alumnos: list[int]) -> ResultadosSubprograma:
    return ResultadosSubprograma(
        subprograma=item_programa(programa),
        total_a_evaluar=len(alumnos),
        distribuciones=[
            _distribucion(evaluaciones, programa.id_programa, alumnos, tipo) for tipo in (GLOBAL, *DIMENSIONES)
        ],
    )


def _programas_configurados(db: Session, ids_colegio: list[int]) -> list[Programa]:
    """Subprogramas configurados en alguno de los colegios incluidos, por id."""
    if not ids_colegio:
        return []
    return list(db.exec(
        select(Programa)
        .where(col(Programa.id_programa).in_(
            select(ColegioPrograma.id_programa).where(col(ColegioPrograma.id_colegio).in_(ids_colegio))
        ))
        .order_by(Programa.id_programa)
    ).all())


def resultados_aprendizaje(db: Session, filtros: FiltrosResueltos) -> ResultadosAprendizaje:
    corte = corte_del_periodo(db, filtros)
    comunes = corte.encabezado(filtros)
    if not corte.pertenencias:
        return ResultadosAprendizaje(**comunes, subprogramas=[], sin_datos=True, mensaje=MENSAJE_SIN_INFORMACION)
    por_programa: dict[int, list[int]] = defaultdict(list)
    for id_alumno, pertenencia in corte.pertenencias.items():
        por_programa[pertenencia.id_programa].append(id_alumno)
    evaluaciones = cargar_evaluaciones(db, list(corte.pertenencias), filtros.periodo.id_periodo_academico)
    return ResultadosAprendizaje(
        **comunes,
        subprogramas=[
            _resultados_del_programa(evaluaciones, programa, por_programa.get(programa.id_programa, []))
            for programa in _programas_configurados(db, list(corte.fechas))
        ],
        sin_datos=False,
    )
