"""
El año de referencia sigue CU015; el PA es opcional en indicadores académicos.
El cumplimiento de CU011 se refiere exclusivamente a obligaciones exigibles HOY.
"""
from collections import Counter, defaultdict
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.core.tiempo import hoy_lima
from app.models.evaluacion import (
    ESTADOS_ASISTIO, Asistencia, Clase, ClaseAlumno, LECTURA_NORMAL,
    LecturaSemanal, LecturaSemanalAlumno, NivelRazkids, NivelRubrica,
    EscalaNivelDescriptivo, RegistroVuelo, RubricaMensual,
)
from app.models.organizacion import Alumno, AlumnoPertenencia, AnioEscolar, Colegio, Grado, PeriodoAcademico, Programa, Seccion
from app.schemas.dashboard import (
    AsistenciaDashboard, CumplimientoDashboard, CumplimientoModulo,
    FiltrosDashboard, LecturaDashboard, NivelDashboard,
    AprendizajePeriodoDashboard, AprendizajeSubprograma, DistribucionAprendizaje,
)
from app.services import lectura_service, registro_vuelo
from app.services.calendario import anio_de_referencia, cargar_calendarios, pa_actual_de_colegios
from app.services.registros_vencidos import MODULOS, estado_de_registros
from app.services.nivel_descriptivo import EscalaDescriptiva


def validar_filtros(db: Session, f: FiltrosDashboard) -> Optional[AnioEscolar]:
    """Comprobar existencia y coherencia de identificadores, nunca ignorar filtros inválidos."""
    if f.id_colegios is not None:
        if not f.id_colegios or len(f.id_colegios) != len(set(f.id_colegios)):
            raise error_de_campo("id_colegios", "Seleccione colegios sin duplicados.")
        if f.id_colegio is not None and f.id_colegio not in f.id_colegios:
            raise error_de_campo("id_colegios", "Los filtros de colegio son incompatibles.")
        for identificador in f.id_colegios:
            if identificador <= 0 or db.get(Colegio, identificador) is None:
                raise error_de_campo("id_colegios", "Uno de los colegios no existe.")
    for campo, modelo, pk in (
        ("id_colegio", Colegio, Colegio.id_colegio),
        ("id_anio_escolar", AnioEscolar, AnioEscolar.id_anio_escolar),
        ("id_grado", Grado, Grado.id_grado),
        ("id_seccion", Seccion, Seccion.id_seccion),
        ("id_programa", Programa, Programa.id_programa),
        ("id_periodo_academico", PeriodoAcademico, PeriodoAcademico.id_periodo_academico),
    ):
        valor = getattr(f, campo)
        if valor is not None and (valor <= 0 or db.exec(select(pk).where(pk == valor)).first() is None):
            raise error_de_campo(campo, "El identificador no existe o no es válido.")
    if f.id_seccion is not None:
        seccion = db.get(Seccion, f.id_seccion)
        if (f.id_colegio is not None and seccion.id_colegio != f.id_colegio) or (
            f.id_colegios is not None and seccion.id_colegio not in f.id_colegios
        ):
            raise error_de_campo("id_seccion", "La sección no pertenece a los colegios indicados.")
        if f.id_grado is not None and seccion.id_grado != f.id_grado:
            raise error_de_campo("id_seccion", "La sección no pertenece al grado indicado.")
    if f.id_periodo_academico is not None:
        periodo = db.get(PeriodoAcademico, f.id_periodo_academico)
        if f.id_anio_escolar is not None and periodo.id_anio_escolar != f.id_anio_escolar:
            raise error_de_campo("id_periodo_academico", "El PA no pertenece al año escolar seleccionado.")
        return db.get(AnioEscolar, periodo.id_anio_escolar)
    if f.id_anio_escolar is not None:
        return db.get(AnioEscolar, f.id_anio_escolar)
    return anio_de_referencia(db)


def _grupo(f: FiltrosDashboard, modelo, programa: bool = True):
    condiciones = []
    for campo in ("id_colegio", "id_grado", "id_seccion"):
        if getattr(f, campo) is not None:
            condiciones.append(getattr(modelo, campo) == getattr(f, campo))
    if f.id_colegios is not None:
        condiciones.append(col(modelo.id_colegio).in_(f.id_colegios))
    if programa and f.id_programa is not None:
        condiciones.append(modelo.id_programa == f.id_programa)
    return condiciones


