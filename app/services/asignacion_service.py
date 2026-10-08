"""Asignaciones de los Docentes (CU016): qué colegio y grados atiende en cada periodo.

Reglas de negocio:
- Un docente trabaja en un solo colegio y, por ahora, enseña todos los grados que ese
  colegio ofrece (`colegio_grado`). Cada asignación se guarda como una fila por grado y
  periodo en `docente_colegio_grado`, para que en el futuro pueda enseñar solo algunos.
- Un solo docente por (colegio, grado, periodo) (UNIQUE en la BD).
- Solo se crean o reemplazan filas en periodos no terminados (`fecha_fin >= hoy`): los
  periodos pasados son historial y nunca se tocan.

Las asignaciones se cambian con POST y PATCH /usuarios; aquí no se hace commit.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import status
from sqlalchemy import and_, delete, func, or_
from sqlmodel import Session, col, select

from app.core.ciclo import calcular_ciclo, ordenar_ciclos
from app.core.errores import ErrorNegocio
from app.core.tiempo import hoy_lima
from app.models.organizacion import (
    Alumno,
    AnioEscolar,
    CicloEbr,
    Colegio,
    ColegioGrado,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
)
from app.schemas.asignacion import AsignacionItem, MiAsignacion
from app.schemas.usuario import AsignacionDetalle, GradoAsignado, PeriodoAsignado
from app.services.alcance import alcance_docente
from app.services.calendario import (
    no_terminado,
    periodo_vigente,
    periodos_del_anio,
    proximo_periodo,
)

SIN_ASIGNACION = "Sin asignación"


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


@dataclass(frozen=True)
class Asignacion:
    """Colegio y grados de un docente a partir de un periodo de un año escolar."""

    colegio: Colegio
    grados: list[Grado]
    anio: AnioEscolar
    periodos: list[PeriodoAcademico]

    def resumen(self) -> str:
        return resumen_asignacion(self.colegio.nombre, self.grados, self.periodos[0], self.anio)


def resumen_asignacion(
    colegio: str, grados: list[Grado], desde: PeriodoAcademico, anio: AnioEscolar
) -> str:
    """Texto legible para la auditoría: `AMASHCA [1.º, 2.º] desde P3 2026`."""
    nombres = ", ".join(g.nombre for g in sorted(grados, key=lambda g: g.id_grado))
    return f"{colegio} [{nombres}] desde P{desde.numero} {anio.nombre}"


# ── Validación de los datos de una asignación ───────────────────────────────────────

def colegio_activo(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    if not colegio.activo:
        raise _no_procesable(f"El colegio {colegio.nombre} está inactivo.", "colegio_inactivo")
    return colegio


def grados_del_colegio(db: Session, id_colegio: int) -> list[Grado]:
    return list(
        db.exec(
            select(Grado)
            .join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
            .where(ColegioGrado.id_colegio == id_colegio)
            .order_by(Grado.id_grado)
        ).all()
    )


def resolver_grados(db: Session, colegio: Colegio, ids_grados: Optional[list[int]]) -> list[Grado]:
    """Los grados pedidos, que el colegio debe ofrecer; sin pedido, todos los que ofrece."""
    ofrecidos = {g.id_grado: g for g in grados_del_colegio(db, colegio.id_colegio)}
    if not ofrecidos:
        raise _no_procesable(
            f"El colegio {colegio.nombre} no tiene grados registrados.", "colegio_sin_grados"
        )
    if ids_grados is None:
        return list(ofrecidos.values())
    no_ofrecidos = sorted(set(ids_grados) - set(ofrecidos))
    if no_ofrecidos:
        raise _no_procesable(
            f"El colegio {colegio.nombre} no ofrece los grados con id {no_ofrecidos}.",
            "grado_no_ofrecido",
        )
    return [ofrecidos[i] for i in sorted(set(ids_grados))]


def anio_con_periodos(db: Session, id_anio_escolar: int) -> tuple[AnioEscolar, list[PeriodoAcademico]]:
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise _no_procesable("No existe el año escolar indicado.", "anio_inexistente")
    periodos = periodos_del_anio(db, id_anio_escolar)
    if not periodos:
        raise _no_procesable("El año escolar no tiene periodos cargados.", "anio_sin_periodos")
    return anio, periodos


def periodo_de_inicio(
    db: Session, periodos: list[PeriodoAcademico], id_periodo: Optional[int]
) -> Optional[PeriodoAcademico]:
    """Desde qué periodo del año aplica un cambio.

    Con `id_periodo`, ese (debe ser del año y no estar terminado). Sin él, el vigente o,
    si estamos entre periodos, el próximo; si ninguno es de ese año, None (= desde el
    primer periodo no terminado del año).
    """
    if id_periodo is not None:
        elegido = next((p for p in periodos if p.id_periodo_academico == id_periodo), None)
        if elegido is None:
            raise _no_procesable(
                "El periodo indicado no pertenece al año escolar de la asignación.",
                "periodo_fuera_del_anio",
            )
        if not no_terminado(elegido):
            raise _no_procesable("El periodo indicado ya terminó.", "periodo_terminado")
        return elegido
    ids_del_anio = {p.id_periodo_academico for p in periodos}
    for candidato in (periodo_vigente(db), proximo_periodo(db)):
        if candidato is not None and candidato.id_periodo_academico in ids_del_anio:
            return candidato
    return None


def periodos_asignables(
    periodos: list[PeriodoAcademico], desde: Optional[PeriodoAcademico] = None
) -> list[PeriodoAcademico]:
    """Periodos no terminados del año, desde `desde` en adelante."""
    asignables = [
        p for p in periodos
        if no_terminado(p) and (desde is None or p.fecha_inicio >= desde.fecha_inicio)
    ]
    if not asignables:
        raise _no_procesable(
            "El año escolar no tiene periodos vigentes ni futuros para asignar.",
            "sin_periodos_asignables",
        )
    return asignables


# ── Escritura ───────────────────────────────────────────────────────────────────────

def _verificar_grados_libres(db: Session, id_docente: int, asignacion: Asignacion) -> None:
    """409 si alguno de esos grados ya lo tiene otro docente en alguno de esos periodos."""
    ocupada = db.exec(
        select(Grado.nombre, Docente.nombres, Docente.apellidos, PeriodoAcademico.numero)
        .select_from(DocenteColegioGrado)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .join(Docente, Docente.id_docente == DocenteColegioGrado.id_docente)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .where(
            DocenteColegioGrado.id_colegio == asignacion.colegio.id_colegio,
            col(DocenteColegioGrado.id_grado).in_([g.id_grado for g in asignacion.grados]),
            col(DocenteColegioGrado.id_periodo_academico).in_(
                [p.id_periodo_academico for p in asignacion.periodos]
            ),
            DocenteColegioGrado.id_docente != id_docente,
        )
        .order_by(PeriodoAcademico.fecha_inicio, Grado.id_grado)
    ).first()
    if ocupada is not None:
        grado, nombres, apellidos, numero = ocupada
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            f"{grado} de {asignacion.colegio.nombre} ya está asignado a {nombres} {apellidos} "
            f"en el periodo {numero} de {asignacion.anio.nombre}.",
            motivo="grado_asignado",
        )


def reemplazar_asignaciones(
    db: Session, id_docente: int, asignacion: Asignacion, id_actor: int, ahora: datetime
) -> None:
    """Deja al docente con ese colegio y grados en esos periodos (y solo en esos).

    Borra sus filas de esos periodos e inserta las nuevas: son periodos vigentes o
    futuros, así que no se pierde historial.
    """
    _verificar_grados_libres(db, id_docente, asignacion)
    ids_periodo = [p.id_periodo_academico for p in asignacion.periodos]
    db.execute(
        delete(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == id_docente,
            col(DocenteColegioGrado.id_periodo_academico).in_(ids_periodo),
        )
    )
    db.add_all(
        DocenteColegioGrado(
            id_docente=id_docente,
            id_colegio=asignacion.colegio.id_colegio,
            id_grado=grado.id_grado,
            id_periodo_academico=id_periodo,
            creado_por=id_actor,
            creado_en=ahora,
        )
        for id_periodo in ids_periodo
        for grado in asignacion.grados
    )


def planificar_asignacion_nueva(
    db: Session,
    id_colegio: int,
    id_anio_escolar: int,
    ids_grados: Optional[list[int]],
    desde_periodo: Optional[int] = None,
) -> Asignacion:
    """Asignación de un docente que no tiene una previa (alta o paso a Docente)."""
    colegio = colegio_activo(db, id_colegio)
    grados = resolver_grados(db, colegio, ids_grados)
    anio, periodos = anio_con_periodos(db, id_anio_escolar)
    desde = periodo_de_inicio(db, periodos, desde_periodo) if desde_periodo is not None else None
    return Asignacion(colegio, grados, anio, periodos_asignables(periodos, desde))


@dataclass(frozen=True)
class EstadoActual:
    """Último periodo asignado del docente, con su colegio y grados."""

    periodo: PeriodoAcademico
    id_colegio: int
    ids_grados: list[int]


def estado_en(db: Session, id_docente: int, periodo: Optional[PeriodoAcademico] = None) -> Optional[EstadoActual]:
    """Colegio y grados del docente en `periodo` o, sin él, en su último periodo asignado."""
    consulta = (
        select(DocenteColegioGrado, PeriodoAcademico)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .where(DocenteColegioGrado.id_docente == id_docente)
    )
    if periodo is not None:
        consulta = consulta.where(
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico
        )
    filas = db.exec(consulta.order_by(col(PeriodoAcademico.fecha_inicio).desc())).all()
    if not filas:
        return None
    ultimo = filas[0][1]
    del_ultimo = [fila for fila, p in filas if p.id_periodo_academico == ultimo.id_periodo_academico]
    return EstadoActual(
        periodo=ultimo,
        id_colegio=del_ultimo[0].id_colegio,
        ids_grados=sorted(f.id_grado for f in del_ultimo),
    )


def planificar_cambio(
    db: Session,
    id_docente: int,
    id_colegio: Optional[int],
    ids_grados: Optional[list[int]],
    id_anio_escolar: Optional[int],
    desde_periodo: Optional[int],
) -> Asignacion:
    """Nueva asignación de un docente que ya tiene una (PATCH /usuarios).

    - Mismo año: aplica desde `desde_periodo` (por defecto el vigente o el próximo).
      Sin `grados`: si cambia el colegio, todos los del colegio nuevo; si no, los actuales.
    - Otro año (renovación): los periodos vigentes o futuros de ese año, con el colegio y
      los grados enviados o, si no se envían, los de su último periodo asignado.
    """
    ultimo = estado_en(db, id_docente)
    if ultimo is None:
        if id_colegio is None or id_anio_escolar is None:
            raise _no_procesable(
                "El docente no tiene asignaciones: se requieren id_colegio e id_anio_escolar.",
                "asignacion_incompleta",
            )
        return planificar_asignacion_nueva(db, id_colegio, id_anio_escolar, ids_grados, desde_periodo)

    id_anio = id_anio_escolar if id_anio_escolar is not None else ultimo.periodo.id_anio_escolar
    anio, periodos = anio_con_periodos(db, id_anio)
    renovacion = id_anio != ultimo.periodo.id_anio_escolar
    desde, base = _inicio_y_base(db, id_docente, ultimo, periodos, desde_periodo, renovacion)
    colegio = colegio_activo(db, id_colegio if id_colegio is not None else base.id_colegio)
    grados = _grados_del_cambio(db, colegio, ids_grados, base)
    return Asignacion(colegio, grados, anio, periodos_asignables(periodos, desde))


def _inicio_y_base(
    db: Session,
    id_docente: int,
    ultimo: EstadoActual,
    periodos: list[PeriodoAcademico],
    desde_periodo: Optional[int],
    renovacion: bool,
) -> tuple[Optional[PeriodoAcademico], EstadoActual]:
    """Desde qué periodo aplica el cambio y qué asignación toma como punto de partida.

    Renovación: todo el año salvo que se indique `desde_periodo`, partiendo de su último
    periodo asignado. Mismo año: desde el periodo indicado (o el vigente o el próximo),
    partiendo de lo que tiene en ese periodo.
    """
    if renovacion:
        desde = periodo_de_inicio(db, periodos, desde_periodo) if desde_periodo is not None else None
        return desde, ultimo
    desde = periodo_de_inicio(db, periodos, desde_periodo)
    base = estado_en(db, id_docente, desde) if desde is not None else None
    return desde, base or ultimo


def _grados_del_cambio(
    db: Session, colegio: Colegio, ids_grados: Optional[list[int]], base: EstadoActual
) -> list[Grado]:
    """Los grados enviados; sin ellos, todos los del colegio si cambió, o los actuales."""
    if ids_grados is not None:
        return resolver_grados(db, colegio, ids_grados)
    if colegio.id_colegio != base.id_colegio:
        return resolver_grados(db, colegio, None)
    # Se conservan los grados actuales tal como están asignados.
    return list(
        db.exec(
            select(Grado).where(col(Grado.id_grado).in_(base.ids_grados)).order_by(Grado.id_grado)
        ).all()
    )


def resumen_actual(db: Session, id_docente: int, periodos: list[PeriodoAcademico], anio: AnioEscolar) -> str:
    """Resumen de lo que el docente tiene hoy en esos periodos (el "antes" de un cambio)."""
    for periodo in periodos:
        estado = estado_en(db, id_docente, periodo)
        if estado is not None:
            colegio = db.get(Colegio, estado.id_colegio)
            grados = list(db.exec(select(Grado).where(col(Grado.id_grado).in_(estado.ids_grados))).all())
            return resumen_asignacion(colegio.nombre, grados, periodo, anio)
    return SIN_ASIGNACION


def liberar_asignaciones(db: Session, id_docente: int) -> str:
    """Quita al docente de sus grados desde el periodo vigente (incluido) en adelante.

    Se usa al desactivarlo y al pasarlo a otro rol: así otro docente puede tomar esos
    grados de inmediato. Las asignaciones de periodos terminados se conservan como
    historial. Devuelve el resumen de lo liberado (para la auditoría) o SIN_ASIGNACION.
    """
    no_terminados = list(
        db.exec(
            select(PeriodoAcademico)
            .join(
                DocenteColegioGrado,
                DocenteColegioGrado.id_periodo_academico == PeriodoAcademico.id_periodo_academico,
            )
            .where(
                DocenteColegioGrado.id_docente == id_docente,
                PeriodoAcademico.fecha_fin >= hoy_lima(),
            )
            .distinct()
            .order_by(PeriodoAcademico.fecha_inicio)
        ).all()
    )
    if not no_terminados:
        return SIN_ASIGNACION
    anio = db.get(AnioEscolar, no_terminados[0].id_anio_escolar)
    resumen = resumen_actual(db, id_docente, no_terminados, anio)
    db.execute(
        delete(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == id_docente,
            col(DocenteColegioGrado.id_periodo_academico).in_(
                [p.id_periodo_academico for p in no_terminados]
            ),
        )
    )
    return resumen


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def colegios_vigentes_por_docente(db: Session, ids_docente: list[int]) -> dict[int, list[str]]:
    """Nombres de los colegios de cada docente en el periodo vigente, en una sola consulta."""
    periodo = periodo_vigente(db)
    if periodo is None or not ids_docente:
        return {}
    filas = db.exec(
        select(DocenteColegioGrado.id_docente, Colegio.nombre)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            col(DocenteColegioGrado.id_docente).in_(ids_docente),
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
        )
        .distinct()
        .order_by(Colegio.nombre)
    ).all()
    por_docente: dict[int, list[str]] = defaultdict(list)
    for id_docente, nombre in filas:
        por_docente[id_docente].append(nombre)
    return por_docente


def detalle_asignacion(db: Session, id_docente: int) -> Optional[AsignacionDetalle]:
    """Asignación del docente para prellenar el formulario de edición.

    Los periodos del año escolar vigente si tiene asignaciones en él; si no, los del año
    más reciente en que tenga. None si nunca tuvo asignaciones.
    """
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico, Colegio, Grado)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .where(DocenteColegioGrado.id_docente == id_docente)
        .order_by(PeriodoAcademico.fecha_inicio, Grado.id_grado)
    ).all()
    if not filas:
        return None

    vigente = periodo_vigente(db)
    anios = {p.id_anio_escolar for _, p, _, _ in filas}
    if vigente is not None and vigente.id_anio_escolar in anios:
        id_anio = vigente.id_anio_escolar
    else:
        id_anio = filas[-1][1].id_anio_escolar  # el del periodo más reciente

    periodos: dict[int, PeriodoAsignado] = {}
    for fila, periodo, colegio, grado in filas:
        if periodo.id_anio_escolar != id_anio:
            continue
        item = periodos.setdefault(
            periodo.id_periodo_academico,
            PeriodoAsignado(
                id_periodo=periodo.id_periodo_academico,
                numero=periodo.numero,
                vigente=vigente is not None and vigente.id_periodo_academico == periodo.id_periodo_academico,
                id_colegio=fila.id_colegio,
                colegio=colegio.nombre,
                grados=[],
            ),
        )
        item.grados.append(GradoAsignado(id_grado=grado.id_grado, nombre=grado.nombre))
    return AsignacionDetalle(id_anio_escolar=id_anio, periodos=list(periodos.values()))


def listar_asignaciones(
    db: Session,
    id_docente: Optional[int] = None,
    id_periodo_academico: Optional[int] = None,
    id_colegio: Optional[int] = None,
) -> list[AsignacionItem]:
    vigente = periodo_vigente(db)
    consulta = (
        select(DocenteColegioGrado, Docente, Colegio, Grado, PeriodoAcademico, AnioEscolar)
        .join(Docente, Docente.id_docente == DocenteColegioGrado.id_docente)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
    )
    if id_docente is not None:
        consulta = consulta.where(DocenteColegioGrado.id_docente == id_docente)
    if id_periodo_academico is not None:
        consulta = consulta.where(DocenteColegioGrado.id_periodo_academico == id_periodo_academico)
    if id_colegio is not None:
        consulta = consulta.where(DocenteColegioGrado.id_colegio == id_colegio)
    consulta = consulta.order_by(
        PeriodoAcademico.fecha_inicio, Colegio.nombre, Grado.id_grado, Docente.apellidos
    )
    return [
        AsignacionItem(
            id=fila.id,
            id_docente=docente.id_docente,
            docente=f"{docente.nombres} {docente.apellidos}",
            id_colegio=colegio.id_colegio,
            colegio=colegio.nombre,
            id_grado=grado.id_grado,
            grado=grado.nombre,
            id_periodo_academico=periodo.id_periodo_academico,
            periodo=periodo.numero,
            id_anio_escolar=anio.id_anio_escolar,
            anio=anio.nombre,
            vigente=vigente is not None and vigente.id_periodo_academico == periodo.id_periodo_academico,
        )
        for fila, docente, colegio, grado, periodo, anio in db.exec(consulta).all()
    ]


def mis_asignaciones(db: Session, id_docente: int) -> list[MiAsignacion]:
    """Lo que el docente tiene a cargo en el periodo vigente, agrupado por colegio.

    Los subprogramas y ciclos se deducen de sus alumnos activos en esos colegio y grados
    (diseño v3: el docente atiende ambos subprogramas).
    """
    alcance = alcance_docente(db, id_docente)
    if not alcance:
        return []

    ids_colegio = {c for c, _ in alcance}
    ids_grado = {g for _, g in alcance}
    colegios = {c.id_colegio: c for c in db.exec(select(Colegio).where(col(Colegio.id_colegio).in_(ids_colegio))).all()}
    grados = {g.id_grado: g for g in db.exec(select(Grado).where(col(Grado.id_grado).in_(ids_grado))).all()}

    alumnos = db.exec(
        select(Alumno.id_colegio, Programa.nombre, CicloEbr.nombre, func.count(Alumno.id_alumno))
        .join(Programa, Programa.id_programa == Alumno.id_programa_actual)
        .join(Grado, Grado.id_grado == Alumno.id_grado)
        .join(CicloEbr, CicloEbr.id_ciclo == Grado.id_ciclo)
        .where(
            col(Alumno.activo).is_(True),
            or_(*(and_(Alumno.id_colegio == c, Alumno.id_grado == g) for c, g in alcance)),
        )
        .group_by(Alumno.id_colegio, Programa.nombre, CicloEbr.nombre)
    ).all()
    ciclos: dict[int, set[str]] = defaultdict(set)
    programas: dict[int, set[str]] = defaultdict(set)
    cantidad: dict[int, int] = defaultdict(int)
    for id_colegio, programa, ciclo_grado, total in alumnos:
        ciclos[id_colegio].add(calcular_ciclo(programa, ciclo_grado))
        programas[id_colegio].add(programa)
        cantidad[id_colegio] += total

    return [
        MiAsignacion(
            id_colegio=id_colegio,
            colegio=colegios[id_colegio].nombre,
            grados=[
                GradoAsignado(id_grado=g, nombre=grados[g].nombre)
                for g in sorted(g for c, g in alcance if c == id_colegio)
            ],
            ciclos=ordenar_ciclos(ciclos[id_colegio]),
            subprogramas=sorted(programas[id_colegio]),
            cantidad_alumnos=cantidad[id_colegio],
        )
        for id_colegio in sorted(ids_colegio, key=lambda c: colegios[c].nombre)
    ]
