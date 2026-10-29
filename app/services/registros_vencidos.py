"""Registros vencidos e incompletos de los cuatro módulos (CU011).

Se calcula al consultar, con la hora de Perú y el calendario de cada colegio (CU027). Un
evaluador por módulo, con la misma interfaz (`Evaluador`), devuelve sus obligaciones
vencidas o incompletas (`RegistroObligatorio`, con el docente asignado al que se le
exigen) y los colegios que ya tienen algún registro consolidado en su PA vigente:

- Asistencias: por clase programada del PA en curso, hasta ayer. VENCIDA sin ninguna
  asistencia; INCOMPLETA con algún alumno de su lista sin estado.
- Lectura: por grupo, subprograma y semana con clase habitual o reprogramada en el PA en
  curso; vence el sábado 00:00 de esa semana. VENCIDA sin tabla; INCOMPLETA con filas
  "Pendiente". "No realizada" cumple; una semana sin clase no obliga.
- Rúbrica: por alumno y mes con días lectivos del PA en curso (R2, y DP-1 para un mes
  partido entre dos PA); vence el día 1 del mes siguiente. VENCIDA sin ninguna
  dimensión; INCOMPLETA con una sola.
- Registro de Vuelo: por alumno y PA ya terminado del año; vence el día siguiente al fin
  del PA. VENCIDA sin fila; INCOMPLETA con fila pero sin nivel colocado (en cualquier
  grupo, V1). Los PA en curso y futuros no vencen.

Solo se exigen los grados con docente asignado a ese PA en un colegio activo.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import hoy_lima
from app.models.evaluacion import (
    TIPO_HABITUAL,
    Asistencia,
    Clase,
    ClaseAlumno,
    LecturaSemanal,
    RegistroVuelo,
    RubricaMensual,
    SemanaReporte,
)
from app.models.organizacion import AlumnoPertenencia, Colegio, DocenteColegioGrado
from app.services.calendario import (
    Calendarios,
    PeriodoCalendario,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
)
from app.services.lectura_service import colegios_con_filas_confirmadas, tablas_con_pendientes
from app.services.programacion import ClaseProgramada, FiltroGrupos, clases_programadas
from app.services.registro_vuelo import colegios_con_decisiones
from app.services.reglas_provisionales import regla_mes_exigible_en_pa
from app.services.rubrica_service import (
    GrupoMes,
    MesEvaluado,
    alumnos_del_mes,
    colegios_con_rubricas,
    mes_evaluado,
)

UN_DIA = timedelta(days=1)
# Lectura: la semana vence el sábado (lunes + 5) a las 00:00.
DIAS_HASTA_EL_SABADO = 5

ASISTENCIAS = "asistencias"
LECTURA = "lectura"
RUBRICA = "rubrica"
REGISTRO_VUELO = "registro_vuelo"
MODULOS = (ASISTENCIAS, LECTURA, RUBRICA, REGISTRO_VUELO)

PaPorColegio = dict[int, PeriodoCalendario]
# (colegio, grado) -> docentes asignados a ese PA.
DocentesPorGrado = dict[tuple[int, int], list[int]]


@dataclass(frozen=True)
class RegistroObligatorio:
    """Una obligación vencida o incompleta: módulo, grupo, a qué corresponde (fecha,
    semana, mes o PA, y alumno si aplica) y el docente al que se le exige."""

    modulo: str
    id_colegio: int
    id_grado: int
    id_seccion: int
    referencia: str
    id_docente: int


@dataclass
class EstadoRegistros:
    vencidas: list[RegistroObligatorio] = field(default_factory=list)
    incompletas: list[RegistroObligatorio] = field(default_factory=list)
    # Colegios con algún registro consolidado en su PA vigente (de cualquier módulo).
    colegios_con_registros: set[int] = field(default_factory=set)

    def absorber(self, otro: "EstadoRegistros") -> None:
        self.vencidas.extend(otro.vencidas)
        self.incompletas.extend(otro.incompletas)
        self.colegios_con_registros |= otro.colegios_con_registros

    def agregar(self, vencida: bool, obligacion: tuple, docentes: list[int]) -> None:
        destino = self.vencidas if vencida else self.incompletas
        destino.extend(RegistroObligatorio(*obligacion, id_docente) for id_docente in docentes)


def por_modulo(registros: list[RegistroObligatorio]) -> dict[str, int]:
    """{módulo: cantidad}, con los cuatro módulos (0 si no tiene)."""
    conteo = dict.fromkeys(MODULOS, 0)
    for registro in registros:
        conteo[registro.modulo] += 1
    return conteo


@dataclass(frozen=True)
class ContextoVencidos:
    calendarios: Calendarios
    # PA en que está hoy cada colegio activo.
    pa_actual: PaPorColegio
    # Asignados al PA actual de cada colegio.
    docentes: DocentesPorGrado
    colegios_activos: list[int]
    hoy: date


Evaluador = Callable[[Session, ContextoVencidos], EstadoRegistros]


def docentes_por_grado(db: Session, pa_por_colegio: PaPorColegio) -> DocentesPorGrado:
    """{(colegio, grado): docentes asignados al PA de ese colegio en `pa_por_colegio`}."""
    filas = db.exec(
        select(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado, DocenteColegioGrado.id_docente).where(
            en_pa_de_su_colegio(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_periodo_academico, pa_por_colegio)
        )
    ).all()
    por_grado: DocentesPorGrado = defaultdict(list)
    for id_colegio, id_grado, id_docente in filas:
        por_grado[(id_colegio, id_grado)].append(id_docente)
    return por_grado


def _intervalos_del_colegio(db: Session, id_colegio: int, desde: date, hasta: date) -> list[AlumnoPertenencia]:
    return list(db.exec(
        select(AlumnoPertenencia).where(
            AlumnoPertenencia.id_colegio == id_colegio,
            AlumnoPertenencia.desde <= hasta,
            (col(AlumnoPertenencia.hasta).is_(None)) | (col(AlumnoPertenencia.hasta) > desde),
        )
    ).all())


def _contiene(intervalo: AlumnoPertenencia, dia: date) -> bool:
    return intervalo.desde <= dia and (intervalo.hasta is None or intervalo.hasta > dia)


# ── Asistencias ─────────────────────────────────────────────────────────────────────

def _incompletas(db: Session, ids_clase: list[int]) -> set[int]:
    """De esas clases, las que tienen algún alumno de su lista sin estado."""
    if not ids_clase:
        return set()
    return set(
        db.exec(
            select(ClaseAlumno.id_clase)
            .outerjoin(
                Asistencia,
                (Asistencia.id_clase == ClaseAlumno.id_clase) & (Asistencia.id_alumno == ClaseAlumno.id_alumno),
            )
            .where(col(ClaseAlumno.id_clase).in_(ids_clase), col(Asistencia.estado).is_(None))
            .distinct()
        ).all()
    )


def _colegios_con_asistencias(db: Session, pa_actual: PaPorColegio) -> set[int]:
    return set(
        db.exec(
            select(Clase.id_colegio)
            .where(
                en_pa_de_su_colegio(Clase.id_colegio, Clase.id_periodo_academico, pa_actual),
                col(Clase.id_clase).in_(select(Asistencia.id_clase)),
            )
            .distinct()
        ).all()
    )


def _clases_del_pa(
    db: Session, contexto: ContextoVencidos, id_colegio: int, hasta: date
) -> list[ClaseProgramada]:
    """Clases programadas del PA actual del colegio hasta `hasta`, de grados con docente."""
    pa = contexto.pa_actual[id_colegio]
    if hasta < pa.fecha_inicio:
        return []
    clases = clases_programadas(
        db, FiltroGrupos(id_colegio), pa.fecha_inicio, min(pa.fecha_fin, hasta), contexto.calendarios
    )
    return [
        c for c in clases
        if c.id_periodo_academico == pa.id_periodo_academico and (id_colegio, c.id_grado) in contexto.docentes
    ]


def asistencias(db: Session, contexto: ContextoVencidos) -> EstadoRegistros:
    obligaciones = [
        (id_colegio, clase)
        for id_colegio in sorted(contexto.pa_actual)
        for clase in _clases_del_pa(db, contexto, id_colegio, contexto.hoy - UN_DIA)
    ]
    incompletas = _incompletas(db, [c.id_clase for _, c in obligaciones if c.tiene_asistencias])
    estado = EstadoRegistros(colegios_con_registros=_colegios_con_asistencias(db, contexto.pa_actual))
    for id_colegio, clase in obligaciones:
        if clase.tiene_asistencias and clase.id_clase not in incompletas:
            continue
        obligacion = (ASISTENCIAS, id_colegio, clase.id_grado, clase.id_seccion, clase.fecha.isoformat())
        estado.agregar(not clase.tiene_asistencias, obligacion, contexto.docentes[(id_colegio, clase.id_grado)])
    return estado


# ── Seguimiento de Lectura ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObligacionLectura:
    """Una tabla de Lectura esperada: grupo, subprograma y semana (su lunes), con la fecha
    de la clase que la genera, su tabla si existe y si está pendiente (sin tabla, o tabla
    `normal` con alguna fila "Pendiente")."""

    id_grado: int
    id_seccion: int
    id_programa: int
    lunes: date
    fecha_clase: date
    tabla: Optional[LecturaSemanal]
    pendiente: bool


def _semanas_vencidas(
    db: Session, id_colegio: int, desde: date, hasta: date, calendarios: Calendarios, hoy: date,
    id_periodo: Optional[int],
) -> dict[tuple, date]:
    """{(grado, sección, lunes): fecha de la clase} de las semanas con clase habitual o
    reprogramada con fecha en [desde, min(hasta, hoy)] cuyo sábado ya llegó."""
    fin = min(hasta, hoy)
    if fin < desde:
        return {}
    semanas = {}
    for clase in clases_programadas(db, FiltroGrupos(id_colegio), desde, fin, calendarios):
        lunes = clase.fecha - timedelta(days=clase.fecha.weekday())
        vencida = lunes + timedelta(days=DIAS_HASTA_EL_SABADO) <= hoy
        del_periodo = id_periodo is None or clase.id_periodo_academico == id_periodo
        if clase.tipo == TIPO_HABITUAL and vencida and del_periodo:
            semanas.setdefault((clase.id_grado, clase.id_seccion, lunes), clase.fecha)
    return semanas


def _tablas_del_colegio(db: Session, id_colegio: int, lunes: set[date]) -> dict[tuple, LecturaSemanal]:
    """{(grado, sección, subprograma, lunes): tabla} del colegio en esas semanas."""
    if not lunes:
        return {}
    filas = db.exec(
        select(LecturaSemanal, SemanaReporte.fecha_inicio)
        .join(SemanaReporte, SemanaReporte.id_semana == LecturaSemanal.id_semana)
        .where(LecturaSemanal.id_colegio == id_colegio, col(SemanaReporte.fecha_inicio).in_(lunes))
    ).all()
    return {(t.id_grado, t.id_seccion, t.id_programa, inicio): t for t, inicio in filas}


def obligaciones_lectura(
    db: Session,
    id_colegio: int,
    desde: date,
    hasta: date,
    calendarios: Calendarios,
    hoy: date,
    id_periodo: Optional[int] = None,
) -> list[ObligacionLectura]:
    """Tablas de Lectura que ya debían existir en el colegio (CU011): por grupo, subprograma
    y semana con clase habitual o reprogramada con fecha en [desde, min(hasta, hoy)] (de ese
    PA si se pasa `id_periodo`), cuyo sábado ya llegó, para cada subprograma con alumnos del
    grupo en la fecha de la clase. "No realizada" cumple; una semana sin clase no obliga.
    En orden de grado, sección, semana y subprograma. Consultas fijas por colegio."""
    semanas = _semanas_vencidas(db, id_colegio, desde, hasta, calendarios, hoy, id_periodo)
    if not semanas:
        return []
    intervalos = _intervalos_del_colegio(db, id_colegio, desde, hasta)
    tablas = _tablas_del_colegio(db, id_colegio, {lunes for _, _, lunes in semanas})
    pendientes = tablas_con_pendientes(db, [t.id for t in tablas.values() if t.estado == "normal"])
    obligaciones = []
    for (grado, seccion, lunes), fecha in sorted(semanas.items()):
        programas = {
            i.id_programa for i in intervalos if (i.id_grado, i.id_seccion) == (grado, seccion) and _contiene(i, fecha)
        }
        for id_programa in sorted(programas):
            tabla = tablas.get((grado, seccion, id_programa, lunes))
            pendiente = tabla is None or tabla.id in pendientes
            obligaciones.append(ObligacionLectura(grado, seccion, id_programa, lunes, fecha, tabla, pendiente))
    return obligaciones


def _lectura_del_colegio(db: Session, contexto: ContextoVencidos, id_colegio: int, estado: EstadoRegistros) -> None:
    pa = contexto.pa_actual[id_colegio]
    for obligacion in obligaciones_lectura(
        db, id_colegio, pa.fecha_inicio, pa.fecha_fin, contexto.calendarios, contexto.hoy, pa.id_periodo_academico
    ):
        docentes = contexto.docentes.get((id_colegio, obligacion.id_grado))
        if not obligacion.pendiente or docentes is None:
            continue
        referencia = f"{obligacion.lunes.isoformat()}:{obligacion.id_programa}"
        estado.agregar(
            obligacion.tabla is None,
            (LECTURA, id_colegio, obligacion.id_grado, obligacion.id_seccion, referencia),
            docentes,
        )


def lectura(db: Session, contexto: ContextoVencidos) -> EstadoRegistros:
    estado = EstadoRegistros(colegios_con_registros=colegios_con_filas_confirmadas(db, contexto.pa_actual))
    for id_colegio in sorted(contexto.pa_actual):
        _lectura_del_colegio(db, contexto, id_colegio, estado)
    return estado


# ── Rúbrica ─────────────────────────────────────────────────────────────────────────

def _meses_vencidos(pa: PeriodoCalendario, hoy: date) -> list[tuple[int, int]]:
    """(año, mes) desde el inicio del PA cuyo día 1 del mes siguiente ya llegó."""
    meses = []
    anio, mes = pa.fecha_inicio.year, pa.fecha_inicio.month
    while True:
        siguiente = date(anio + mes // 12, mes % 12 + 1, 1)
        if siguiente > hoy or date(anio, mes, 1) > pa.fecha_fin:
            return meses
        meses.append((anio, mes))
        anio, mes = siguiente.year, siguiente.month


def _mes_exigible(mes: MesEvaluado, pa: PeriodoCalendario) -> bool:
    """Si la rúbrica del mes se exige en el PA (DP-1, provisional): si alguno de los PA que
    pueden evaluarlo es ese, es decir, si el mes tiene días lectivos de él."""
    return regla_mes_exigible_en_pa(
        mes.dias_lectivos, _pa_entre(mes.periodos_que_evaluan), pa.id_periodo_academico
    )


def _pa_entre(periodos: tuple[PeriodoCalendario, ...]) -> Callable[[date], Optional[PeriodoCalendario]]:
    """`pa_de` restringido a esos PA (los del mes, ya resueltos con el calendario del colegio)."""
    return lambda dia: next((p for p in periodos if p.fecha_inicio <= dia <= p.fecha_fin), None)


def _rubrica_del_grupo(db: Session, grupo: GrupoMes, pa: PeriodoCalendario, docentes: list[int], estado: EstadoRegistros) -> None:
    try:
        mes = mes_evaluado(db, grupo)
    except ErrorNegocio:
        return
    if not _mes_exigible(mes, pa):
        return
    evaluaciones = {
        r.id_alumno: r for r in db.exec(select(RubricaMensual).where(
            *(getattr(RubricaMensual, campo) == valor for campo, valor in grupo.columnas().items())
        )).all()
    }
    for alumno in alumnos_del_mes(db, grupo, mes):
        fila = evaluaciones.get(alumno.id_alumno)
        evaluadas = 0 if fila is None else (fila.id_nivel_fluidez is not None) + (fila.id_nivel_comprension is not None)
        if evaluadas < 2:
            obligacion = (RUBRICA, grupo.id_colegio, grupo.id_grado, grupo.id_seccion,
                          f"{grupo.mes}:{alumno.id_alumno}")
            estado.agregar(evaluadas == 0, obligacion, docentes)


def rubrica(db: Session, contexto: ContextoVencidos) -> EstadoRegistros:
    estado = EstadoRegistros(colegios_con_registros=colegios_con_rubricas(db, contexto.pa_actual))
    for id_colegio, pa in sorted(contexto.pa_actual.items()):
        calendario = contexto.calendarios.calendario_en(id_colegio, contexto.hoy)
        intervalos = _intervalos_del_colegio(db, id_colegio, pa.fecha_inicio, pa.fecha_fin)
        grupos = {(i.id_grado, i.id_seccion, i.id_programa) for i in intervalos if (id_colegio, i.id_grado) in contexto.docentes}
        for _, mes in _meses_vencidos(pa, contexto.hoy):
            for grado, seccion, programa in sorted(grupos):
                grupo = GrupoMes(id_colegio, grado, seccion, programa, calendario.id_anio_escolar, mes)
                _rubrica_del_grupo(db, grupo, pa, contexto.docentes[(id_colegio, grado)], estado)
    return estado


# ── Registro de Vuelo ───────────────────────────────────────────────────────────────

def _pas_terminados(contexto: ContextoVencidos, id_colegio: int) -> list[PeriodoCalendario]:
    calendario = contexto.calendarios.calendario_en(id_colegio, contexto.hoy)
    if calendario is None:
        return []
    return [p for p in calendario.periodos if p.es_pa and p.fecha_fin < contexto.hoy]


def _ultimo_grupo(intervalos: list[AlumnoPertenencia], pa: PeriodoCalendario) -> dict[int, AlumnoPertenencia]:
    """{alumno: su último intervalo dentro del PA} (el grupo en que se le exige)."""
    ultimo: dict[int, AlumnoPertenencia] = {}
    for i in intervalos:
        if i.desde <= pa.fecha_fin and (i.hasta is None or i.hasta > pa.fecha_inicio):
            if i.id_alumno not in ultimo or i.desde > ultimo[i.id_alumno].desde:
                ultimo[i.id_alumno] = i
    return ultimo


def _vuelo_del_pa(db: Session, id_colegio: int, pa: PeriodoCalendario, estado: EstadoRegistros) -> None:
    docentes = docentes_por_grado(db, {id_colegio: pa})
    if not docentes:
        return
    grupos = _ultimo_grupo(_intervalos_del_colegio(db, id_colegio, pa.fecha_inicio, pa.fecha_fin), pa)
    filas: dict[int, list[RegistroVuelo]] = defaultdict(list)
    for fila in db.exec(select(RegistroVuelo).where(
        col(RegistroVuelo.id_alumno).in_(list(grupos)), RegistroVuelo.id_periodo_academico == pa.id_periodo_academico,
    )).all():
        filas[fila.id_alumno].append(fila)
    for id_alumno, intervalo in sorted(grupos.items()):
        if (id_colegio, intervalo.id_grado) not in docentes:
            continue
        if any(f.id_nivel_colocado is not None for f in filas[id_alumno]):
            continue
        obligacion = (REGISTRO_VUELO, id_colegio, intervalo.id_grado, intervalo.id_seccion,
                      f"{pa.id_periodo_academico}:{id_alumno}")
        estado.agregar(not filas[id_alumno], obligacion, docentes[(id_colegio, intervalo.id_grado)])


def registro_vuelo(db: Session, contexto: ContextoVencidos) -> EstadoRegistros:
    estado = EstadoRegistros(colegios_con_registros=colegios_con_decisiones(db, contexto.pa_actual))
    for id_colegio in contexto.colegios_activos:
        for pa in _pas_terminados(contexto, id_colegio):
            _vuelo_del_pa(db, id_colegio, pa, estado)
    return estado


# ── Todos los módulos ───────────────────────────────────────────────────────────────

EVALUADORES: dict[str, Evaluador] = {
    ASISTENCIAS: asistencias,
    LECTURA: lectura,
    RUBRICA: rubrica,
    REGISTRO_VUELO: registro_vuelo,
}


def estado_de_registros(db: Session, calendarios: Optional[Calendarios] = None) -> EstadoRegistros:
    """Vencidos e incompletos de los cuatro módulos. Con `calendarios` ya cargados no
    vuelve a leerlos."""
    calendarios = calendarios or cargar_calendarios(db)
    pa_actual = pa_actual_de_colegios(db, solo_activos=True, calendarios=calendarios)
    contexto = ContextoVencidos(
        calendarios=calendarios, pa_actual=pa_actual, docentes=docentes_por_grado(db, pa_actual),
        colegios_activos=sorted(db.exec(select(Colegio.id_colegio).where(col(Colegio.activo).is_(True))).all()),
        hoy=hoy_lima(),
    )
    estado = EstadoRegistros()
    for evaluador in EVALUADORES.values():
        estado.absorber(evaluador(db, contexto))
    return estado
