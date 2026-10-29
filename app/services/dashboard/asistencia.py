"""Asistencia de alumnos del PA (CU033 paso 8, CU035 paso 7).

Clases: las de la programación (`programacion.clases_programadas`, la misma fuente que
CU011) con fecha vigente entre el inicio del PA en el calendario de cada colegio y
`min(fin del PA, hoy)`: habituales calculadas, reprogramadas en su fecha vigente (una sola
vez) y adicionales. Recesos y días no lectivos no generan clases.

- Dictada: tiene al menos una asistencia guardada (como en CU011). Cuenta su lista
  histórica (`clase_alumno`): presentes, tardanzas, ausentes y sin marcar. Con alumnos sin
  marcar es "Registro incompleto" y vuelve provisional todo resumen que la incluya.
- Sin registro: habitual o reprogramada sin ninguna asistencia y con fecha anterior a hoy
  (la "fecha transcurrida" de CU011). No es dictada ni da ausencias: se cuenta aparte y
  no vuelve provisional el porcentaje. Una adicional sin asistencias no cuenta.

Porcentaje: (P + T) / (P + T + A) × 100 sobre la suma de estados del conjunto (nunca el
promedio de porcentajes). Sin marcar no es ausencia ni entra al denominador. Sin estados,
el porcentaje es null ("Sin información").
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Optional

from fastapi import status
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio, error_de_campo
from app.core.texto import comparable
from app.core.tiempo import hoy_lima
from app.models.evaluacion import ESTADOS_ASISTENCIA, TIPO_HABITUAL, Asistencia, ClaseAlumno
from app.models.organizacion import Colegio, ColegioGrado, Grado, Seccion, Usuario
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import (
    AsistenciaColegio,
    AsistenciaDelPeriodo,
    AsistenciaPorClase,
    ClaseDictada,
    ClaseSinRegistro,
    ConteoAsistencia,
    GrupoAsistencia,
    MesAsistencia,
    ResumenAsistencia,
)
from app.services.dashboard.corte import colegios_sin_informacion, fechas_de_corte
from app.services.dashboard.filtros import MOTIVO_FUERA_DE_ALCANCE, FiltrosResueltos, colegios_del_alcance, item_colegio
from app.services.dashboard.numeros import porcentaje
from app.services.programacion import ClaseProgramada, FiltroGrupos, clases_programadas

MENSAJE_SIN_INFORMACION = "Sin información"
PRESENTE, TARDANZA, AUSENTE = ESTADOS_ASISTENCIA


def _porcentaje(asistieron: int, completadas: int) -> Optional[float]:
    return porcentaje(asistieron, completadas) if completadas else None


# ── Conteos ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EstadosClase:
    """Estados de la lista histórica de una clase dictada."""

    presentes: int = 0
    tardanzas: int = 0
    ausentes: int = 0
    sin_marcar: int = 0

    @property
    def lista(self) -> int:
        return self.presentes + self.tardanzas + self.ausentes + self.sin_marcar

    @property
    def porcentaje(self) -> Optional[float]:
        return _porcentaje(self.presentes + self.tardanzas, self.presentes + self.tardanzas + self.ausentes)


@dataclass
class Conteo:
    """Suma de estados y de clases de un conjunto (colegio, mes, grupo o todo)."""

    presentes: int = 0
    tardanzas: int = 0
    ausentes: int = 0
    sin_marcar: int = 0
    clases_dictadas: int = 0
    clases_incompletas: int = 0
    clases_sin_registro: int = 0

    def dictada(self, estados: EstadosClase) -> None:
        self.presentes += estados.presentes
        self.tardanzas += estados.tardanzas
        self.ausentes += estados.ausentes
        self.sin_marcar += estados.sin_marcar
        self.clases_dictadas += 1
        self.clases_incompletas += estados.sin_marcar > 0

    def sumar(self, otro: "Conteo") -> None:
        for campo in fields(self):
            setattr(self, campo.name, getattr(self, campo.name) + getattr(otro, campo.name))

    @property
    def completadas(self) -> int:
        return self.presentes + self.tardanzas + self.ausentes

    @property
    def porcentaje(self) -> Optional[float]:
        return _porcentaje(self.presentes + self.tardanzas, self.completadas)

    @property
    def provisional(self) -> bool:
        return self.clases_incompletas > 0

    def campos(self) -> dict:
        """Campos de `ConteoAsistencia`."""
        return {
            "porcentaje": self.porcentaje,
            **{campo.name: getattr(self, campo.name) for campo in fields(self)},
            "provisional": self.provisional,
        }


@dataclass
class ClasesDelConjunto:
    dictadas: list[tuple[ClaseProgramada, EstadosClase]] = field(default_factory=list)
    sin_registro: list[ClaseProgramada] = field(default_factory=list)

    def conteo(self) -> Conteo:
        conteo = Conteo(clases_sin_registro=len(self.sin_registro))
        for _, estados in self.dictadas:
            conteo.dictada(estados)
        return conteo


# ── Clases del periodo ──────────────────────────────────────────────────────────────

def _rango(filtros: FiltrosResueltos, id_colegio: int, hoy: date) -> Optional[tuple[date, date]]:
    """Del inicio del PA del colegio a min(fin, hoy); None si todavía no empezó."""
    pa = filtros.calendarios[id_colegio].pa_de(filtros.periodo.id_periodo_academico)
    hasta = min(pa.fecha_fin, hoy)
    return (pa.fecha_inicio, hasta) if pa.fecha_inicio <= hasta else None


def _estados(db: Session, ids_clase: list[int]) -> dict[int, EstadosClase]:
    """Estados de la lista histórica de cada clase, en una consulta."""
    if not ids_clase:
        return {}
    conteos: dict[int, Counter] = defaultdict(Counter)
    for id_clase, estado in db.exec(
        select(ClaseAlumno.id_clase, Asistencia.estado)
        .outerjoin(
            Asistencia,
            (Asistencia.id_clase == ClaseAlumno.id_clase) & (Asistencia.id_alumno == ClaseAlumno.id_alumno),
        )
        .where(col(ClaseAlumno.id_clase).in_(ids_clase))
    ).all():
        conteos[id_clase][estado] += 1
    return {
        id_clase: EstadosClase(n[PRESENTE], n[TARDANZA], n[AUSENTE], n[None]) for id_clase, n in conteos.items()
    }


def _clasificar(clases: list[ClaseProgramada], estados: dict[int, EstadosClase], hoy: date) -> ClasesDelConjunto:
    conjunto = ClasesDelConjunto()
    for clase in clases:
        if clase.tiene_asistencias:
            conjunto.dictadas.append((clase, estados.get(clase.id_clase, EstadosClase())))
        elif clase.tipo == TIPO_HABITUAL and clase.fecha < hoy:
            conjunto.sin_registro.append(clase)
    return conjunto


def clases_del_periodo(
    db: Session, filtros: FiltrosResueltos, grupos: list[FiltroGrupos]
) -> dict[int, ClasesDelConjunto]:
    """{colegio: clases dictadas y sin registro del PA} de los grupos pedidos (uno o todos
    los de cada colegio). Consultas fijas por colegio, no por clase ni por alumno."""
    hoy = hoy_lima()
    id_periodo = filtros.periodo.id_periodo_academico
    programadas: dict[int, list[ClaseProgramada]] = {}
    for filtro in grupos:
        rango = _rango(filtros, filtro.id_colegio, hoy)
        clases = clases_programadas(db, filtro, *rango, filtros.cargados) if rango else []
        programadas[filtro.id_colegio] = [c for c in clases if c.id_periodo_academico == id_periodo]
    estados = _estados(db, [c.id_clase for clases in programadas.values() for c in clases if c.tiene_asistencias])
    return {id_colegio: _clasificar(clases, estados, hoy) for id_colegio, clases in programadas.items()}


# ── Serie mensual ───────────────────────────────────────────────────────────────────

def _meses(desde: date, hasta: date) -> list[tuple[int, int]]:
    meses, anio, mes = [], desde.year, desde.month
    while (anio, mes) <= (hasta.year, hasta.month):
        meses.append((anio, mes))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return meses


def _mes(anio: int, mes: int, conteo: Conteo, hoy: date) -> MesAsistencia:
    if date(anio, mes, 1) > hoy:
        return MesAsistencia(anio=anio, mes=mes, estado="futuro", porcentaje=None)
    en_curso = (anio, mes) == (hoy.year, hoy.month)
    if not en_curso and conteo.completadas == 0:
        return MesAsistencia(anio=anio, mes=mes, estado="sin_informacion", porcentaje=None)
    return MesAsistencia(
        anio=anio, mes=mes, estado="en_curso" if en_curso else "con_datos", porcentaje=conteo.porcentaje,
        presentes=conteo.presentes, tardanzas=conteo.tardanzas, ausentes=conteo.ausentes,
        sin_marcar=conteo.sin_marcar, provisional=conteo.provisional,
    )


def _mensual(filtros: FiltrosResueltos, clases: dict[int, ClasesDelConjunto]) -> list[MesAsistencia]:
    """Un punto por mes del intervalo del periodo (unión de los rangos de los colegios)."""
    por_mes: dict[tuple[int, int], Conteo] = defaultdict(Conteo)
    for conjunto in clases.values():
        for clase, estados in conjunto.dictadas:
            por_mes[(clase.fecha.year, clase.fecha.month)].dictada(estados)
    intervalo, hoy = filtros.intervalo(), hoy_lima()
    return [_mes(anio, mes, por_mes.get((anio, mes), Conteo()), hoy) for anio, mes in _meses(intervalo.desde, intervalo.hasta)]


# ── Bloque del periodo ──────────────────────────────────────────────────────────────

def asistencia_del_periodo(db: Session, filtros: FiltrosResueltos) -> AsistenciaDelPeriodo:
    clases = clases_del_periodo(db, filtros, [FiltroGrupos(id_colegio) for id_colegio in filtros.ids_colegio])
    total, por_colegio = Conteo(), []
    for colegio in filtros.colegios:
        conteo = clases[colegio.id_colegio].conteo()
        total.sumar(conteo)
        por_colegio.append(AsistenciaColegio(colegio=item_colegio(colegio), **conteo.campos()))
    resumen = ResumenAsistencia(
        **total.campos(),
        asistencias_esperadas=total.completadas + total.sin_marcar,
        asistencias_completadas=total.completadas,
        mensaje=None if total.completadas else MENSAJE_SIN_INFORMACION,
    )
    _, sin_iniciar = fechas_de_corte(filtros, hoy_lima())
    return AsistenciaDelPeriodo(
        contexto=filtros.contexto(),
        resumen=resumen,
        mensual=_mensual(filtros, clases),
        por_colegio=por_colegio,
        colegios_sin_informacion=colegios_sin_informacion(sin_iniciar),
    )


# ── Detalle por clase (solo Supervisor) ─────────────────────────────────────────────

def _validar_colegio(db: Session, usuario: Usuario, filtros: FiltrosResueltos, id_colegio: int) -> None:
    """El colegio del detalle debe estar entre los seleccionados: 422 si no existe o no
    está en la selección, 403 si existe pero está fuera del alcance (como en los filtros)."""
    if id_colegio in filtros.ids_colegio:
        return
    if db.get(Colegio, id_colegio) is None:
        raise error_de_campo("id_colegio", "El colegio seleccionado no existe.")
    if id_colegio not in {c.id_colegio for c in colegios_del_alcance(db, usuario)}:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN, "Solo puedes consultar los colegios de tu alcance.", motivo=MOTIVO_FUERA_DE_ALCANCE
        )
    raise error_de_campo("id_colegio", "Elige un colegio de los seleccionados.")


def _validar_grupo(db: Session, id_colegio: int, id_grado: Optional[int], id_seccion: Optional[int]) -> None:
    if id_grado is not None and db.get(ColegioGrado, (id_colegio, id_grado)) is None:
        raise error_de_campo("id_grado", "El grado no pertenece al colegio seleccionado.")
    if id_seccion is None:
        return
    seccion = db.get(Seccion, id_seccion)
    if seccion is None or seccion.id_colegio != id_colegio or id_grado not in (None, seccion.id_grado):
        raise error_de_campo("id_seccion", "La sección no pertenece al colegio o grado seleccionado.")


def _clase_dictada(clase: ClaseProgramada, estados: EstadosClase) -> ClaseDictada:
    return ClaseDictada(
        id_clase=clase.id_clase, fecha=clase.fecha, tipo=clase.tipo, reprogramada=clase.reprogramada,
        lista_historica=estados.lista, presentes=estados.presentes, tardanzas=estados.tardanzas,
        ausentes=estados.ausentes, sin_marcar=estados.sin_marcar, porcentaje=estados.porcentaje,
        estado="registro_incompleto" if estados.sin_marcar else "completa",
    )


def _clase_sin_registro(clase: ClaseProgramada) -> ClaseSinRegistro:
    return ClaseSinRegistro(
        id_clase=clase.id_clase, fecha=clase.fecha, tipo=clase.tipo, reprogramada=clase.reprogramada,
        fecha_original=clase.fecha_original,
    )


def _por_grupo(conjunto: ClasesDelConjunto) -> dict[tuple[int, int], ClasesDelConjunto]:
    grupos: dict[tuple[int, int], ClasesDelConjunto] = defaultdict(ClasesDelConjunto)
    for clase, estados in conjunto.dictadas:
        grupos[(clase.id_grado, clase.id_seccion)].dictadas.append((clase, estados))
    for clase in conjunto.sin_registro:
        grupos[(clase.id_grado, clase.id_seccion)].sin_registro.append(clase)
    return grupos


def asistencia_por_clase(
    db: Session,
    usuario: Usuario,
    filtros: FiltrosResueltos,
    id_colegio: int,
    id_grado: Optional[int],
    id_seccion: Optional[int],
) -> AsistenciaPorClase:
    """Una fila por clase dictada y, aparte, las clases sin registro, por grado y sección."""
    _validar_colegio(db, usuario, filtros, id_colegio)
    _validar_grupo(db, id_colegio, id_grado, id_seccion)
    conjunto = clases_del_periodo(db, filtros, [FiltroGrupos(id_colegio, id_grado, id_seccion)])[id_colegio]
    grupos = _por_grupo(conjunto)
    grados = dict(db.exec(select(Grado.id_grado, Grado.nombre).where(
        col(Grado.id_grado).in_({g for g, _ in grupos})
    )).all())
    secciones = dict(db.exec(select(Seccion.id_seccion, Seccion.nombre).where(
        col(Seccion.id_seccion).in_({s for _, s in grupos})
    )).all())
    orden = sorted(grupos, key=lambda par: (par[0], comparable(secciones[par[1]]), par[1]))
    return AsistenciaPorClase(
        contexto=filtros.contexto(),
        grupos=[
            GrupoAsistencia(
                grado=ItemCatalogo(id=grado, nombre=grados[grado]),
                seccion=ItemCatalogo(id=seccion, nombre=secciones[seccion]),
                resumen=ConteoAsistencia(**grupos[(grado, seccion)].conteo().campos()),
                clases=[_clase_dictada(c, e) for c, e in grupos[(grado, seccion)].dictadas],
                clases_sin_registro=[_clase_sin_registro(c) for c in grupos[(grado, seccion)].sin_registro],
            )
            for grado, seccion in orden
        ],
    )
