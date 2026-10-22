"""Asignaciones de los Docentes (CU016): qué colegio y grados atiende en cada periodo.

Reglas de negocio:
- Un docente trabaja en un solo colegio y, por ahora, enseña todos los grados que ese
  colegio ofrece (`colegio_grado`). Cada asignación se guarda como una fila por grado y
  periodo en `docente_colegio_grado`, para que en el futuro pueda enseñar solo algunos.
- Un solo docente por (colegio, grado, periodo) (UNIQUE en la BD).
- Solo se crean o reemplazan filas en PA no terminados (su fin, en el calendario del
  colegio de la asignación, es hoy o después): los PA pasados son historial y nunca se
  tocan. "Vigente", "terminado" y "próximo" siempre se miden con el calendario del colegio
  (CU027): dos colegios pueden estar en periodos distintos el mismo día.
- Si el colegio está hoy en un receso (PR n), el request elige `inicio_asignacion`:
  "retroactivo" crea además la fila del PA n recién culminado (`retroactiva`), que no
  puede sustituir a su titular; "proximo" empieza en el PA n + 1 (no existe en el PR4).

Las asignaciones se cambian con POST y PATCH /usuarios; aquí no se hace commit.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fastapi import status
from sqlalchemy import and_, delete, func, or_
from sqlmodel import Session, col, select

from app.core.ciclo import calcular_ciclo, ordenar_ciclos
from app.core.errores import ErrorNegocio, error_de_campo
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
    Seccion,
    Usuario,
)
from app.schemas.asignacion import AsignacionItem, GradoACargo, MiAsignacion, TotalesACargo
from app.schemas.comun import ItemCatalogo
from app.schemas.usuario import AsignacionDetalle, GradoAsignado, PeriodoAsignado
from app.services.habilitacion import pares_a_cargo
from app.services.calendario import (
    CalendarioResuelto,
    PeriodoCalendario,
    anio_de_referencia,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
    periodos_academicos_del_anio,
)

SIN_ASIGNACION = "Sin asignación"
RETROACTIVO = "retroactivo"
PROXIMO = "proximo"
CAMPO_INICIO = "asignacion.inicio_asignacion"
MENSAJE_SIN_PROXIMO = (
    "Este año escolar ya no tiene otro periodo académico. Para asignar al Docente al "
    "siguiente año, selecciona ese año escolar."
)
MENSAJE_INICIO_AMBIGUO = (
    "Indica el periodo desde el que aplica el cambio o cómo empieza la asignación en el "
    "receso, pero no ambos."
)
ULTIMO_PA = 4


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


@dataclass(frozen=True)
class Asignacion:
    """Colegio y grados de un docente a partir de un periodo de un año escolar.
    `retroactivo`: el PA recién culminado que se agrega con "Retroactivo" (también está
    primero en `periodos`)."""

    colegio: Colegio
    grados: list[Grado]
    anio: AnioEscolar
    periodos: list[PeriodoAcademico]
    retroactivo: Optional[PeriodoAcademico] = None

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


@dataclass(frozen=True)
class AnioDelColegio:
    """Un año escolar con sus PA y el calendario que le aplica a un colegio."""

    anio: AnioEscolar
    periodos: list[PeriodoAcademico]
    calendario: CalendarioResuelto

    def no_terminado(self, periodo: PeriodoAcademico) -> bool:
        """Vigente o futuro en el calendario del colegio: su último día es hoy o después."""
        pa = self.calendario.pa(periodo.numero)
        return pa is not None and pa.fecha_fin >= hoy_lima()


def anio_del_colegio(db: Session, id_anio_escolar: int, id_colegio: int) -> AnioDelColegio:
    """El año, sus PA y el calendario del colegio. 422 si el año no existe, no tiene PA o
    no tiene calendario configurado (CU027)."""
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise _no_procesable("No existe el año escolar indicado.", "anio_inexistente")
    periodos = periodos_academicos_del_anio(db, id_anio_escolar)
    if not periodos:
        raise _no_procesable("El año escolar no tiene periodos cargados.", "anio_sin_periodos")
    calendario = cargar_calendarios(db, id_anio_escolar, ids_colegio=[id_colegio]).aplicable(
        id_colegio, id_anio_escolar
    )
    if calendario is None:
        raise _no_procesable(
            "El año escolar no tiene un calendario académico configurado: revisa el calendario "
            "antes de guardar la asignación.",
            "calendario_no_configurado",
        )
    return AnioDelColegio(anio, periodos, calendario)


@dataclass(frozen=True)
class Inicio:
    """Desde qué PA aplica una asignación (None = desde el primer PA no terminado del año)
    y, con "Retroactivo", el PA recién culminado que se agrega."""

    desde: Optional[PeriodoAcademico]
    retroactivo: Optional[PeriodoAcademico] = None


def _pa_numero(anio: AnioDelColegio, numero: int) -> Optional[PeriodoAcademico]:
    return next((p for p in anio.periodos if p.numero == numero), None)


def _inicio_en_receso(anio: AnioDelColegio, colegio: Colegio, numero: int, inicio: Optional[str]) -> Inicio:
    """El colegio está en el PR `numero`: "retroactivo" agrega el PA culminado y sigue
    con el próximo; "proximo" empieza en el PA siguiente. Sin elección, 422."""
    if inicio is None:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"El colegio {colegio.nombre} está en receso (PR{numero}): indica si la asignación es "
            f"retroactiva al PA{numero} o empieza en el próximo periodo.",
            motivo="inicio_asignacion_requerido",
            extra={"id_colegio": colegio.id_colegio, "colegio": colegio.nombre, "periodo": f"PR{numero}"},
        )
    siguiente = _pa_numero(anio, numero + 1)
    if inicio == RETROACTIVO:
        return Inicio(siguiente, retroactivo=_pa_numero(anio, numero))
    if numero >= ULTIMO_PA or siguiente is None:
        raise error_de_campo(CAMPO_INICIO, MENSAJE_SIN_PROXIMO)
    return Inicio(siguiente)


def inicio_de(
    anio: AnioDelColegio, colegio: Colegio, id_periodo: Optional[int], inicio: Optional[str] = None
) -> Inicio:
    """Desde qué PA del año aplica una asignación, según el calendario del colegio (CU016).

    Con `id_periodo`, ese (del año y no terminado); con él y `inicio` a la vez, 422: no
    hay prioridad silenciosa. Sin él: si hoy el colegio está en un
    PA, ese; si está en un PR, lo que diga `inicio` ("retroactivo" o "proximo"); si hoy
    no cae en ese año (p. ej. todavía no empezó), desde el primer PA no terminado.
    """
    if id_periodo is not None and inicio is not None:
        raise _no_procesable(MENSAJE_INICIO_AMBIGUO, "inicio_asignacion_ambiguo")
    if id_periodo is not None:
        return Inicio(periodo_de_inicio(anio, id_periodo))
    actual = anio.calendario.periodo_de(hoy_lima())
    if actual is None:
        return Inicio(None)
    if actual.es_pa:
        return Inicio(_pa_numero(anio, actual.numero))
    return _inicio_en_receso(anio, colegio, actual.numero, inicio)


def periodo_de_inicio(anio: AnioDelColegio, id_periodo: int) -> PeriodoAcademico:
    """El PA `id_periodo`, que debe ser del año y no estar terminado (en el calendario del
    colegio)."""
    elegido = next((p for p in anio.periodos if p.id_periodo_academico == id_periodo), None)
    if elegido is None:
        raise _no_procesable(
            "El periodo indicado no pertenece al año escolar de la asignación.",
            "periodo_fuera_del_anio",
        )
    if not anio.no_terminado(elegido):
        raise _no_procesable("El periodo indicado ya terminó.", "periodo_terminado")
    return elegido


def periodos_asignables(anio: AnioDelColegio, inicio: Optional[Inicio] = None) -> list[PeriodoAcademico]:
    """PA no terminados del año (en el calendario del colegio), desde `inicio.desde` en
    adelante, precedidos por el PA retroactivo si lo hay. 422 si no queda ninguno."""
    inicio = inicio or Inicio(None)
    desde = inicio.desde
    asignables = [
        p for p in anio.periodos
        if anio.no_terminado(p) and (desde is None or p.numero >= desde.numero)
    ]
    if inicio.retroactivo is not None:
        asignables.insert(0, inicio.retroactivo)
    if not asignables:
        raise _no_procesable(
            "El año escolar no tiene periodos vigentes ni futuros para asignar.",
            "sin_periodos_asignables",
        )
    return asignables


# ── Escritura ───────────────────────────────────────────────────────────────────────

def grado_asignado(
    colegio: Optional[str] = None,
    grado: Optional[str] = None,
    periodo: Optional[int] = None,
    anio: Optional[str] = None,
) -> ErrorNegocio:
    """409 de aula ocupada (CU016), con el texto exacto del caso de uso. Sin datos (la
    traducción del UNIQUE de `docente_colegio_grado` ante dos asignaciones simultáneas),
    un texto general."""
    if colegio is None:
        detalle = "No se puede guardar la asignación. Uno de los grados ya tiene un Docente asignado en ese periodo."
    else:
        detalle = (
            f"No se puede guardar la asignación. El colegio {colegio}, grado {grado}, ya tiene un "
            f"Docente asignado en PA{periodo} del año escolar {anio}."
        )
    return ErrorNegocio(status.HTTP_409_CONFLICT, detalle, motivo="grado_asignado")


def _verificar_grados_libres(db: Session, id_docente: int, asignacion: Asignacion) -> None:
    """409 si alguno de esos grados ya lo tiene otro docente en alguno de esos periodos
    (incluido el retroactivo: no se sustituye al titular histórico). Todo o nada."""
    ocupada = db.exec(
        select(Grado.nombre, PeriodoAcademico.numero)
        .select_from(DocenteColegioGrado)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(
            DocenteColegioGrado.id_colegio == asignacion.colegio.id_colegio,
            col(DocenteColegioGrado.id_grado).in_([g.id_grado for g in asignacion.grados]),
            col(DocenteColegioGrado.id_periodo_academico).in_(
                [p.id_periodo_academico for p in asignacion.periodos]
            ),
            DocenteColegioGrado.id_docente != id_docente,
        )
        .order_by(PeriodoAcademico.numero, Grado.id_grado)
        .limit(1)
    ).first()
    if ocupada is not None:
        grado, numero = ocupada
        raise grado_asignado(asignacion.colegio.nombre, grado, numero, asignacion.anio.nombre)


def reemplazar_asignaciones(
    db: Session, id_docente: int, asignacion: Asignacion, id_actor: int, ahora: datetime
) -> None:
    """Deja al docente con ese colegio y grados en esos periodos (y solo en esos).

    Solo borra las filas que sobran e inserta las que faltan: las que no cambian se
    conservan con su `creado_en`, que así indica desde cuándo el docente atiende ese
    colegio y grado (lo usa la alerta de inactividad, CU011). Son periodos vigentes o
    futuros, así que no se pierde historial; en el PA retroactivo (ya culminado) solo se
    agregan filas, marcadas `retroactiva`.
    """
    _verificar_grados_libres(db, id_docente, asignacion)
    ids_periodo = [p.id_periodo_academico for p in asignacion.periodos]
    id_retroactivo = asignacion.retroactivo.id_periodo_academico if asignacion.retroactivo else None
    deseadas = {
        (asignacion.colegio.id_colegio, grado.id_grado, id_periodo)
        for id_periodo in ids_periodo
        for grado in asignacion.grados
    }
    actuales = db.exec(
        select(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == id_docente,
            col(DocenteColegioGrado.id_periodo_academico).in_(ids_periodo),
        )
    ).all()
    conservadas = set()
    sobrantes = []
    for fila in actuales:
        clave = (fila.id_colegio, fila.id_grado, fila.id_periodo_academico)
        if clave in deseadas:
            conservadas.add(clave)
        elif fila.id_periodo_academico != id_retroactivo:
            sobrantes.append(fila.id)
    if sobrantes:
        db.execute(delete(DocenteColegioGrado).where(col(DocenteColegioGrado.id).in_(sobrantes)))
    db.add_all(
        DocenteColegioGrado(
            id_docente=id_docente,
            id_colegio=id_colegio,
            id_grado=id_grado,
            id_periodo_academico=id_periodo,
            retroactiva=id_periodo == id_retroactivo,
            creado_por=id_actor,
            creado_en=ahora,
        )
        for id_colegio, id_grado, id_periodo in sorted(deseadas - conservadas)
    )


def planificar_asignacion_nueva(
    db: Session,
    id_colegio: int,
    id_anio_escolar: int,
    ids_grados: Optional[list[int]],
    desde_periodo: Optional[int] = None,
    inicio_asignacion: Optional[str] = None,
) -> Asignacion:
    """Asignación de un docente que no tiene una previa (alta o paso a Docente)."""
    colegio = colegio_activo(db, id_colegio)
    grados = resolver_grados(db, colegio, ids_grados)
    anio = anio_del_colegio(db, id_anio_escolar, colegio.id_colegio)
    return _armar(colegio, grados, anio, inicio_de(anio, colegio, desde_periodo, inicio_asignacion))


def _armar(colegio: Colegio, grados: list[Grado], anio: AnioDelColegio, inicio: Inicio) -> Asignacion:
    return Asignacion(colegio, grados, anio.anio, periodos_asignables(anio, inicio), inicio.retroactivo)


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
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .where(DocenteColegioGrado.id_docente == id_docente)
    )
    if periodo is not None:
        consulta = consulta.where(
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico
        )
    filas = db.exec(
        consulta.order_by(col(AnioEscolar.fecha_inicio).desc(), col(PeriodoAcademico.numero).desc())
    ).all()
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
    inicio_asignacion: Optional[str] = None,
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
        return planificar_asignacion_nueva(
            db, id_colegio, id_anio_escolar, ids_grados, desde_periodo, inicio_asignacion
        )

    id_anio = id_anio_escolar if id_anio_escolar is not None else ultimo.periodo.id_anio_escolar
    # "Desde cuándo" se mide con el calendario del colegio de destino (el nuevo si cambia).
    destino = colegio_activo(db, id_colegio) if id_colegio is not None else db.get(Colegio, ultimo.id_colegio)
    anio = anio_del_colegio(db, id_anio, destino.id_colegio)
    inicio = inicio_de(anio, destino, desde_periodo, inicio_asignacion)
    renovacion = id_anio != ultimo.periodo.id_anio_escolar
    base = _base_del_cambio(db, id_docente, ultimo, inicio, renovacion)
    colegio = colegio_activo(db, id_colegio if id_colegio is not None else base.id_colegio)
    grados = _grados_del_cambio(db, colegio, ids_grados, base)
    return _armar(colegio, grados, anio, inicio)


def _base_del_cambio(
    db: Session, id_docente: int, ultimo: EstadoActual, inicio: Inicio, renovacion: bool
) -> EstadoActual:
    """Qué asignación toma el cambio como punto de partida: en una renovación, su último
    periodo asignado; en el mismo año, lo que tiene en el periodo desde el que aplica."""
    if renovacion or inicio.desde is None:
        return ultimo
    return estado_en(db, id_docente, inicio.desde) or ultimo


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
    """Quita al docente de sus grados desde el PA vigente de su colegio (incluido) en
    adelante.

    Se usa al desactivarlo y al pasarlo a otro rol: así otro docente puede tomar esos
    grados de inmediato. Las asignaciones de PA terminados (en el calendario de su
    colegio) se conservan como historial. Devuelve el resumen de lo liberado (para la
    auditoría) o SIN_ASIGNACION.
    """
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .where(DocenteColegioGrado.id_docente == id_docente)
        .order_by(AnioEscolar.fecha_inicio, PeriodoAcademico.numero)
    ).all()
    calendarios = cargar_calendarios(db, ids_colegio={f.id_colegio for f, _ in filas})
    hoy = hoy_lima()
    liberables = [(f, p) for f, p in filas if not calendarios.pa_terminado(f.id_colegio, p, hoy)]
    if not liberables:
        return SIN_ASIGNACION
    no_terminados = list({p.id_periodo_academico: p for _, p in liberables}.values())
    anio = db.get(AnioEscolar, no_terminados[0].id_anio_escolar)
    resumen = resumen_actual(db, id_docente, no_terminados, anio)
    db.execute(delete(DocenteColegioGrado).where(col(DocenteColegioGrado.id).in_([f.id for f, _ in liberables])))
    return resumen


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def colegios_vigentes_por_docente(db: Session, ids_docente: list[int]) -> dict[int, list[ItemCatalogo]]:
    """Colegios de cada docente con asignación del PA en que hoy está ese colegio, sin
    repetir y por nombre, en una sola consulta (más los calendarios)."""
    if not ids_docente:
        return {}
    pa_actual = pa_actual_de_colegios(db)
    filas = db.exec(
        select(DocenteColegioGrado.id_docente, Colegio.id_colegio, Colegio.nombre)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            col(DocenteColegioGrado.id_docente).in_(ids_docente),
            en_pa_de_su_colegio(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_periodo_academico, pa_actual),
        )
        .distinct()
        .order_by(Colegio.nombre, Colegio.id_colegio)
    ).all()
    por_docente: dict[int, list[ItemCatalogo]] = defaultdict(list)
    for id_docente, id_colegio, nombre in filas:
        por_docente[id_docente].append(ItemCatalogo(id=id_colegio, nombre=nombre))
    return por_docente


def detalle_asignacion(db: Session, id_docente: int) -> Optional[AsignacionDetalle]:
    """Asignación del docente para prellenar el formulario de edición.

    Los periodos del año escolar de referencia si tiene asignaciones en él; si no, los
    del año más reciente en que tenga. None si nunca tuvo asignaciones. `vigente` marca
    el PA en que hoy está el colegio de la asignación.
    """
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico, Colegio, Grado)
        .join(
            PeriodoAcademico,
            PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico,
        )
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .where(DocenteColegioGrado.id_docente == id_docente)
        .order_by(AnioEscolar.fecha_inicio, PeriodoAcademico.numero, Grado.id_grado)
    ).all()
    if not filas:
        return None

    referencia = anio_de_referencia(db)
    anios = {p.id_anio_escolar for _, p, _, _ in filas}
    if referencia is not None and referencia.id_anio_escolar in anios:
        id_anio = referencia.id_anio_escolar
    else:
        id_anio = filas[-1][1].id_anio_escolar  # el del periodo más reciente
    pa_actual = pa_actual_de_colegios(db)

    periodos: dict[int, PeriodoAsignado] = {}
    for fila, periodo, colegio, grado in filas:
        if periodo.id_anio_escolar != id_anio:
            continue
        item = periodos.setdefault(
            periodo.id_periodo_academico,
            PeriodoAsignado(
                id_periodo=periodo.id_periodo_academico,
                numero=periodo.numero,
                vigente=_es_pa_actual(pa_actual, fila.id_colegio, periodo.id_periodo_academico),
                id_colegio=fila.id_colegio,
                colegio=colegio.nombre,
                grados=[],
            ),
        )
        item.grados.append(GradoAsignado(id_grado=grado.id_grado, nombre=grado.nombre))
    return AsignacionDetalle(id_anio_escolar=id_anio, periodos=list(periodos.values()))


def _es_pa_actual(pa_actual: dict[int, PeriodoCalendario], id_colegio: int, id_periodo: int) -> bool:
    """Si el periodo es el PA en que hoy está ese colegio."""
    pa = pa_actual.get(id_colegio)
    return pa is not None and pa.id_periodo_academico == id_periodo


def listar_asignaciones(
    db: Session,
    id_docente: Optional[int] = None,
    id_periodo_academico: Optional[int] = None,
    id_colegio: Optional[int] = None,
) -> list[AsignacionItem]:
    """Asignaciones con filtros. `vigente`: del PA en que hoy está su colegio."""
    pa_actual = pa_actual_de_colegios(db)
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
        AnioEscolar.fecha_inicio, PeriodoAcademico.numero, Colegio.nombre, Grado.id_grado, Docente.apellidos
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
            vigente=_es_pa_actual(pa_actual, colegio.id_colegio, periodo.id_periodo_academico),
        )
        for fila, docente, colegio, grado, periodo, anio in db.exec(consulta).all()
    ]


@dataclass
class _Alumnado:
    """Acumula alumnos activos, ciclos y subprogramas (id, nombre) de un grupo de asignaciones."""

    ciclos: set[str] = field(default_factory=set)
    subprogramas: set[tuple[int, str]] = field(default_factory=set)
    cantidad: int = 0

    def sumar(self, ciclo: str, subprograma: tuple[int, str], cantidad: int) -> None:
        self.ciclos.add(ciclo)
        self.subprogramas.add(subprograma)
        self.cantidad += cantidad

    def campos(self) -> dict:
        return {
            "ciclos": ordenar_ciclos(self.ciclos),
            "subprogramas": sorted({nombre for _, nombre in self.subprogramas}),
            "cantidad_alumnos": self.cantidad,
        }

    def campos_de_grado(self) -> dict:
        """Como `campos`, con los subprogramas como `{id, nombre}`."""
        subprogramas = sorted(self.subprogramas, key=lambda s: s[1])
        return {**self.campos(), "subprogramas": [ItemCatalogo(id=i, nombre=n) for i, n in subprogramas]}


def _secciones_activas(db: Session, alcance: set[tuple[int, int]]) -> dict[tuple[int, int], list[ItemCatalogo]]:
    """{(colegio, grado): secciones activas}, en el orden en que se crearon."""
    filas = db.exec(
        select(Seccion)
        .where(col(Seccion.activo).is_(True), or_(*(and_(Seccion.id_colegio == c, Seccion.id_grado == g) for c, g in alcance)))
        .order_by(Seccion.id_seccion)
    ).all()
    por_grupo: dict[tuple[int, int], list[ItemCatalogo]] = defaultdict(list)
    for seccion in filas:
        por_grupo[(seccion.id_colegio, seccion.id_grado)].append(ItemCatalogo(id=seccion.id_seccion, nombre=seccion.nombre))
    return por_grupo


def _alumnado(db: Session, alcance: set[tuple[int, int]]) -> tuple[_Alumnado, dict, dict]:
    """Alumnos activos de esos colegio y grados: en total, por colegio y por (colegio, grado)."""
    alumnos = db.exec(
        select(
            Alumno.id_colegio,
            Alumno.id_grado,
            Programa.id_programa,
            Programa.nombre,
            CicloEbr.nombre,
            func.count(Alumno.id_alumno),
        )
        .join(Programa, Programa.id_programa == Alumno.id_programa_actual)
        .join(Grado, Grado.id_grado == Alumno.id_grado)
        .join(CicloEbr, CicloEbr.id_ciclo == Grado.id_ciclo)
        .where(
            col(Alumno.activo).is_(True),
            or_(*(and_(Alumno.id_colegio == c, Alumno.id_grado == g) for c, g in alcance)),
        )
        .group_by(Alumno.id_colegio, Alumno.id_grado, Programa.id_programa, Programa.nombre, CicloEbr.nombre)
    ).all()
    total = _Alumnado()
    por_colegio: dict[int, _Alumnado] = defaultdict(_Alumnado)
    por_grado: dict[tuple[int, int], _Alumnado] = defaultdict(_Alumnado)
    for id_colegio, id_grado, id_programa, programa, ciclo_grado, cantidad in alumnos:
        ciclo = calcular_ciclo(programa, ciclo_grado)
        for grupo in (total, por_colegio[id_colegio], por_grado[(id_colegio, id_grado)]):
            grupo.sumar(ciclo, (id_programa, programa), cantidad)
    return total, por_colegio, por_grado


def a_cargo(db: Session, usuario: Usuario) -> tuple[list[MiAsignacion], TotalesACargo]:
    """Lo que el docente tiene a cargo hoy (CU010): por colegio, por asignación (colegio +
    grado) y en total. Cuentan sus asignaciones habilitadas: las del PA vigente de cada
    colegio y las retroactivas durante su PR; no los grupos que solo ve por un permiso.

    Los subprogramas y ciclos se deducen de sus alumnos activos en esos colegio y grados
    (diseño v3: el docente atiende ambos subprogramas). Cuatro consultas más el alcance.
    """
    alcance = pares_a_cargo(db, usuario)
    if not alcance:
        return [], TotalesACargo(**_Alumnado().campos())

    ids_colegio = {c for c, _ in alcance}
    ids_grado = {g for _, g in alcance}
    colegios = {c.id_colegio: c for c in db.exec(select(Colegio).where(col(Colegio.id_colegio).in_(ids_colegio))).all()}
    grados = {g.id_grado: g for g in db.exec(select(Grado).where(col(Grado.id_grado).in_(ids_grado))).all()}
    total, por_colegio, por_grado = _alumnado(db, alcance)
    secciones = _secciones_activas(db, alcance)

    asignaciones = [
        MiAsignacion(
            id_colegio=id_colegio,
            colegio=colegios[id_colegio].nombre,
            grados=[
                GradoACargo(
                    id_grado=g,
                    nombre=grados[g].nombre,
                    secciones=secciones[(id_colegio, g)],
                    **por_grado[(id_colegio, g)].campos_de_grado(),
                )
                for g in sorted(g for c, g in alcance if c == id_colegio)
            ],
            **por_colegio[id_colegio].campos(),
        )
        for id_colegio in sorted(ids_colegio, key=lambda c: colegios[c].nombre)
    ]
    return asignaciones, TotalesACargo(**total.campos())


def mis_asignaciones(db: Session, usuario: Usuario) -> list[MiAsignacion]:
    """Lo que el docente tiene a cargo hoy, agrupado por colegio."""
    return a_cargo(db, usuario)[0]
