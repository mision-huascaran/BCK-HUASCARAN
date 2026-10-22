"""Calendario académico por colegio (CU027, diseño v4 §3 y §8). Solo lectura.

Cada año escolar tiene un calendario general y cada colegio puede tener uno particular
activo. El calendario de un colegio en un año es su particular activo si existe; si no,
el general. Define 8 periodos con fechas inclusivas (PA1, PR1, ..., PA4, PR4) y días no
lectivos (rangos dentro de un PA).

Regla de todo el backend: "día" y "periodo" dependen del colegio. Toda operación sobre un
colegio pregunta con `periodo_de(db, id_colegio, fecha)` o, si evalúa muchos colegios o
fechas en una petición, carga los calendarios una sola vez (`cargar_calendarios`) y
resuelve en memoria. Una fecha que no cae en ningún periodo configurado no tiene periodo:
no se aproxima (CU027). Solo lo que no pertenece a un colegio (catálogos generales, seed)
usa el calendario general (`periodo_general_de`).
"""
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, false, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.organizacion import (
    TIPO_PA,
    TIPO_PR,
    AnioEscolar,
    Calendario,
    CalendarioPeriodo,
    Colegio,
    DiaNoLectivo,
    PeriodoAcademico,
)

VIERNES = 4
UN_DIA = timedelta(days=1)
NUMEROS_PA = (1, 2, 3, 4)
# Orden cronológico obligatorio de los 8 periodos de un calendario.
ORDEN_PERIODOS = tuple((tipo, n) for n in NUMEROS_PA for tipo in (TIPO_PA, TIPO_PR))


def nombre_periodo(tipo: str, numero: int) -> str:
    """'PA2', 'PR4'."""
    return f"{tipo}{numero}"


@dataclass(frozen=True)
class PeriodoCalendario:
    """Un periodo de un calendario con sus fechas (inclusivas).

    `id_periodo_academico`: el PA n del año; para un PR n, el del PA anterior (n). None
    solo si el año todavía no tiene esa fila en `periodo_academico`.
    """

    tipo: str
    numero: int
    id_periodo_academico: Optional[int]
    fecha_inicio: date
    fecha_fin: date

    @property
    def es_pa(self) -> bool:
        return self.tipo == TIPO_PA

    @property
    def nombre(self) -> str:
        return nombre_periodo(self.tipo, self.numero)

    def contiene(self, fecha: date) -> bool:
        return self.fecha_inicio <= fecha <= self.fecha_fin


@dataclass(frozen=True)
class RangoNoLectivo:
    motivo: str
    fecha_inicio: date
    fecha_fin: date
    origen: str

    def contiene(self, fecha: date) -> bool:
        return self.fecha_inicio <= fecha <= self.fecha_fin


def _vigente_proximo_o_ultimo(pas: list[PeriodoCalendario], hoy: date) -> Optional[PeriodoCalendario]:
    """De unos PA en orden cronológico: el que contiene hoy; si no, el próximo; si no
    queda ninguno por empezar, el último. None si no hay ninguno."""
    actual = next((p for p in pas if p.contiene(hoy)), None)
    proximo = next((p for p in pas if p.fecha_inicio > hoy), None)
    return actual or proximo or (pas[-1] if pas else None)


