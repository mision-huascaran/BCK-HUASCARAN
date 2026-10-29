"""Filtros comunes del Dashboard: año escolar, periodo académico y colegios.

Cada endpoint declara si es un bloque "del periodo" (`FiltrosDelPeriodo`: el PA es
obligatorio) o anual (`FiltrosAnuales`: el PA está prohibido). Los dos reciben los
colegios de una de dos formas, nunca las dos a la vez: `todos_los_colegios=true` (todo el
alcance) o `id_colegios` repetido en el query.

Ningún colegio se excluye en silencio: si uno no existe, está fuera del alcance o le falta
el calendario del periodo pedido, la consulta se rechaza. Tampoco se usa el calendario de
otro colegio (CU033, flujo "Falta un calendario aplicable").
"""
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Query, status
from sqlmodel import Session, col, select

from app.core.database import get_db
from app.core.errores import ErrorNegocio, error_de_campo
from app.core.texto import comparable_sql
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import AnioEscolar, Colegio, PeriodoAcademico, Usuario
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import AlcanceColegios, ContextoDashboard, IntervaloFechas, PeriodoVisible
from app.services.calendario import CalendarioResuelto, Calendarios, cargar_calendarios
from app.services.dashboard.roles import SupervisorODirectivo

CAMPO_ANIO = "id_anio_escolar"
CAMPO_PERIODO = "id_periodo_academico"
CAMPO_COLEGIOS = "id_colegios"

# Texto de CU034.
MENSAJE_SIN_COLEGIOS = "Selecciona un año escolar y al menos un colegio."
# Texto de CU033, flujo "Falta un calendario aplicable".
MENSAJE_SIN_CALENDARIO = (
    "No hay un calendario configurado para todos los colegios seleccionados. "
    "Revisa el año escolar o la selección de colegios."
)
MOTIVO_SIN_CALENDARIO = "calendario_no_configurado"
MOTIVO_FUERA_DE_ALCANCE = "colegio_fuera_de_alcance"

NOMBRES_VISIBLES_PERIODO = {1: "Primer periodo", 2: "Segundo periodo", 3: "Tercer periodo", 4: "Cuarto periodo"}


def nombre_visible_periodo(numero: int) -> str:
    """Nombre del PA para el Dashboard: 1 -> "Primer periodo". El resto del sistema sigue
    usando `calendario.nombre_periodo` ("PA1")."""
    return NOMBRES_VISIBLES_PERIODO[numero]


def periodo_visible(periodo: PeriodoAcademico) -> PeriodoVisible:
    return PeriodoVisible(
        id=periodo.id_periodo_academico, numero=periodo.numero, nombre=nombre_visible_periodo(periodo.numero)
    )


def item_colegio(colegio: Colegio) -> ItemCatalogo:
    return ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre)


def colegios_del_alcance(db: Session, usuario: Usuario) -> list[Colegio]:
    """Colegios que el usuario puede consultar en el Dashboard: todos los activos, igual
    para el Supervisor y el Directivo, ordenados por nombre sin mayúsculas ni tildes.

    Ningún CU define todavía cómo se asigna un alcance a un Supervisor o a un Directivo
    (decisión A1). Queda aislado aquí para cambiarlo en un solo lugar: "todos los
    colegios" del Dashboard es exactamente esta lista. `usuario` no se usa aún.
    """
    return list(
        db.exec(
            select(Colegio)
            .where(col(Colegio.activo).is_(True))
            .order_by(comparable_sql(Colegio.nombre), Colegio.id_colegio)
        ).all()
    )


# ── Validación ──────────────────────────────────────────────────────────────────────

def _anio(db: Session, id_anio_escolar: int) -> AnioEscolar:
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise error_de_campo(CAMPO_ANIO, "El año escolar seleccionado no existe.")
    return anio