def _periodos(db: Session, anio: Optional[AnioEscolar], f: FiltrosDashboard) -> list[int]:
    if anio is None:
        return []
    if f.id_periodo_academico is not None:
        return [f.id_periodo_academico]
    return list(db.exec(select(PeriodoAcademico.id_periodo_academico).where(
        PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar
    )).all())


def asistencia(db: Session, f: FiltrosDashboard, por_alumno: bool = False) -> list[AsistenciaDashboard]:
    """CU033: sumar únicamente estados confirmados; no imputar ausencias a sin marcar.

    La lista histórica ClaseAlumno evita trasladar asistencias tras cambiar de grupo.
    Las clases con estados parciales se incluyen y se señalan como provisionales.
    """
    anio = validar_filtros(db, f)
    periodos = _periodos(db, anio, f)
    if not periodos:
        return []
    # OUTER JOIN: una fila histórica sin asistencia se considera "sin marcar".
    filas = db.exec(select(
        ClaseAlumno.id_alumno, Clase.id_grado, Clase.id_clase, Asistencia.estado,
    ).join(Clase, Clase.id_clase == ClaseAlumno.id_clase).outerjoin(
        Asistencia, (Asistencia.id_clase == ClaseAlumno.id_clase) &
        (Asistencia.id_alumno == ClaseAlumno.id_alumno)
    ).where(
        col(Clase.id_periodo_academico).in_(periodos),
        Clase.fecha <= hoy_lima(),
        *_grupo(f, Clase, programa=False),
        *([ClaseAlumno.id_programa == f.id_programa] if f.id_programa is not None else []),
    )).all()
    # Las clases incompletas se calculan exclusivamente dentro del conjunto filtrado.
    # No mezclar alumnos de otros subprogramas ni de otros grados/secciones.
    por_grupo = defaultdict(list)
    for id_alumno, grado, id_clase, estado in filas:
        clave = (id_alumno if por_alumno else None, grado)
        por_grupo[clave].append((id_clase, estado))
    salida = []
    for (id_alumno, grado), registros in sorted(
        por_grupo.items(), key=lambda par: (par[0][1], par[0][0] or 0)
    ):
        estados = Counter(estado for _, estado in registros)
        presentes = estados['presente']
        tardanzas = estados['tardanza']
        ausentes = estados['ausente']
        sin_marcar = sum(1 for _, estado in registros if estado is None)
        total_confirmadas = presentes + tardanzas + ausentes
        clases = {id_clase for id_clase, _ in registros}
        estados_por_clase = defaultdict(list)
        for id_clase, estado in registros:
            estados_por_clase[id_clase].append(estado)
        incompletas = sum(
            any(estado is None for estado in estados_clase)
            and any(estado is not None for estado in estados_clase)
            for estados_clase in estados_por_clase.values()
        ) if not por_alumno else 0
        sin_registro = sum(
            all(estado is None for estado in estados_clase)
            for estados_clase in estados_por_clase.values()
        )
        salida.append(AsistenciaDashboard(
            id_alumno=id_alumno, id_grado=grado,
            asistidas=presentes + tardanzas, contabilizadas=total_confirmadas,
            porcentaje=round(100 * (presentes + tardanzas) / total_confirmadas, 1)
            if total_confirmadas else None,
            presentes=presentes, tardanzas=tardanzas, ausentes=ausentes,
            sin_marcar=sin_marcar, clases_incompletas=incompletas,
            clases_sin_registro=sin_registro,
            provisional=bool(sin_marcar or sin_registro),
        ))
    return salida