@dataclass(frozen=True)
class CalendarioResuelto:
    """Un calendario ya cargado: responde sin volver a la BD."""

    id_calendario: int
    id_anio_escolar: int
    id_colegio: Optional[int]
    version: int
    # En orden cronológico (PA1, PR1, ..., PR4).
    periodos: tuple[PeriodoCalendario, ...]
    # Ordenados por fecha de inicio.
    no_lectivos: tuple[RangoNoLectivo, ...] = ()

    @property
    def es_general(self) -> bool:
        return self.id_colegio is None

    def periodo_de(self, fecha: date) -> Optional[PeriodoCalendario]:
        return next((p for p in self.periodos if p.contiene(fecha)), None)

    def pa(self, numero: int) -> Optional[PeriodoCalendario]:
        return next((p for p in self.periodos if p.es_pa and p.numero == numero), None)

    def pa_de(self, id_periodo_academico: int) -> Optional[PeriodoCalendario]:
        return next(
            (p for p in self.periodos if p.es_pa and p.id_periodo_academico == id_periodo_academico), None
        )

    def no_lectivo_de(self, fecha: date) -> Optional[RangoNoLectivo]:
        """El día no lectivo que contiene la fecha, o None."""
        inicios = [r.fecha_inicio for r in self.no_lectivos]
        candidato = bisect_right(inicios, fecha) - 1
        if candidato >= 0 and self.no_lectivos[candidato].contiene(fecha):
            return self.no_lectivos[candidato]
        return None

    def es_lectivo(self, fecha: date) -> bool:
        """Lunes a viernes, dentro de un PA y fuera de los días no lectivos."""
        periodo = self.periodo_de(fecha)
        return (
            fecha.weekday() <= VIERNES
            and periodo is not None
            and periodo.es_pa
            and self.no_lectivo_de(fecha) is None
        )

    def dias_lectivos(self, desde: date, hasta: date) -> list[date]:
        """Días lectivos de [desde, hasta] (inclusivo), en orden."""
        dias = []
        for periodo in self.periodos:
            if not periodo.es_pa:
                continue
            dia, ultimo = max(desde, periodo.fecha_inicio), min(hasta, periodo.fecha_fin)
            while dia <= ultimo:
                if dia.weekday() <= VIERNES and self.no_lectivo_de(dia) is None:
                    dias.append(dia)
                dia += UN_DIA
        return dias

    def pa_de_referencia(self, hoy: date) -> Optional[PeriodoCalendario]:
        """PA que representa "el año en curso" para indicadores que no deben caer a 0 en
        un receso (p. ej. Colegios Operando, CU012): el que contiene hoy; si no, el
        próximo; si el año ya terminó, el último."""
        return _vigente_proximo_o_ultimo([p for p in self.periodos if p.es_pa], hoy)

    @property
    def fecha_inicio(self) -> date:
        return self.periodos[0].fecha_inicio

    @property
    def fecha_fin(self) -> date:
        return self.periodos[-1].fecha_fin


