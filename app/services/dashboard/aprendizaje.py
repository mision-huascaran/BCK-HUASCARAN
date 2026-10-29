"""Distribución por subprograma y resultados de aprendizaje del PA (CU033 pasos 4 a 6,
CU035 paso 6).

Corte por colegio (A6): cada colegio mira la pertenencia de sus alumnos en su propia
fecha de corte, `min(fin del PA en su calendario, hoy)`. Un colegio cuyo PA todavía no
empezó no se suma y se informa en `colegios_sin_informacion`.

Universo ("alumnos que deben ser evaluados"): quienes pertenecen a un grupo del colegio en
su fecha de corte, por pertenencia histórica (sin mirar su estado actual), en un
subprograma configurado para el colegio (`ColegioPrograma`). Cada alumno cuenta una vez:
si por cortes distintos aparece en dos colegios, cuenta en el de la fecha de corte más
reciente.

Resultados:
- Global: el nivel del programa de la decisión vigente del Registro de Vuelo en el PA,
  con el grado del registro (`registro_vuelo.niveles_programa_del_periodo`).
- Fluidez y Comprensión: la última evaluación mensual completa del PA
  (`rubrica_service.ultimas_rubricas_completas`, la misma regla del Registro de Vuelo). Las
  dos dimensiones salen de esa misma evaluación (A9); una incompleta nunca da nivel. Si la
  evaluación es de otro subprograma que el del alumno al corte, queda pendiente: su
  escala es otra.
Sin nivel, el alumno queda "Pendiente de evaluar" y no entra en la base de los porcentajes.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.core.tiempo import hoy_lima
from app.models.evaluacion import NIVELES_DESCRIPTIVOS, NivelRubrica, RubricaMensual
from app.models.organizacion import AlumnoPertenencia, Colegio, ColegioPrograma, Programa
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import (
    ColegioSinInformacion,
    DistribucionResultado,
    DistribucionSubprograma,
    FechaCorte,
    NivelDesempeno,
    ResultadosAprendizaje,
    ResultadosSubprograma,
    SubprogramaAlumnos,
)
from app.services.dashboard.filtros import FiltrosResueltos, item_colegio
from app.services.registro_vuelo import niveles_programa_del_periodo
from app.services.rubrica_service import DIMENSIONES, ultimas_rubricas_completas

MENSAJE_SIN_INFORMACION = "No hay información disponible para esta selección."
MENSAJE_SIN_EVALUACIONES = "No hay evaluaciones completas para esta selección."
PERIODO_NO_INICIADO = "periodo_no_iniciado"


def porcentaje(parte: int, base: int) -> float:
    return round(100 * parte / base, 1)


# ── Corte por colegio y universo ────────────────────────────────────────────────────

@dataclass(frozen=True)
class CorteDelPeriodo:
    fechas: dict[int, date]
    colegios: dict[int, Colegio]
    sin_informacion: list[Colegio]
    # id_alumno -> su pertenencia a la fecha de corte de su colegio.
    pertenencias: dict[int, AlumnoPertenencia]

    def fechas_corte(self) -> list[FechaCorte]:
        return [FechaCorte(colegio=item_colegio(self.colegios[c]), fecha=f) for c, f in self.fechas.items()]

    def colegios_sin_informacion(self) -> list[ColegioSinInformacion]:
        return [ColegioSinInformacion(colegio=item_colegio(c), motivo=PERIODO_NO_INICIADO) for c in self.sin_informacion]

    def encabezado(self, filtros: FiltrosResueltos) -> dict:
        """Campos comunes de las respuestas con corte: contexto, fechas y colegios sin sumar."""
        return {
            "contexto": filtros.contexto(),
            "fechas_corte": self.fechas_corte(),
            "colegios_sin_informacion": self.colegios_sin_informacion(),
        }


def _fechas_de_corte(filtros: FiltrosResueltos, hoy: date) -> tuple[dict[int, date], list[Colegio]]:
    fechas, sin_informacion = {}, []
    for colegio in filtros.colegios:
        pa = filtros.calendarios[colegio.id_colegio].pa_de(filtros.periodo.id_periodo_academico)
        if pa.fecha_inicio > hoy:
            sin_informacion.append(colegio)
        else:
            fechas[colegio.id_colegio] = min(pa.fecha_fin, hoy)
    return fechas, sin_informacion


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
    fechas, sin_informacion = _fechas_de_corte(filtros, hoy_lima())
    return CorteDelPeriodo(
        fechas=fechas,
        colegios={c.id_colegio: c for c in filtros.colegios},
        sin_informacion=sin_informacion,
        pertenencias=_pertenencias_al_corte(db, fechas),
    )


def _programas(db: Session) -> list[Programa]:
    return list(db.exec(select(Programa).order_by(Programa.id_programa)).all())


def _item_programa(programa: Programa) -> ItemCatalogo:
    return ItemCatalogo(id=programa.id_programa, nombre=programa.nombre)


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
                subprograma=_item_programa(p),
                alumnos=conteo[p.id_programa],
                porcentaje=porcentaje(conteo[p.id_programa], total),
            )
            for p in _programas(db)
        ],
        sin_datos=False,
    )


# ── Resultados de aprendizaje (CU033 paso 6, CU035 paso 6) ──────────────────────────

def _distribucion(
    resultado: str, fuente: str, escala: list[tuple[object, str]], conteo: Counter, total: int
) -> DistribucionResultado:
    """`escala`: (clave, nombre) de cada nivel en orden; `conteo`: alumnos por clave."""
    evaluados = sum(conteo[clave] for clave, _ in escala)
    return DistribucionResultado(
        resultado=resultado,
        fuente=fuente,
        niveles=[
            NivelDesempeno(
                nombre=nombre, alumnos=conteo[clave], porcentaje=porcentaje(conteo[clave], evaluados) if evaluados else None
            )
            for clave, nombre in escala
        ],
        evaluados=evaluados,
        pendientes=total - evaluados,
        mensaje=None if evaluados else MENSAJE_SIN_EVALUACIONES,
    )


@dataclass(frozen=True)
class _Evaluaciones:
    globales: dict[int, str]
    rubricas: dict[int, RubricaMensual]
    # (id_programa, dimensión) -> niveles de la rúbrica en orden de valor.
    escalas: dict[tuple[int, str], list[NivelRubrica]]


def _evaluaciones(db: Session, ids_alumno: list[int], id_periodo: int) -> _Evaluaciones:
    escalas: dict[tuple[int, str], list[NivelRubrica]] = defaultdict(list)
    for nivel in db.exec(select(NivelRubrica).order_by(NivelRubrica.valor)).all():
        escalas[(nivel.id_programa, nivel.dimension)].append(nivel)
    return _Evaluaciones(
        globales=niveles_programa_del_periodo(db, ids_alumno, id_periodo),
        rubricas=ultimas_rubricas_completas(db, ids_alumno, id_periodo),
        escalas=escalas,
    )


def _dimension(evaluaciones: _Evaluaciones, programa: int, alumnos: list[int], clave: str) -> DistribucionResultado:
    columna, dimension = DIMENSIONES[clave]
    escala = evaluaciones.escalas.get((programa, dimension), [])
    conteo = Counter()
    for id_alumno in alumnos:
        rubrica = evaluaciones.rubricas.get(id_alumno)
        if rubrica is not None and rubrica.id_programa == programa:
            conteo[getattr(rubrica, columna)] += 1
    return _distribucion(clave, "rubrica", [(n.id_nivel_rubrica, n.nombre_nivel) for n in escala], conteo, len(alumnos))


def _resultados_del_programa(evaluaciones: _Evaluaciones, programa: Programa, alumnos: list[int]) -> ResultadosSubprograma:
    globales = Counter(evaluaciones.globales[a] for a in alumnos if a in evaluaciones.globales)
    escala_global = [(nivel, nivel) for nivel in NIVELES_DESCRIPTIVOS]
    return ResultadosSubprograma(
        subprograma=_item_programa(programa),
        total_a_evaluar=len(alumnos),
        distribuciones=[
            _distribucion("global", "registro_vuelo", escala_global, globales, len(alumnos)),
            *(_dimension(evaluaciones, programa.id_programa, alumnos, clave) for clave in DIMENSIONES),
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
    evaluaciones = _evaluaciones(db, list(corte.pertenencias), filtros.periodo.id_periodo_academico)
    return ResultadosAprendizaje(
        **comunes,
        subprogramas=[
            _resultados_del_programa(evaluaciones, programa, por_programa.get(programa.id_programa, []))
            for programa in _programas_configurados(db, list(corte.fechas))
        ],
        sin_datos=False,
    )
