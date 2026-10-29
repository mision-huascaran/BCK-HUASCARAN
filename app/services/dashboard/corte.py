"""Corte por colegio, alumnos a evaluar y resultado de cada alumno en el PA. Lo comparten
los resultados de aprendizaje (D2), la asistencia (D3) y la comparación (D4).

Corte por colegio (A6): cada colegio mira la pertenencia de sus alumnos en su propia
fecha de corte, `min(fin del PA en su calendario, hoy)`. Un colegio cuyo PA todavía no
empezó no se suma y se informa en `colegios_sin_informacion` (`periodo_no_iniciado`).

Alumnos a evaluar: quienes pertenecen a un grupo del colegio en su fecha de corte, por
pertenencia histórica (sin mirar su estado actual), en un subprograma configurado para el
colegio (`ColegioPrograma`). Cada alumno cuenta una vez: si por cortes distintos aparece en
dos colegios, cuenta en el de la fecha de corte más reciente. Su grupo (colegio, grado y
subprograma) es el de esa pertenencia.

Resultado de cada alumno, por tipo:
- `global`: el nivel del programa de la decisión vigente del Registro de Vuelo en el PA,
  con el grado del registro.
- `nivel_alfabetico`: la letra Raz-Kids colocada en esa misma decisión (A8).
- `fluidez` y `comprension`: la última evaluación mensual completa del PA
  (`rubrica_service.ultimas_rubricas_completas`, la misma regla del Registro de Vuelo). Las
  dos dimensiones salen de esa misma evaluación (A9); una incompleta nunca da nivel. Si la
  evaluación es de otro subprograma que el del alumno al corte, queda pendiente: su escala
  es otra.
Sin resultado, el alumno queda "Pendiente de evaluar".
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.evaluacion import NIVELES_DESCRIPTIVOS, NivelRazkids, NivelRubrica, RegistroVuelo, RubricaMensual
from app.models.organizacion import AlumnoPertenencia, Colegio, ColegioPrograma, Programa
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import ColegioSinInformacion, FechaCorte
from app.services.dashboard.filtros import FiltrosResueltos, item_colegio
from app.services.nivel_descriptivo import EscalaDescriptiva
from app.services.registro_vuelo import decisiones_del_periodo, escalas_por_grado, nivel_programa
from app.services.rubrica_service import DIMENSIONES, ultimas_rubricas_completas

PERIODO_NO_INICIADO = "periodo_no_iniciado"
GLOBAL, NIVEL_ALFABETICO = "global", "nivel_alfabetico"


# ── Corte por colegio ───────────────────────────────────────────────────────────────

def fechas_de_corte(filtros: FiltrosResueltos, hoy: date) -> tuple[dict[int, date], list[Colegio]]:
    """({colegio: fecha de corte}, colegios con el PA sin iniciar)."""
    fechas, sin_iniciar = {}, []
    for colegio in filtros.colegios:
        pa = filtros.calendarios[colegio.id_colegio].pa_de(filtros.periodo.id_periodo_academico)
        if pa.fecha_inicio > hoy:
            sin_iniciar.append(colegio)
        else:
            fechas[colegio.id_colegio] = min(pa.fecha_fin, hoy)
    return fechas, sin_iniciar


def colegios_sin_informacion(colegios: list[Colegio]) -> list[ColegioSinInformacion]:
    """El aviso común de los bloques del periodo para los colegios con el PA sin iniciar."""
    return [ColegioSinInformacion(colegio=item_colegio(c), motivo=PERIODO_NO_INICIADO) for c in colegios]


@dataclass(frozen=True)
class CorteDelPeriodo:
    fechas: dict[int, date]
    colegios: dict[int, Colegio]
    sin_informacion: list[Colegio]
    # id_alumno -> su pertenencia a la fecha de corte de su colegio.
    pertenencias: dict[int, AlumnoPertenencia]

    def fechas_corte(self) -> list[FechaCorte]:
        return [FechaCorte(colegio=item_colegio(self.colegios[c]), fecha=f) for c, f in self.fechas.items()]

    def encabezado(self, filtros: FiltrosResueltos) -> dict:
        """Campos comunes de las respuestas con corte: contexto, fechas y colegios sin sumar."""
        return {
            "contexto": filtros.contexto(),
            "fechas_corte": self.fechas_corte(),
            "colegios_sin_informacion": colegios_sin_informacion(self.sin_informacion),
        }


def _pertenencias_al_corte(db: Session, fechas: dict[int, date]) -> dict[int, AlumnoPertenencia]:
    if not fechas:
        return {}
    filas = db.exec(
        select(AlumnoPertenencia)
        .join(ColegioPrograma, and_(
            ColegioPrograma.id_colegio == AlumnoPertenencia.id_colegio,
            ColegioPrograma.id_programa == AlumnoPertenencia.id_programa,
        ))
        .where(
            col(AlumnoPertenencia.id_colegio).in_(list(fechas)),
            AlumnoPertenencia.desde <= max(fechas.values()),
            or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > min(fechas.values())),
        )
    ).all()
    elegidas: dict[int, AlumnoPertenencia] = {}
    for fila in filas:
        corte = fechas[fila.id_colegio]
        vigente = fila.desde <= corte and (fila.hasta is None or corte < fila.hasta)
        anterior = elegidas.get(fila.id_alumno)
        if vigente and (anterior is None or fechas[anterior.id_colegio] < corte):
            elegidas[fila.id_alumno] = fila
    return elegidas


def corte_del_periodo(db: Session, filtros: FiltrosResueltos) -> CorteDelPeriodo:
    fechas, sin_iniciar = fechas_de_corte(filtros, hoy_lima())
    return CorteDelPeriodo(
        fechas=fechas,
        colegios={c.id_colegio: c for c in filtros.colegios},
        sin_informacion=sin_iniciar,
        pertenencias=_pertenencias_al_corte(db, fechas),
    )


# ── Subprogramas ────────────────────────────────────────────────────────────────────

def item_programa(programa: Programa) -> ItemCatalogo:
    return ItemCatalogo(id=programa.id_programa, nombre=programa.nombre)


def programas(db: Session) -> list[Programa]:
    return list(db.exec(select(Programa).order_by(Programa.id_programa)).all())


def programas_por_colegio(db: Session, ids_colegio: list[int]) -> dict[int, set[int]]:
    """{colegio: subprogramas configurados} (`ColegioPrograma`)."""
    configurados: dict[int, set[int]] = {id_colegio: set() for id_colegio in ids_colegio}
    if ids_colegio:
        for id_colegio, id_programa in db.exec(
            select(ColegioPrograma.id_colegio, ColegioPrograma.id_programa)
            .where(col(ColegioPrograma.id_colegio).in_(ids_colegio))
        ).all():
            configurados[id_colegio].add(id_programa)
    return configurados


# ── Resultado de cada alumno ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Evaluaciones:
    """Lo necesario para el resultado de cada alumno en el PA, cargado de una vez."""

    decisiones: dict[int, RegistroVuelo]
    escalas_grado: dict[int, EscalaDescriptiva]
    rubricas: dict[int, RubricaMensual]
    # (id_programa, dimensión) -> niveles de la rúbrica en orden de valor.
    escalas_rubrica: dict[tuple[int, str], list[NivelRubrica]]
    # De aa a Z.
    letras: list[NivelRazkids]

    def escala(self, id_programa: int, tipo: str) -> list[tuple[object, str]]:
        """(clave, nombre) de cada nivel del tipo de resultado, en orden."""
        if tipo == GLOBAL:
            return [(nivel, nivel) for nivel in NIVELES_DESCRIPTIVOS]
        if tipo == NIVEL_ALFABETICO:
            return [(letra.id_nivel_rk, letra.letra) for letra in self.letras]
        _, dimension = DIMENSIONES[tipo]
        return [(n.id_nivel_rubrica, n.nombre_nivel) for n in self.escalas_rubrica.get((id_programa, dimension), [])]

    def resultado(self, id_alumno: int, id_programa: int, tipo: str) -> Optional[object]:
        """Clave del nivel del alumno en ese tipo de resultado, o None (pendiente)."""
        if tipo in (GLOBAL, NIVEL_ALFABETICO):
            decision = self.decisiones.get(id_alumno)
            if decision is None:
                return None
            if tipo == NIVEL_ALFABETICO:
                return decision.id_nivel_colocado
            return nivel_programa(self.escalas_grado, decision.id_grado, decision.id_nivel_colocado)
        rubrica = self.rubricas.get(id_alumno)
        if rubrica is None or rubrica.id_programa != id_programa:
            return None
        return getattr(rubrica, DIMENSIONES[tipo][0])

    def conteo(self, alumnos: list[int], id_programa: int, tipo: str) -> Counter:
        """Alumnos por clave de nivel; los pendientes no están."""
        return Counter(
            nivel for nivel in (self.resultado(a, id_programa, tipo) for a in alumnos) if nivel is not None
        )


def cargar_evaluaciones(db: Session, ids_alumno: list[int], id_periodo: int) -> Evaluaciones:
    escalas_rubrica: dict[tuple[int, str], list[NivelRubrica]] = defaultdict(list)
    for nivel in db.exec(select(NivelRubrica).order_by(NivelRubrica.valor)).all():
        escalas_rubrica[(nivel.id_programa, nivel.dimension)].append(nivel)
    return Evaluaciones(
        decisiones=decisiones_del_periodo(db, ids_alumno, id_periodo),
        escalas_grado=escalas_por_grado(db),
        rubricas=ultimas_rubricas_completas(db, ids_alumno, id_periodo),
        escalas_rubrica=escalas_rubrica,
        letras=list(db.exec(select(NivelRazkids).order_by(NivelRazkids.id_nivel_rk)).all()),
    )