@dataclass
class Calendarios:
    """Calendarios de uno o varios años cargados de una vez (4 consultas), para resolver en
    memoria muchos colegios o fechas en una misma petición."""

    # id_anio_escolar -> general.
    generales: dict[int, CalendarioResuelto] = field(default_factory=dict)
    # (id_anio_escolar, id_colegio) -> particular activo.
    particulares: dict[tuple[int, int], CalendarioResuelto] = field(default_factory=dict)

    def aplicable(self, id_colegio: Optional[int], id_anio_escolar: int) -> Optional[CalendarioResuelto]:
        """El particular activo del colegio en ese año; si no tiene, el general."""
        if id_colegio is not None:
            particular = self.particulares.get((id_anio_escolar, id_colegio))
            if particular is not None:
                return particular
        return self.generales.get(id_anio_escolar)

    def del_colegio(self, id_colegio: Optional[int]) -> list[CalendarioResuelto]:
        """El calendario aplicable al colegio en cada año cargado (None = solo generales)."""
        aplicables = (self.aplicable(id_colegio, id_anio) for id_anio in self.generales)
        return [c for c in aplicables if c is not None]

    def calendario_en(self, id_colegio: Optional[int], fecha: date) -> Optional[CalendarioResuelto]:
        """El calendario del colegio cuyo periodo contiene la fecha (el PR4 puede pasar al
        año calendario siguiente), o None."""
        return next((c for c in self.del_colegio(id_colegio) if c.periodo_de(fecha) is not None), None)

    def periodo_de(self, id_colegio: Optional[int], fecha: date) -> Optional[PeriodoCalendario]:
        calendario = self.calendario_en(id_colegio, fecha)
        return calendario.periodo_de(fecha) if calendario is not None else None

    def pa_de(self, id_colegio: int, fecha: date) -> Optional[PeriodoCalendario]:
        """El PA que contiene la fecha en el calendario del colegio, o None (PR o fuera)."""
        periodo = self.periodo_de(id_colegio, fecha)
        return periodo if periodo is not None and periodo.es_pa else None

    def es_lectivo(self, id_colegio: int, fecha: date) -> bool:
        calendario = self.calendario_en(id_colegio, fecha)
        return calendario is not None and calendario.es_lectivo(fecha)

    def dias_lectivos(self, id_colegio: int, desde: date, hasta: date) -> list[date]:
        dias = (d for c in self.del_colegio(id_colegio) for d in c.dias_lectivos(desde, hasta))
        return sorted(set(dias))

    def pa_terminado(self, id_colegio: int, periodo: PeriodoAcademico, hoy: date) -> bool:
        """Si ese PA ya terminó en el calendario del colegio. Sin calendario configurado
        para su año no se da por terminado (p. ej. un año futuro todavía sin calendario)."""
        calendario = self.aplicable(id_colegio, periodo.id_anio_escolar)
        pa = calendario.pa(periodo.numero) if calendario is not None else None
        return pa is not None and pa.fecha_fin < hoy

    def pas_del_colegio(self, id_colegio: int) -> list[PeriodoCalendario]:
        """Los PA de todos los años cargados del colegio, en orden cronológico."""
        pas = (p for c in self.del_colegio(id_colegio) for p in c.periodos if p.es_pa)
        return sorted(pas, key=lambda p: p.fecha_inicio)

    def pa_para_registrar(self, id_colegio: int, hoy: date) -> Optional[PeriodoCalendario]:
        """PA al que se asigna un dato del colegio que no trae periodo propio (p. ej. el
        subprograma de un alumno): el vigente; en un receso o entre años, el próximo; si
        no hay ninguno futuro, el último cargado."""
        return _vigente_proximo_o_ultimo(self.pas_del_colegio(id_colegio), hoy)

    def pa_actual_por_colegio(self, ids_colegio: Iterable[int], hoy: date) -> dict[int, PeriodoCalendario]:
        """{colegio: PA que contiene hoy} de los colegios que hoy están en un PA."""
        actuales = {c: self.pa_de(c, hoy) for c in ids_colegio}
        return {c: pa for c, pa in actuales.items() if pa is not None}


# ── Carga ───────────────────────────────────────────────────────────────────────────

def _ids_periodo(db: Session, ids_anio: set[int]) -> dict[tuple[int, int], int]:
    filas = db.exec(
        select(PeriodoAcademico.id_anio_escolar, PeriodoAcademico.numero, PeriodoAcademico.id_periodo_academico)
        .where(col(PeriodoAcademico.id_anio_escolar).in_(ids_anio))
    ).all()
    return {(id_anio, numero): id_periodo for id_anio, numero, id_periodo in filas}


def resolver(
    calendario: Calendario,
    periodos: Iterable[CalendarioPeriodo],
    no_lectivos: Iterable[DiaNoLectivo],
    ids_periodo: dict[tuple[int, int], int],
) -> CalendarioResuelto:
    """Arma un CalendarioResuelto con las filas ya leídas."""
    return CalendarioResuelto(
        id_calendario=calendario.id_calendario,
        id_anio_escolar=calendario.id_anio_escolar,
        id_colegio=calendario.id_colegio,
        version=calendario.version,
        periodos=tuple(
            PeriodoCalendario(
                p.tipo, p.numero, ids_periodo.get((calendario.id_anio_escolar, p.numero)), p.fecha_inicio, p.fecha_fin
            )
            for p in sorted(periodos, key=lambda p: p.fecha_inicio)
        ),
        no_lectivos=tuple(
            RangoNoLectivo(d.motivo, d.fecha_inicio, d.fecha_fin, d.origen)
            for d in sorted(no_lectivos, key=lambda d: d.fecha_inicio)
        ),
    )