def libros(db: Session, f: FiltrosDashboard) -> LecturaDashboard:
    """Total de libros en el año o PA solicitado, según la clase de referencia."""
    anio = validar_filtros(db, f)
    if anio is None:
        return LecturaDashboard(total_libros=0, libros_lsn=0, libros_lsl=0)
    filas = db.exec(select(
        LecturaSemanalAlumno.id_alumno, LecturaSemanalAlumno.lsn_titulo,
        LecturaSemanalAlumno.cantidad_lsl, Clase.fecha,
    ).join(LecturaSemanal, LecturaSemanal.id == LecturaSemanalAlumno.id_lectura_semanal)
    .join(Clase, Clase.id_clase == LecturaSemanal.id_clase_referencia)
    .join(Asistencia, (Asistencia.id_clase == Clase.id_clase) &
          (Asistencia.id_alumno == LecturaSemanalAlumno.id_alumno))
    .where(
        LecturaSemanal.id_anio_escolar == anio.id_anio_escolar,
        *([Clase.id_periodo_academico == f.id_periodo_academico]
          if f.id_periodo_academico is not None else []),
        LecturaSemanal.estado == LECTURA_NORMAL,
        col(Asistencia.estado).in_(ESTADOS_ASISTIO),
        *_grupo(f, LecturaSemanal),
    )).all()
    lsn, lsl = 0, 0
    por_mes = defaultdict(int)
    alumnos_con_lecturas = set()
    for id_alumno, titulo, cantidad, fecha_clase in filas:
        a = int(titulo is not None)
        b = cantidad or 0
        if a + b > 0:
            alumnos_con_lecturas.add(id_alumno)
        lsn += a
        lsl += b
        por_mes[fecha_clase.month] += a + b
    total = lsn + lsl
    n_alumnos = len(alumnos_con_lecturas)
    return LecturaDashboard(
        total_libros=total, libros_lsn=lsn, libros_lsl=lsl,
        alumnos_con_lecturas=n_alumnos,
        promedio_por_alumno_con_lecturas=round(total / n_alumnos, 2) if n_alumnos else None,
        por_mes=dict(sorted(por_mes.items())),
    )


def niveles(db: Session, f: FiltrosDashboard) -> list[NivelDashboard]:
    """CU031: una decisión vigente por alumno y PA, incluso si cambió de grupo."""
    anio = validar_filtros(db, f)
    periodos = _periodos(db, anio, f)
    if not periodos:
        return []
    # Se consultan las decisiones de todo el año; filtrar *después* de escoger la
    # vigente evita contar una decisión antigua de otro grupo tras un traslado.
    from app.models.evaluacion import RegistroVuelo
    filas = db.exec(select(RegistroVuelo).where(
        RegistroVuelo.id_anio_escolar == anio.id_anio_escolar,
        col(RegistroVuelo.id_periodo_academico).in_(periodos),
    )).all()
    por_alumno_pa: dict[tuple[int, int], list[RegistroVuelo]] = {}
    for r in filas:
        por_alumno_pa.setdefault((r.id_alumno, r.id_periodo_academico), []).append(r)
    conteo = Counter()
    for (_, pa), registros in por_alumno_pa.items():
        decision = registro_vuelo.decision_del_periodo(registros, pa)
        if decision is None or (f.id_colegios is not None and decision.id_colegio not in f.id_colegios) or any(getattr(decision, campo) != getattr(f, campo)
                                   for campo in ("id_colegio", "id_grado", "id_seccion", "id_programa")
                                   if getattr(f, campo) is not None):
            continue
        conteo[(pa, decision.id_nivel_colocado)] += 1
    letras = dict(db.exec(select(NivelRazkids.id_nivel_rk, NivelRazkids.letra)).all())
    return [NivelDashboard(id_periodo_academico=pa, id_nivel_rk=nivel,
                           letra=letras[nivel], alumnos=cantidad)
            for (pa, nivel), cantidad in sorted(conteo.items())]



