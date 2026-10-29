"""Comparación de resultados de aprendizaje (CU033, paso 7).

Dentro de un subprograma y un PA, compara los colegios ("Resumen por colegio": los
alumnos de los grados elegidos de cada colegio, juntos) o cada colegio y grado ("Detalle
por grado", con el grado de la pertenencia al corte). Los alumnos a evaluar y su
resultado son los de `corte.py` (los mismos de los resultados de aprendizaje).

Resultado global: índice = suma de los valores (Inicio 1, En proceso 2, Logrado 3,
Avanzado 4) / alumnos evaluados del grupo, calculado sobre el conjunto (nunca el promedio de
los índices de los grados). Los pendientes no valen 0 ni entran al divisor. Orden de mayor a
menor por el valor exacto; con el mismo índice exacto comparten posición (1, 1, 3) y van en
orden alfabético (colegio y luego grado escolar). Un grupo con pendientes entra al ranking
como "Evaluación incompleta", con la posición provisional (A11).

Fluidez, Comprensión y nivel alfabético: solo distribuciones en su escala, en orden
alfabético, sin índice ni posición.

Aparte: los grupos sin ninguna evaluación completa (`sin_evaluaciones`) y los colegios
donde el subprograma no está configurado (`no_aplica`). Un grupo sin alumnos a evaluar con
el subprograma configurado no aparece en ninguna lista.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.core.texto import comparable
from app.models.evaluacion import NIVELES_DESCRIPTIVOS
from app.models.organizacion import Colegio, ColegioGrado, Grado, Programa
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import (
    ComparacionResultados,
    GrupoComparado,
    GrupoNoAplica,
    GrupoSinEvaluaciones,
    NivelEscala,
    NivelGrupo,
)
from app.services.dashboard.corte import (
    GLOBAL,
    CorteDelPeriodo,
    Evaluaciones,
    cargar_evaluaciones,
    corte_del_periodo,
    item_programa,
    programas_por_colegio,
)
from app.services.dashboard.filtros import FiltrosResueltos, SeleccionMultiple, item_colegio
from app.services.dashboard.numeros import dos_decimales, porcentaje

RESUMEN_COLEGIO, DETALLE_GRADO = "resumen_colegio", "detalle_grado"
VALOR_INDICE = {nivel: valor for valor, nivel in enumerate(NIVELES_DESCRIPTIVOS, start=1)}

SELECCION_GRADOS = SeleccionMultiple(
    "id_grados",
    vacia="Selecciona al menos un grado.",
    ambas="Elige todos los grados o una lista de grados, no ambas cosas.",
    repetidos="No repitas grados en la selección.",
)


@dataclass(frozen=True)
class ConsultaComparacion:
    id_programa: int
    todos_los_grados: bool
    id_grados: Optional[list[int]]
    vista: str
    resultado: str


# ── Validación ──────────────────────────────────────────────────────────────────────

def _programa(db: Session, id_programa: int) -> Programa:
    programa = db.get(Programa, id_programa)
    if programa is None:
        raise error_de_campo("id_programa", "El subprograma seleccionado no existe.")
    return programa


def _grados(db: Session, todos: bool, ids: Optional[list[int]]) -> list[Grado]:
    """Los grados elegidos en orden escolar; "todos los grados" son todos los del catálogo."""
    elegidos = SELECCION_GRADOS.ids_elegidos(todos, ids)
    consulta = select(Grado).order_by(Grado.id_grado)
    if elegidos is not None:
        consulta = consulta.where(col(Grado.id_grado).in_(elegidos))
    grados = list(db.exec(consulta).all())
    if elegidos is not None and len(grados) != len(elegidos):
        raise error_de_campo(SELECCION_GRADOS.campo, "Uno de los grados seleccionados no existe.")
    return grados


# ── Grupos ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Grupo:
    colegio: Colegio
    grado: Optional[Grado]
    alumnos: list[int]

    def orden_alfabetico(self) -> tuple:
        return comparable(self.colegio.nombre), self.colegio.id_colegio, self.grado.id_grado if self.grado else 0

    def items(self) -> dict:
        return {
            "colegio": item_colegio(self.colegio),
            "grado": ItemCatalogo(id=self.grado.id_grado, nombre=self.grado.nombre) if self.grado else None,
        }


def _grupos(corte: CorteDelPeriodo, id_programa: int, grados: dict[int, Grado], vista: str) -> list[_Grupo]:
    """Grupos con alumnos a evaluar del subprograma en los grados elegidos."""
    alumnos: dict[tuple[int, Optional[int]], list[int]] = defaultdict(list)
    for id_alumno, pertenencia in corte.pertenencias.items():
        if pertenencia.id_programa == id_programa and pertenencia.id_grado in grados:
            grado = pertenencia.id_grado if vista == DETALLE_GRADO else None
            alumnos[(pertenencia.id_colegio, grado)].append(id_alumno)
    return [
        _Grupo(corte.colegios[id_colegio], grados.get(id_grado), lista)
        for (id_colegio, id_grado), lista in alumnos.items()
    ]


def _no_aplica(
    db: Session, corte: CorteDelPeriodo, id_programa: int, grados: dict[int, Grado], vista: str
) -> list[_Grupo]:
    """Colegios (o colegio y grado elegido que el colegio tiene) sin el subprograma."""
    configurados = programas_por_colegio(db, list(corte.fechas))
    sin_programa = [corte.colegios[c] for c, programas in configurados.items() if id_programa not in programas]
    if vista != DETALLE_GRADO or not sin_programa:
        return sorted((_Grupo(c, None, []) for c in sin_programa), key=_Grupo.orden_alfabetico)
    por_id = {c.id_colegio: c for c in sin_programa}
    filas = db.exec(select(ColegioGrado.id_colegio, ColegioGrado.id_grado).where(
        col(ColegioGrado.id_colegio).in_(list(por_id)), col(ColegioGrado.id_grado).in_(list(grados)),
    )).all()
    return sorted((_Grupo(por_id[c], grados[g], []) for c, g in filas), key=_Grupo.orden_alfabetico)


# ── Medición y ranking ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Medido:
    grupo: _Grupo
    escala: list[tuple[object, str]]
    conteo: Counter
    evaluados: int

    @property
    def pendientes(self) -> int:
        return len(self.grupo.alumnos) - self.evaluados

    def indice(self) -> Fraction:
        """Valor exacto: suma de los valores de los evaluados / evaluados."""
        return Fraction(sum(VALOR_INDICE[nivel] * self.conteo[nivel] for nivel, _ in self.escala), self.evaluados)

    def comparado(self, posicion: Optional[int] = None, indice: Optional[Fraction] = None) -> GrupoComparado:
        incompleta = self.pendientes > 0
        return GrupoComparado(
            posicion=posicion,
            provisional=incompleta if indice is not None else None,
            evaluacion_incompleta=incompleta,
            **self.grupo.items(),
            indice=dos_decimales(indice) if indice is not None else None,
            total_a_evaluar=len(self.grupo.alumnos),
            evaluados=self.evaluados,
            pendientes=self.pendientes,
            distribucion=[
                NivelGrupo(codigo=nombre, alumnos=self.conteo[clave], porcentaje=porcentaje(self.conteo[clave], self.evaluados))
                for clave, nombre in self.escala
            ],
        )

    def sin_evaluaciones(self) -> GrupoSinEvaluaciones:
        return GrupoSinEvaluaciones(
            **self.grupo.items(), total_a_evaluar=len(self.grupo.alumnos), evaluados=0, pendientes=self.pendientes
        )


def _medir(grupo: _Grupo, evaluaciones: Evaluaciones, id_programa: int, tipo: str) -> _Medido:
    escala = evaluaciones.escala(id_programa, tipo)
    conteo = evaluaciones.conteo(grupo.alumnos, id_programa, tipo)
    return _Medido(grupo, escala, conteo, sum(conteo[clave] for clave, _ in escala))


def _ranking_global(medidos: list[_Medido]) -> list[GrupoComparado]:
    """De mayor a menor índice exacto; empates exactos: misma posición (1, 1, 3)."""
    con_indice = sorted(((m.indice(), m) for m in medidos), key=lambda par: (-par[0], par[1].grupo.orden_alfabetico()))
    ranking, posicion, anterior = [], 0, None
    for lugar, (indice, medido) in enumerate(con_indice, start=1):
        if indice != anterior:
            posicion, anterior = lugar, indice
        ranking.append(medido.comparado(posicion, indice))
    return ranking


def _escala(evaluaciones: Evaluaciones, id_programa: int, tipo: str) -> list[NivelEscala]:
    return [
        NivelEscala(codigo=nombre, nombre=nombre, valor_indice=VALOR_INDICE[nombre] if tipo == GLOBAL else None)
        for _, nombre in evaluaciones.escala(id_programa, tipo)
    ]


def comparacion_resultados(db: Session, filtros: FiltrosResueltos, consulta: ConsultaComparacion) -> ComparacionResultados:
    programa = _programa(db, consulta.id_programa)
    grados = {g.id_grado: g for g in _grados(db, consulta.todos_los_grados, consulta.id_grados)}
    corte = corte_del_periodo(db, filtros)
    grupos = _grupos(corte, programa.id_programa, grados, consulta.vista)
    evaluaciones = cargar_evaluaciones(
        db, [a for g in grupos for a in g.alumnos], filtros.periodo.id_periodo_academico
    )
    medidos = [_medir(g, evaluaciones, programa.id_programa, consulta.resultado) for g in grupos]
    con_evaluaciones = [m for m in medidos if m.evaluados > 0]
    if consulta.resultado == GLOBAL:
        ranking = _ranking_global(con_evaluaciones)
    else:
        ranking = [m.comparado() for m in sorted(con_evaluaciones, key=lambda m: m.grupo.orden_alfabetico())]
    sin_evaluaciones = sorted((m for m in medidos if m.evaluados == 0), key=lambda m: m.grupo.orden_alfabetico())
    return ComparacionResultados(
        **corte.encabezado(filtros),
        subprograma=item_programa(programa),
        grados=[ItemCatalogo(id=g.id_grado, nombre=g.nombre) for g in grados.values()],
        vista=consulta.vista,
        resultado=consulta.resultado,
        escala=_escala(evaluaciones, programa.id_programa, consulta.resultado),
        ranking=ranking,
        sin_evaluaciones=[m.sin_evaluaciones() for m in sin_evaluaciones],
        no_aplica=[GrupoNoAplica(**g.items()) for g in _no_aplica(db, corte, programa.id_programa, grados, consulta.vista)],
    )