def resolver_filas(db: Session, filas: list[Calendario]) -> list[CalendarioResuelto]:
    """CalendarioResuelto de cada fila de `calendario`, con 3 consultas en total."""
    if not filas:
        return []
    ids = [c.id_calendario for c in filas]
    periodos: dict[int, list[CalendarioPeriodo]] = {i: [] for i in ids}
    for periodo in db.exec(select(CalendarioPeriodo).where(col(CalendarioPeriodo.id_calendario).in_(ids))).all():
        periodos[periodo.id_calendario].append(periodo)
    no_lectivos: dict[int, list[DiaNoLectivo]] = {i: [] for i in ids}
    for dia in db.exec(select(DiaNoLectivo).where(col(DiaNoLectivo.id_calendario).in_(ids))).all():
        no_lectivos[dia.id_calendario].append(dia)
    ids_periodo = _ids_periodo(db, {c.id_anio_escolar for c in filas})
    return [
        resolver(c, periodos[c.id_calendario], no_lectivos[c.id_calendario], ids_periodo)
        for c in filas
        if periodos[c.id_calendario]
    ]


def cargar_calendarios(
    db: Session, id_anio_escolar: Optional[int] = None, ids_colegio: Optional[Iterable[int]] = None
) -> Calendarios:
    """Los generales y los particulares activos, de un año o de todos. Con `ids_colegio`,
    solo los particulares de esos colegios. Siempre 4 consultas."""
    consulta = select(Calendario).where(col(Calendario.activo).is_(True))
    if id_anio_escolar is not None:
        consulta = consulta.where(Calendario.id_anio_escolar == id_anio_escolar)
    if ids_colegio is not None:
        consulta = consulta.where(
            or_(col(Calendario.id_colegio).is_(None), col(Calendario.id_colegio).in_(list(ids_colegio)))
        )
    calendarios = Calendarios()
    for resuelto in resolver_filas(db, list(db.exec(consulta.order_by(Calendario.id_anio_escolar)).all())):
        if resuelto.es_general:
            calendarios.generales[resuelto.id_anio_escolar] = resuelto
        else:
            calendarios.particulares[(resuelto.id_anio_escolar, resuelto.id_colegio)] = resuelto
    return calendarios


# ── Consultas puntuales (un colegio) ────────────────────────────────────────────────

def calendario_aplicable(db: Session, id_colegio: Optional[int], id_anio_escolar: int) -> Optional[Calendario]:
    """La fila del calendario del colegio en ese año: su particular activo si existe; si
    no, el general. None si el año no tiene calendario general."""
    if id_colegio is not None:
        particular = db.exec(
            select(Calendario).where(
                Calendario.id_anio_escolar == id_anio_escolar,
                Calendario.id_colegio == id_colegio,
                col(Calendario.activo).is_(True),
            )
        ).first()
        if particular is not None:
            return particular
    return calendario_general(db, id_anio_escolar)


def calendario_general(db: Session, id_anio_escolar: int) -> Optional[Calendario]:
    return db.exec(
        select(Calendario).where(Calendario.id_anio_escolar == id_anio_escolar, col(Calendario.id_colegio).is_(None))
    ).first()


def periodo_de(db: Session, id_colegio: int, fecha: date) -> Optional[PeriodoCalendario]:
    """Periodo (PA o PR) en que está el colegio en esa fecha según su calendario; None si
    la fecha no cae en ningún periodo configurado (no se aproxima)."""
    return cargar_calendarios(db, ids_colegio=[id_colegio]).periodo_de(id_colegio, fecha)


def periodo_actual(db: Session, id_colegio: int) -> Optional[PeriodoCalendario]:
    """`periodo_de` con el día de hoy en Lima."""
    return periodo_de(db, id_colegio, hoy_lima())


def es_lectivo(db: Session, id_colegio: int, fecha: date) -> bool:
    """Lunes a viernes, dentro de un PA del calendario del colegio y fuera de sus días no
    lectivos (los de otro calendario no le afectan)."""
    return cargar_calendarios(db, ids_colegio=[id_colegio]).es_lectivo(id_colegio, fecha)