def aprendizaje(db: Session, f: FiltrosDashboard) -> AprendizajePeriodoDashboard:
    """CU031/CU033: corte por colegio y PA; alumno único según pertenencia histórica.

    Global usa la última decisión válida de Registro de Vuelo y la escala de su grado.
    Fluidez y Comprensión usan la última rúbrica no nula de cada dimensión en el PA.
    Los casos sin una decisión o evaluación quedan pendientes y no entran al divisor.
    """
    anio = validar_filtros(db, f)
    if f.id_periodo_academico is None:
        raise error_de_campo("id_periodo_academico", "Seleccione un PA.")
    pa = db.get(PeriodoAcademico, f.id_periodo_academico)
    if anio is None or pa is None:
        raise error_de_campo("id_periodo_academico", "PA inexistente.")

    # Nunca inferir un PA común: cada colegio tiene un calendario particular o general.
    ids = list(db.exec(select(Colegio.id_colegio)).all())
    ids = [c for c in ids if (f.id_colegio is None or c == f.id_colegio)
           and (f.id_colegios is None or c in f.id_colegios)]
    calendarios = cargar_calendarios(db, anio.id_anio_escolar, ids_colegio=ids)
    hoy = hoy_lima()
    cortes = {}
    for colegio in ids:
        calendario = calendarios.aplicable(colegio, anio.id_anio_escolar)
        rango = calendario.pa_de(pa.id_periodo_academico) if calendario else None
        if rango is not None and rango.fecha_inicio <= hoy:
            cortes[colegio] = min(rango.fecha_fin, hoy)

    # Fecha de pertenencia inclusiva desde, exclusiva hasta. Una sola fila por alumno
    # puede estar vigente al corte: evita duplicados por traslados entre colegios.
    grupos = {}
    for pertenencia in db.exec(select(AlumnoPertenencia).where(
        col(AlumnoPertenencia.id_colegio).in_(list(cortes))
    )).all() if cortes else []:
        corte = cortes[pertenencia.id_colegio]
        if not (pertenencia.desde <= corte and
                (pertenencia.hasta is None or corte < pertenencia.hasta)):
            continue
        if (f.id_grado is not None and pertenencia.id_grado != f.id_grado or
            f.id_seccion is not None and pertenencia.id_seccion != f.id_seccion or
            f.id_programa is not None and pertenencia.id_programa != f.id_programa):
            continue
        grupos[pertenencia.id_alumno] = pertenencia

    decisiones_por_alumno = defaultdict(list)
    rubricas_por_alumno = defaultdict(list)
    if grupos:
        for registro in db.exec(select(RegistroVuelo).where(
            RegistroVuelo.id_periodo_academico == pa.id_periodo_academico,
            col(RegistroVuelo.id_alumno).in_(list(grupos)),
        )).all():
            decisiones_por_alumno[registro.id_alumno].append(registro)
        for rubrica in db.exec(select(RubricaMensual).where(
            RubricaMensual.id_periodo_academico == pa.id_periodo_academico,
            col(RubricaMensual.id_alumno).in_(list(grupos)),
        )).all():
            rubricas_por_alumno[rubrica.id_alumno].append(rubrica)

    escalas = defaultdict(list)
    for nivel in db.exec(select(EscalaNivelDescriptivo)).all():
        escalas[nivel.id_grado].append((nivel.id_nivel_rk_desde, nivel.nivel))
    escalas = {grado: EscalaDescriptiva(tuple(sorted(valores))) for grado, valores in escalas.items()}
    catalogo_rubricas = {r.id_nivel_rubrica: r for r in db.exec(select(NivelRubrica)).all()}
    programas = {p.id_programa: p.nombre for p in db.exec(select(Programa)).all()}
    # Conteos por subprograma y dimensión. El universo incluye a cada alumno incluso
    # cuando todavía no tiene ningún Registro de Vuelo ni Rúbrica.
    totales = Counter()
    contadores = defaultdict(Counter)
    for alumno, pertenencia in grupos.items():
        programa = pertenencia.id_programa
        totales[programa] += 1
        decision = registro_vuelo.decision_del_periodo(
            decisiones_por_alumno[alumno], pa.id_periodo_academico
        )
        escala = escalas.get(pertenencia.id_grado)
        global_nivel = escala.nivel(decision.id_nivel_colocado) if escala and decision else None
        if global_nivel is not None:
            contadores[(programa, "Global")][global_nivel] += 1

        filas = sorted(rubricas_por_alumno[alumno],
                       key=lambda r: (r.mes, r.fecha_registro), reverse=True)
        for dimension, atributo in (("Fluidez", "id_nivel_fluidez"),
                                    ("Comprensión", "id_nivel_comprension")):
            elegido = next((catalogo_rubricas.get(getattr(r, atributo))
                            for r in filas if getattr(r, atributo) is not None), None)
            if elegido is not None and elegido.id_programa == programa and elegido.dimension == dimension:
                contadores[(programa, dimension)][elegido.nombre_nivel] += 1

    resultados = []
    for programa, total in sorted(totales.items()):
        distribuciones = []
        for dimension in ("Global", "Fluidez", "Comprensión"):
            niveles = dict(contadores[(programa, dimension)])
            evaluados = sum(niveles.values())
            distribuciones.append(DistribucionAprendizaje(
                dimension=dimension, niveles=niveles, evaluados=evaluados,
                pendientes=total-evaluados, total_alumnos=total,
                porcentajes={nombre: round(100*cantidad/evaluados, 1)
                             for nombre, cantidad in niveles.items()} if evaluados else None,
            ))
        resultados.append(AprendizajeSubprograma(
            id_programa=programa, nombre_programa=programas.get(programa, str(programa)),
            distribuciones=distribuciones,
        ))
    return AprendizajePeriodoDashboard(
        id_periodo_academico=pa.id_periodo_academico,
        id_anio_escolar=anio.id_anio_escolar,
        fecha_corte_por_colegio={colegio: corte.isoformat() for colegio, corte in cortes.items()},
        resultados=resultados,
    )