def _periodo(
    db: Session, anio: AnioEscolar, id_periodo: Optional[int], requiere_periodo: bool
) -> Optional[PeriodoAcademico]:
    if not requiere_periodo:
        if id_periodo is not None:
            raise error_de_campo(CAMPO_PERIODO, "Este indicador es anual: no elijas un periodo académico.")
        return None
    if id_periodo is None:
        raise error_de_campo(CAMPO_PERIODO, "Selecciona un periodo académico.")
    periodo = db.get(PeriodoAcademico, id_periodo)
    if periodo is None or periodo.id_anio_escolar != anio.id_anio_escolar:
        raise error_de_campo(CAMPO_PERIODO, "El periodo académico no pertenece al año escolar seleccionado.")
    return periodo


@dataclass(frozen=True)
class SeleccionMultiple:
    """Un filtro "todos o una lista" (colegios, grados): su campo y sus mensajes."""

    campo: str
    vacia: str
    ambas: str
    repetidos: str

    def ids_elegidos(self, todos: bool, ids: Optional[list[int]]) -> Optional[list[int]]:
        """Los ids elegidos uno a uno, o None si se eligió "todos". Exactamente uno de los
        dos modos; una lista vacía no equivale a "todos"."""
        if todos and ids:
            raise error_de_campo(self.campo, self.ambas)
        if todos:
            return None
        if not ids:
            raise error_de_campo(self.campo, self.vacia)
        if len(set(ids)) != len(ids):
            raise error_de_campo(self.campo, self.repetidos)
        return ids


SELECCION_COLEGIOS = SeleccionMultiple(
    CAMPO_COLEGIOS,
    vacia=MENSAJE_SIN_COLEGIOS,
    ambas="Elige todos los colegios o una lista de colegios, no ambas cosas.",
    repetidos="No repitas colegios en la selección.",
)


def _colegios(
    db: Session, usuario: Usuario, todos_los_colegios: bool, id_colegios: Optional[list[int]]
) -> tuple[list[Colegio], AlcanceColegios]:
    pedidos = SELECCION_COLEGIOS.ids_elegidos(todos_los_colegios, id_colegios)
    alcance = colegios_del_alcance(db, usuario)
    if pedidos is None:
        if not alcance:
            raise error_de_campo(CAMPO_COLEGIOS, MENSAJE_SIN_COLEGIOS)
        return alcance, "todos"
    existentes = set(db.exec(select(Colegio.id_colegio).where(col(Colegio.id_colegio).in_(pedidos))).all())
    if existentes != set(pedidos):
        raise error_de_campo(CAMPO_COLEGIOS, "Uno de los colegios seleccionados no existe.")
    elegidos = [c for c in alcance if c.id_colegio in existentes]
    if len(elegidos) != len(pedidos):
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "Solo puedes consultar los colegios de tu alcance.",
            motivo=MOTIVO_FUERA_DE_ALCANCE,
        )
    return elegidos, "uno" if len(elegidos) == 1 else "varios"


def _calendarios(
    cargados: Calendarios, anio: AnioEscolar, periodo: Optional[PeriodoAcademico], colegios: list[Colegio]
) -> dict[int, CalendarioResuelto]:
    """{colegio: calendario aplicable del año}. En un bloque del periodo, todos deben
    tenerlo y con ese PA configurado; si no, 422 con la lista de los que no."""
    aplicables = {c.id_colegio: cargados.aplicable(c.id_colegio, anio.id_anio_escolar) for c in colegios}
    if periodo is not None:
        faltan = [
            c for c in colegios
            if aplicables[c.id_colegio] is None or aplicables[c.id_colegio].pa_de(periodo.id_periodo_academico) is None
        ]
        if faltan:
            raise ErrorNegocio(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                MENSAJE_SIN_CALENDARIO,
                motivo=MOTIVO_SIN_CALENDARIO,
                extra={"colegios_sin_calendario": [item_colegio(c).model_dump() for c in faltan]},
            )
    return {id_colegio: c for id_colegio, c in aplicables.items() if c is not None}