def dias_lectivos(db: Session, id_colegio: int, desde: date, hasta: date) -> list[date]:
    """Días lectivos del colegio en [desde, hasta] (inclusivo)."""
    return cargar_calendarios(db, ids_colegio=[id_colegio]).dias_lectivos(id_colegio, desde, hasta)


def periodos_del_anio(db: Session, id_colegio: Optional[int], id_anio_escolar: int) -> list[PeriodoCalendario]:
    """Los 8 periodos del año con las fechas del calendario del colegio (None = el
    general). Vacía si el año no tiene calendario."""
    calendario = cargar_calendarios(db, id_anio_escolar, ids_colegio=[] if id_colegio is None else [id_colegio])
    aplicable = calendario.aplicable(id_colegio, id_anio_escolar)
    return list(aplicable.periodos) if aplicable is not None else []


def periodo_general_de(db: Session, fecha: date) -> Optional[PeriodoCalendario]:
    """Periodo de la fecha según el calendario GENERAL. Solo para lo que no pertenece a un
    colegio (catálogos generales, seed): cualquier operación sobre un colegio debe usar
    `periodo_de`, que respeta su calendario particular."""
    return cargar_calendarios(db, ids_colegio=[]).periodo_de(None, fecha)


# ── PA actual de cada colegio ───────────────────────────────────────────────────────

def pa_actual_de_colegios(
    db: Session, solo_activos: bool = False, calendarios: Optional[Calendarios] = None
) -> dict[int, PeriodoCalendario]:
    """{colegio: PA en que está hoy} de los colegios que hoy están en un PA (cada uno según
    su calendario). Los que están en un PR o fuera de todo periodo no aparecen."""
    consulta = select(Colegio.id_colegio)
    if solo_activos:
        consulta = consulta.where(col(Colegio.activo).is_(True))
    ids = db.exec(consulta).all()
    calendarios = calendarios or cargar_calendarios(db, ids_colegio=ids)
    return calendarios.pa_actual_por_colegio(ids, hoy_lima())


def en_pa_de_su_colegio(
    columna_colegio, columna_periodo, pa_por_colegio: dict[int, PeriodoCalendario]
) -> ColumnElement[bool]:
    """Condición SQL: la fila (colegio, periodo) es del PA de ESE colegio en
    `pa_por_colegio` (p. ej. una asignación del PA actual de su colegio). Falsa si el
    diccionario está vacío."""
    if not pa_por_colegio:
        return false()
    return or_(
        *(
            and_(columna_colegio == id_colegio, columna_periodo == pa.id_periodo_academico)
            for id_colegio, pa in pa_por_colegio.items()
        )
    )


# ── Identidad de los periodos y años escolares ──────────────────────────────────────

def periodos_academicos_del_anio(db: Session, id_anio_escolar: int) -> list[PeriodoAcademico]:
    """Filas de `periodo_academico` del año, por número (PA1..PA4)."""
    return list(
        db.exec(
            select(PeriodoAcademico)
            .where(PeriodoAcademico.id_anio_escolar == id_anio_escolar)
            .order_by(PeriodoAcademico.numero)
        ).all()
    )


def anio_vigente(db: Session) -> Optional[AnioEscolar]:
    """El año escolar cuyo rango contiene hoy, o None si estamos entre años."""
    hoy = hoy_lima()
    return db.exec(
        select(AnioEscolar).where(AnioEscolar.fecha_inicio <= hoy, AnioEscolar.fecha_fin >= hoy)
    ).first()


def anio_de_referencia(db: Session) -> Optional[AnioEscolar]:
    """Año escolar que muestran las vistas del año en curso: el vigente; entre años, el
    último que ya empezó (el que acaba de terminar, con datos); si ninguno empezó, el
    último cargado."""
    vigente = anio_vigente(db)
    if vigente is not None:
        return vigente
    ya_empezado = db.exec(
        select(AnioEscolar)
        .where(AnioEscolar.fecha_inicio <= hoy_lima())
        .order_by(col(AnioEscolar.fecha_inicio).desc())
    ).first()
    return ya_empezado or db.exec(
        select(AnioEscolar).order_by(col(AnioEscolar.fecha_inicio).desc())
    ).first()