def cumplimiento(db: Session, f: FiltrosDashboard) -> CumplimientoDashboard:
    """CU011: vencidos e incompletos exigibles hoy, nunca un histórico inventado.

    El evaluador no distingue subprograma dentro de la obligación. Por seguridad,
    una petición por programa se rechaza en vez de devolver un agregado falso.
    """
    anio = validar_filtros(db, f)
    if f.id_programa is not None:
        raise error_de_campo(
            "id_programa",
            "CU011 no permite desagregar todas las obligaciones por subprograma.",
        )

    calendarios = cargar_calendarios(db)
    pa_actual = pa_actual_de_colegios(db, solo_activos=True, calendarios=calendarios)

    # No aplicar un filtro de año que solo se valida pero no se usa: CU011
    # describe obligaciones del PA vigente de cada colegio, no de años pasados.
    anios_pa = {
        pa.id_periodo_academico: db.get(PeriodoAcademico, pa.id_periodo_academico).id_anio_escolar
        for pa in pa_actual.values() if pa.id_periodo_academico is not None
    }
    if f.id_anio_escolar is not None and not any(
        anios_pa.get(pa.id_periodo_academico) == f.id_anio_escolar
        and (f.id_colegio is None or colegio == f.id_colegio)
        and (f.id_colegios is None or colegio in f.id_colegios)
        for colegio, pa in pa_actual.items()
    ):
        raise error_de_campo(
            "id_anio_escolar",
            "CU011 solo permite consultar obligaciones exigibles del año escolar vigente.",
        )

    if f.id_periodo_academico is not None and not any(
        pa.id_periodo_academico == f.id_periodo_academico
        and (f.id_colegio is None or colegio == f.id_colegio)
        and (f.id_colegios is None or colegio in f.id_colegios)
        for colegio, pa in pa_actual.items()
    ):
        raise error_de_campo(
            "id_periodo_academico",
            "CU011 solo permite consultar obligaciones del PA vigente.",
        )

    estado = estado_de_registros(db, calendarios)

    def corresponde(item, modulo):
        if item.modulo != modulo:
            return False
        if f.id_colegio is not None and item.id_colegio != f.id_colegio:
            return False
        if f.id_colegios is not None and item.id_colegio not in f.id_colegios:
            return False
        if f.id_grado is not None and item.id_grado != f.id_grado:
            return False
        if f.id_seccion is not None and item.id_seccion != f.id_seccion:
            return False
        pa = pa_actual.get(item.id_colegio)
        if f.id_anio_escolar is not None and (pa is None or anios_pa.get(pa.id_periodo_academico) != f.id_anio_escolar):
            return False
        if f.id_periodo_academico is not None and (
            pa is None or pa.id_periodo_academico != f.id_periodo_academico
        ):
            return False
        return True

    return CumplimientoDashboard(modulos=[
        CumplimientoModulo(
            modulo=modulo,
            vencidos=sum(corresponde(item, modulo) for item in estado.vencidas),
            incompletos=sum(corresponde(item, modulo) for item in estado.incompletas),
        )
        for modulo in MODULOS
    ])