# ── Filtros resueltos ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FiltrosResueltos:
    """Filtros ya validados de una consulta del Dashboard."""

    anio: AnioEscolar
    # None en los bloques anuales.
    periodo: Optional[PeriodoAcademico]
    # En el orden del alcance (por nombre).
    colegios: list[Colegio]
    alcance: AlcanceColegios
    # id_colegio -> calendario aplicable del año. En un bloque del periodo están todos.
    calendarios: dict[int, CalendarioResuelto]
    # Los mismos calendarios ya cargados, para los servicios que resuelven por fecha
    # (p. ej. la programación de clases).
    cargados: Calendarios

    @property
    def ids_colegio(self) -> list[int]:
        return [c.id_colegio for c in self.colegios]

    def intervalo(self) -> IntervaloFechas:
        """Fechas que representa el bloque: del primer inicio al último fin de ese PA
        entre los calendarios de los colegios; en un bloque anual, las del año escolar."""
        if self.periodo is None:
            return IntervaloFechas(desde=self.anio.fecha_inicio, hasta=self.anio.fecha_fin)
        pas = [c.pa_de(self.periodo.id_periodo_academico) for c in self.calendarios.values()]
        return IntervaloFechas(desde=min(p.fecha_inicio for p in pas), hasta=max(p.fecha_fin for p in pas))

    def contexto(self) -> ContextoDashboard:
        intervalo = self.intervalo()
        return ContextoDashboard(
            anio_escolar=ItemCatalogo(id=self.anio.id_anio_escolar, nombre=self.anio.nombre),
            periodo=periodo_visible(self.periodo) if self.periodo is not None else None,
            colegios=[item_colegio(c) for c in self.colegios],
            alcance=self.alcance,
            fecha_consulta=ahora_utc(),
            intervalo=intervalo,
            parcial=intervalo.desde <= hoy_lima() <= intervalo.hasta,
        )


def resolver_filtros(
    db: Session,
    usuario: Usuario,
    id_anio_escolar: int,
    id_periodo_academico: Optional[int],
    todos_los_colegios: bool,
    id_colegios: Optional[list[int]],
    requiere_periodo: bool,
) -> FiltrosResueltos:
    anio = _anio(db, id_anio_escolar)
    periodo = _periodo(db, anio, id_periodo_academico, requiere_periodo)
    colegios, alcance = _colegios(db, usuario, todos_los_colegios, id_colegios)
    cargados = cargar_calendarios(db, anio.id_anio_escolar, ids_colegio=[c.id_colegio for c in colegios])
    calendarios = _calendarios(cargados, anio, periodo, colegios)
    return FiltrosResueltos(anio, periodo, colegios, alcance, calendarios, cargados)


def _dependencia(requiere_periodo: bool, descripcion_periodo: str):
    def filtros(
        db: Annotated[Session, Depends(get_db)],
        usuario: SupervisorODirectivo,
        id_anio_escolar: int,
        id_periodo_academico: Annotated[Optional[int], Query(description=descripcion_periodo)] = None,
        todos_los_colegios: Annotated[bool, Query(description="Todo el alcance. Excluye id_colegios.")] = False,
        id_colegios: Annotated[
            Optional[list[int]], Query(description="Colegios elegidos (repetido). Excluye todos_los_colegios.")
        ] = None,
    ) -> FiltrosResueltos:
        return resolver_filtros(
            db, usuario, id_anio_escolar, id_periodo_academico, todos_los_colegios, id_colegios, requiere_periodo
        )

    return filtros


FiltrosDelPeriodo = Annotated[FiltrosResueltos, Depends(_dependencia(True, "Obligatorio: PA del año elegido."))]
FiltrosAnuales = Annotated[FiltrosResueltos, Depends(_dependencia(False, "Prohibido: este bloque es anual."))]
