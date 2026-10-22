"""Programación de clases (CU013, CU022): qué clases tiene un grupo y en qué fechas.

- Habitual: cada fecha de un PA en que cae el `dia_clase` del grado (`colegio_grado`),
  lectiva según el calendario del colegio, y en la que el grupo tiene alumnos (alguna
  pertenencia que contiene la fecha). Los grados sin alumnos no programan clases.
- Reprogramada: una habitual que el Supervisor movió a otra fecha del mismo PA; su
  obligación pasa a la fecha nueva y la original deja de generarla.
- Adicional: registrada por el Docente en una fecha lectiva sin clase programada.

Solo hay fila en `clase` cuando hace falta (primera asistencia, reprogramación, clase
adicional o tabla de lectura); las habituales sin nada de eso se calculan. Número fijo
de consultas: los calendarios, los grupos, las pertenencias y las filas guardadas.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.tiempo import ahora_utc
from app.models.evaluacion import TIPO_HABITUAL, Asistencia, Clase, ClaseAlumno
from app.models.organizacion import AlumnoPertenencia, ColegioGrado, Seccion
from app.services.calendario import Calendarios, cargar_calendarios
from app.services.pertenencia import lista_del_grupo

UNA_SEMANA = timedelta(days=7)
# (id_grado, id_seccion) de un colegio.
Grupo = tuple[int, int]
Intervalo = tuple[date, Optional[date]]


@dataclass(frozen=True)
class FiltroGrupos:
    """Qué grupos de un colegio: todos, los de un grado o una sección."""

    id_colegio: int
    id_grado: Optional[int] = None
    id_seccion: Optional[int] = None

    def de(self, modelo) -> list:
        filtro = [modelo.id_colegio == self.id_colegio]
        if self.id_grado is not None:
            filtro.append(modelo.id_grado == self.id_grado)
        if self.id_seccion is not None:
            filtro.append(modelo.id_seccion == self.id_seccion)
        return filtro


@dataclass(frozen=True)
class ClaseProgramada:
    id_clase: Optional[int]
    id_grado: int
    id_seccion: int
    fecha_original: date
    fecha: date
    tipo: str
    reprogramada: bool
    tiene_asistencias: bool
    id_periodo_academico: int
    version: Optional[int] = None

    def orden(self) -> tuple:
        return self.fecha, self.id_grado, self.id_seccion


def fechas_del_dia(dia_clase: int, desde: date, hasta: date) -> list[date]:
    """Fechas del `dia_clase` (1 = lunes) en [desde, hasta]."""
    primera = desde + timedelta(days=(dia_clase - 1 - desde.weekday()) % 7)
    fechas = []
    while primera <= hasta:
        fechas.append(primera)
        primera += UNA_SEMANA
    return fechas


def _grupos_con_dia(db: Session, filtro: FiltroGrupos) -> dict[Grupo, int]:
    """{(grado, sección): dia_clase} de las secciones cuyo grado tiene día de clase."""
    filas = db.exec(
        select(Seccion.id_grado, Seccion.id_seccion, ColegioGrado.dia_clase)
        .join(
            ColegioGrado,
            (ColegioGrado.id_colegio == Seccion.id_colegio) & (ColegioGrado.id_grado == Seccion.id_grado),
        )
        .where(*filtro.de(Seccion), col(ColegioGrado.dia_clase).is_not(None))
    ).all()
    return {(g, s): dia for g, s, dia in filas}


def _pertenencias(db: Session, filtro: FiltroGrupos, desde: date, hasta: date) -> dict[Grupo, list[Intervalo]]:
    """Intervalos [desde, hasta) de pertenencia de cada grupo que tocan el rango."""
    filas = db.exec(
        select(
            AlumnoPertenencia.id_grado, AlumnoPertenencia.id_seccion, AlumnoPertenencia.desde, AlumnoPertenencia.hasta
        ).where(
            *filtro.de(AlumnoPertenencia),
            AlumnoPertenencia.desde <= hasta,
            or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > desde),
        )
    ).all()
    por_grupo: dict[Grupo, list[Intervalo]] = defaultdict(list)
    for grado, seccion, inicio, fin in filas:
        por_grupo[(grado, seccion)].append((inicio, fin))
    return por_grupo


def _tiene_alumnos(intervalos: list[Intervalo], fecha: date) -> bool:
    return any(inicio <= fecha and (fin is None or fin > fecha) for inicio, fin in intervalos)


def _habituales(
    db: Session, calendarios: Calendarios, filtro: FiltroGrupos, desde: date, hasta: date
) -> dict[tuple[int, int, date], ClaseProgramada]:
    """Habituales calculadas con fecha original en el rango, por (grado, sección, fecha)."""
    pertenencias = _pertenencias(db, filtro, desde, hasta)
    calculadas = {}
    for (grado, seccion), dia in _grupos_con_dia(db, filtro).items():
        for fecha in fechas_del_dia(dia, desde, hasta):
            pa = calendarios.pa_de(filtro.id_colegio, fecha)
            if pa is None or not calendarios.es_lectivo(filtro.id_colegio, fecha):
                continue
            if _tiene_alumnos(pertenencias[(grado, seccion)], fecha):
                calculadas[(grado, seccion, fecha)] = ClaseProgramada(
                    None, grado, seccion, fecha, fecha, TIPO_HABITUAL, False, False, pa.id_periodo_academico
                )
    return calculadas


def con_asistencias(db: Session, ids_clase: list[int]) -> set[int]:
    """Las clases de la lista que tienen alguna asistencia guardada."""
    if not ids_clase:
        return set()
    return set(db.exec(select(Asistencia.id_clase).where(col(Asistencia.id_clase).in_(ids_clase)).distinct()).all())


def _guardadas(db: Session, filtro: FiltroGrupos, desde: date, hasta: date) -> list[tuple[Clase, bool]]:
    """Filas de `clase` con fecha vigente u original en el rango, y si tienen asistencias."""
    en_rango = or_(col(Clase.fecha).between(desde, hasta), col(Clase.fecha_original).between(desde, hasta))
    clases = list(db.exec(select(Clase).where(*filtro.de(Clase), en_rango)).all())
    asistidas = con_asistencias(db, [c.id_clase for c in clases])
    return [(c, c.id_clase in asistidas) for c in clases]


def desde_fila(clase: Clase, tiene_asistencias: bool) -> ClaseProgramada:
    return ClaseProgramada(
        clase.id_clase, clase.id_grado, clase.id_seccion, clase.fecha_original, clase.fecha, clase.tipo,
        clase.tipo == TIPO_HABITUAL and clase.fecha != clase.fecha_original, tiene_asistencias,
        clase.id_periodo_academico, clase.version,
    )


def clases_programadas(
    db: Session, filtro: FiltroGrupos, desde: date, hasta: date, calendarios: Optional[Calendarios] = None
) -> list[ClaseProgramada]:
    """Clases de los grupos con fecha vigente en [desde, hasta], por fecha vigente, grado y
    sección: las habituales calculadas, reemplazadas por su fila si la tienen (una
    reprogramada aparece en su fecha nueva y no en la original), y las adicionales."""
    calendarios = calendarios or cargar_calendarios(db, ids_colegio=[filtro.id_colegio])
    habituales = _habituales(db, calendarios, filtro, desde, hasta)
    adicionales = []
    for fila, tiene_asistencias in _guardadas(db, filtro, desde, hasta):
        if fila.tipo == TIPO_HABITUAL:
            habituales[(fila.id_grado, fila.id_seccion, fila.fecha_original)] = desde_fila(fila, tiene_asistencias)
        else:
            adicionales.append(desde_fila(fila, tiene_asistencias))
    todas = [c for c in [*habituales.values(), *adicionales] if desde <= c.fecha <= hasta]
    return sorted(todas, key=ClaseProgramada.orden)


@dataclass(frozen=True)
class ClaseDelDia:
    """La clase de un grupo en una fecha (o None) y, si esa fecha era la original de una
    clase trasladada, a qué fecha se movió."""

    clase: Optional[ClaseProgramada]
    trasladada_a: Optional[date]


def clase_del_dia(
    db: Session, filtro: FiltroGrupos, fecha: date, calendarios: Optional[Calendarios] = None
) -> ClaseDelDia:
    """La clase del grupo (`filtro` con grado y sección) en la fecha."""
    clases = clases_programadas(db, filtro, fecha, fecha, calendarios)
    trasladada = db.exec(
        select(Clase.fecha).where(
            *filtro.de(Clase), Clase.tipo == TIPO_HABITUAL, Clase.fecha_original == fecha, Clase.fecha != fecha
        )
    ).first()
    return ClaseDelDia(clases[0] if clases else None, trasladada)


# ── Fila de la clase y su lista ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class NuevaClase:
    """Lo que hace falta para crear la fila de una clase que todavía no la tiene."""

    fecha: date
    fecha_original: date
    tipo: str
    id_periodo_academico: int


def asegurar_fila(db: Session, filtro: FiltroGrupos, nueva: NuevaClase, id_usuario: int) -> Clase:
    """La fila de la clase del grupo (`filtro` con grado y sección) en `nueva.fecha`; si
    no existe, la crea. INSERT ... ON CONFLICT DO NOTHING: dos escrituras simultáneas no
    crean dos clases."""
    db.execute(
        insert_con_conflicto(db, Clase.__table__)
        .values(
            id_colegio=filtro.id_colegio, id_grado=filtro.id_grado, id_seccion=filtro.id_seccion,
            id_periodo_academico=nueva.id_periodo_academico, tipo=nueva.tipo, fecha_original=nueva.fecha_original,
            fecha=nueva.fecha, version=1, creado_por=id_usuario, creado_en=ahora_utc(),
        )
        .on_conflict_do_nothing()
    )
    return db.exec(
        select(Clase).where(*filtro.de(Clase), Clase.fecha == nueva.fecha).execution_options(populate_existing=True)
    ).one()


def fila_de_programada(db: Session, filtro: FiltroGrupos, clase: ClaseProgramada, id_usuario: int) -> Clase:
    """La fila de una clase programada (la crea si es una habitual calculada)."""
    if clase.id_clase is not None:
        return db.get(Clase, clase.id_clase)
    nueva = NuevaClase(clase.fecha, clase.fecha_original, clase.tipo, clase.id_periodo_academico)
    return asegurar_fila(db, filtro, nueva, id_usuario)


def congelar_lista(db: Session, clase: Clase) -> None:
    """Guarda la lista de la clase (los alumnos del grupo en su fecha, con su
    subprograma) si todavía no está congelada. Desde ahí no cambia con la pertenencia."""
    if clase.lista_congelada_en is not None:
        return
    lista = lista_del_grupo(db, clase.id_colegio, clase.id_grado, clase.id_seccion, clase.fecha)
    if lista:
        db.execute(
            insert_con_conflicto(db, ClaseAlumno.__table__)
            .values([{"id_clase": clase.id_clase, "id_alumno": a.id_alumno, "id_programa": p} for a, p in lista])
            .on_conflict_do_nothing()
        )
    clase.lista_congelada_en = ahora_utc()
    db.add(clase)
    db.flush()
