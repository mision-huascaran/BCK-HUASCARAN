"""Asignaciones de los Docentes (CU016): qué colegios y grados atiende en cada periodo.

Reglas de negocio:
- Un docente puede atender varios colegios en un año escolar, cada uno con sus grados
  (por defecto, todos los que el colegio ofrece en `colegio_grado`). Cada asignación se
  guarda como una fila por colegio, grado y periodo en `docente_colegio_grado`.
- Un solo docente por (colegio, grado, periodo) (UNIQUE en la BD).
- Solo se crean o reemplazan filas en PA no terminados (su fin, en el calendario del
  colegio de la asignación, es hoy o después): los PA pasados son historial y nunca se
  tocan. "Vigente", "terminado" y "próximo" siempre se miden con el calendario de cada
  colegio (CU027): dos colegios pueden estar en periodos distintos el mismo día.
- El inicio se decide por colegio: si su año no empezó, desde el PA1; si está en un PA,
  desde ese PA; si está en un receso (PR n), el request elige `inicio_asignacion` para ese
  colegio: "retroactivo" crea además la fila del PA n recién culminado (`retroactiva`),
  que no puede sustituir a su titular; si el mismo docente ya era su titular, se conserva
  su fila y se marca `retroactiva`: la asignación ordinaria no habilita por sí sola la
  regularización durante el PR. "proximo" empieza en el PA n + 1 (no existe en el PR4).
- En una edición, la lista de colegios es el conjunto completo del año: los colegios que
  el docente deja se liberan en sus PA no terminados (como al inactivarlo).
- Primero se valida todo, para todos los colegios, y recién después se escribe: un
  conflicto en un colegio no deja escrito otro.

Las asignaciones se cambian con POST y PATCH /usuarios; aquí no se hace commit.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from fastapi import status
from sqlalchemy import and_, delete, func, or_
from sqlmodel import Session, col, select

from app.core.ciclo import calcular_ciclo, ordenar_ciclos
from app.core.errores import ErrorNegocio, error_de_campo
from app.core.texto import comparable, comparable_sql
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
from app.schemas.usuario import (
    AsignacionDetalle,
    ColegioAsignadoDetalle,
    ColegioDeAsignacion,
    GradoAsignado,
    PeriodoAsignado,
)
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
CAMPO_COLEGIOS = "asignacion.colegios"
MENSAJE_SIN_PROXIMO = (
    "Este año escolar ya no tiene otro periodo académico. Para asignar al Docente al "
    "siguiente año, selecciona ese año escolar."
)
MENSAJE_COLEGIO_REPETIDO = "El colegio ya está en la asignación: selecciónalo una sola vez."
ULTIMO_PA = 4


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def campo_del_colegio(indice: int, nombre: str) -> str:
    """Ruta del campo de un colegio de la asignación: `asignacion.colegios.1.id_colegio`."""
    return f"{CAMPO_COLEGIOS}.{indice}.{nombre}"


@dataclass(frozen=True)
class Asignacion:
    """Grados de un docente en un colegio a partir de un periodo de un año escolar.
    `retroactivo`: el PA recién culminado que se agrega con "Retroactivo" (también está
    primero en `periodos`)."""

    colegio: Colegio
    grados: list[Grado]
    anio: AnioEscolar
    periodos: list[PeriodoAcademico]
    retroactivo: Optional[PeriodoAcademico] = None

    def resumen(self) -> str:
        return resumen_asignacion(
            self.colegio.nombre, self.grados, self.periodos[0], self.anio, self.retroactivo is not None
        )

    def pares(self) -> set[tuple[int, int]]:
        """(colegio, periodo) que esta asignación reescribe."""
        return {(self.colegio.id_colegio, p.id_periodo_academico) for p in self.periodos}


@dataclass(frozen=True)
class PlanAsignacion:
    """Lo que queda del docente en un año escolar, ya validado: una `Asignacion` por colegio
    pedido y las filas de los colegios que deja (de PA no terminados), que se liberan."""

    anio: AnioEscolar
    colegios: list[Asignacion]
    liberadas: list[DocenteColegioGrado] = field(default_factory=list)

    def resumen(self) -> str:
        por_nombre = sorted(self.colegios, key=lambda a: (comparable(a.colegio.nombre), a.colegio.id_colegio))
        return unir_resumenes(a.resumen() for a in por_nombre)

    def pares(self) -> set[tuple[int, int]]:
        """(colegio, periodo) que el plan reescribe o libera: de ahí sale el "antes"."""
        liberados = {(f.id_colegio, f.id_periodo_academico) for f in self.liberadas}
        return liberados.union(*(a.pares() for a in self.colegios))


def resumen_asignacion(
    colegio: str, grados: list[Grado], desde: PeriodoAcademico, anio: AnioEscolar, retroactivo: bool = False
) -> str:
    """Texto legible para la auditoría: `AMASHCA [1.º, 2.º] desde P3 2026`, con
    ` (P3 retroactivo)` si ese primer PA está habilitado con "Retroactivo"."""
    nombres = ", ".join(g.nombre for g in sorted(grados, key=lambda g: g.id_grado))
    marca = f" (P{desde.numero} retroactivo)" if retroactivo else ""
    return f"{colegio} [{nombres}] desde P{desde.numero} {anio.nombre}{marca}"


def unir_resumenes(resumenes: Iterable[str]) -> str:
    """Los resúmenes de cada colegio, unidos por "; " (SIN_ASIGNACION si no hay ninguno)."""
    return "; ".join(resumenes) or SIN_ASIGNACION


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


def _inicio_en_receso(
    anio: AnioDelColegio, colegio: Colegio, numero: int, inicio: Optional[str], campo: str
) -> Inicio:
    """El colegio está en el PR `numero`: "retroactivo" agrega el PA culminado y sigue
    con el próximo; "proximo" empieza en el PA siguiente. Sin elección, 422."""
    if inicio is None:
        detalle = (
            f"El colegio {colegio.nombre} está en receso (PR{numero}): indica si la asignación es "
            f"retroactiva al PA{numero} o empieza en el próximo periodo."
        )
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detalle,
            motivo="inicio_asignacion_requerido",
            extra={
                "id_colegio": colegio.id_colegio, "colegio": colegio.nombre, "periodo": f"PR{numero}",
                "errores": [{"campo": campo, "mensaje": detalle}],
            },
        )
    siguiente = _pa_numero(anio, numero + 1)
    if inicio == RETROACTIVO:
        return Inicio(siguiente, retroactivo=_pa_numero(anio, numero))
    if numero >= ULTIMO_PA or siguiente is None:
        raise error_de_campo(campo, MENSAJE_SIN_PROXIMO)
    return Inicio(siguiente)


def inicio_de(anio: AnioDelColegio, colegio: Colegio, inicio: Optional[str], campo: str) -> Inicio:
    """Desde qué PA del año aplica la asignación de un colegio, según su calendario (CU016):
    si hoy el colegio está en un PA, ese; si está en un PR, lo que diga `inicio`
    ("retroactivo" o "proximo"); si hoy no cae en ese año (p. ej. todavía no empezó),
    desde el primer PA no terminado. `campo`: dónde se informa el error de `inicio`."""
    actual = anio.calendario.periodo_de(hoy_lima())
    if actual is None:
        return Inicio(None)
    if actual.es_pa:
        return Inicio(_pa_numero(anio, actual.numero))
    return _inicio_en_receso(anio, colegio, actual.numero, inicio, campo)


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


def _verificar_grados_libres(db: Session, id_docente: Optional[int], asignacion: Asignacion) -> None:
    """409 si alguno de esos grados ya lo tiene otro docente en alguno de esos periodos
    (incluido el retroactivo: no se sustituye al titular histórico)."""
    consulta = (
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
        )
    )
    if id_docente is not None:
        consulta = consulta.where(DocenteColegioGrado.id_docente != id_docente)
    ocupada = db.exec(consulta.order_by(PeriodoAcademico.numero, Grado.id_grado).limit(1)).first()
    if ocupada is not None:
        grado, numero = ocupada
        raise grado_asignado(asignacion.colegio.nombre, grado, numero, asignacion.anio.nombre)


# ── Planificación (solo lee) ────────────────────────────────────────────────────────

def planificar_asignacion(
    db: Session, id_anio_escolar: int, pedidos: list[ColegioDeAsignacion], id_docente: Optional[int] = None
) -> PlanAsignacion:
    """Valida la asignación completa de un año (alta, edición o paso a Docente) sin escribir
    nada: colegios, grados, año, inicio y grados libres de todos los colegios.

    `id_docente`: el docente que ya existe (None en un alta). Con él, los grados no
    enviados se toman de lo que ya tiene en ese colegio y los colegios que tiene en el año
    y no vienen en `pedidos` se liberan en sus PA no terminados.
    """
    _sin_colegios_repetidos(pedidos)
    renovacion = id_docente is not None and not _tiene_filas_en_el_anio(db, id_docente, id_anio_escolar)
    colegios = [
        _planificar_colegio(db, id_anio_escolar, indice, pedido, id_docente, renovacion)
        for indice, pedido in enumerate(pedidos)
    ]
    anio = colegios[0].anio
    liberadas = _filas_que_deja(db, id_docente, anio, {p.id_colegio for p in pedidos})
    return PlanAsignacion(anio, colegios, liberadas)


def _sin_colegios_repetidos(pedidos: list[ColegioDeAsignacion]) -> None:
    vistos: set[int] = set()
    for indice, pedido in enumerate(pedidos):
        if pedido.id_colegio in vistos:
            raise error_de_campo(campo_del_colegio(indice, "id_colegio"), MENSAJE_COLEGIO_REPETIDO)
        vistos.add(pedido.id_colegio)


def _planificar_colegio(
    db: Session,
    id_anio_escolar: int,
    indice: int,
    pedido: ColegioDeAsignacion,
    id_docente: Optional[int],
    renovacion: bool,
) -> Asignacion:
    """Valida un colegio de la asignación: inicio según su calendario, grados y que estén
    libres en esos periodos."""
    colegio = colegio_activo(db, pedido.id_colegio)
    anio = anio_del_colegio(db, id_anio_escolar, colegio.id_colegio)
    inicio = inicio_de(anio, colegio, pedido.inicio_asignacion, campo_del_colegio(indice, "inicio_asignacion"))
    # Una renovación parte de lo último que tuvo en el colegio; el mismo año, del periodo de inicio.
    referencia = None if renovacion else inicio.desde
    actuales = _grados_actuales(db, id_docente, colegio.id_colegio, referencia)
    grados = _grados_de(db, colegio, pedido.grados, actuales)
    asignacion = Asignacion(colegio, grados, anio.anio, periodos_asignables(anio, inicio), inicio.retroactivo)
    _verificar_grados_libres(db, id_docente, asignacion)
    return asignacion


def _tiene_filas_en_el_anio(db: Session, id_docente: int, id_anio_escolar: int) -> bool:
    return db.exec(
        select(DocenteColegioGrado.id)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(DocenteColegioGrado.id_docente == id_docente, PeriodoAcademico.id_anio_escolar == id_anio_escolar)
        .limit(1)
    ).first() is not None


def _grados_actuales(
    db: Session, id_docente: Optional[int], id_colegio: int, periodo: Optional[PeriodoAcademico]
) -> list[int]:
    """Grados del docente en ese colegio en `periodo` o, sin él, en el último periodo en
    que lo atendió. Vacío si no lo atendía."""
    if id_docente is None:
        return []
    consulta = (
        select(DocenteColegioGrado.id_grado, DocenteColegioGrado.id_periodo_academico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .where(DocenteColegioGrado.id_docente == id_docente, DocenteColegioGrado.id_colegio == id_colegio)
    )
    if periodo is not None:
        consulta = consulta.where(DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico)
    filas = db.exec(
        consulta.order_by(col(AnioEscolar.fecha_inicio).desc(), col(PeriodoAcademico.numero).desc())
    ).all()
    if not filas:
        return []
    ultimo = filas[0][1]
    return sorted(id_grado for id_grado, id_periodo in filas if id_periodo == ultimo)


def _grados_de(
    db: Session, colegio: Colegio, ids_grados: Optional[list[int]], actuales: list[int]
) -> list[Grado]:
    """Los grados enviados; sin ellos, los que ya tenía en el colegio o, si no lo atendía,
    todos los que ofrece."""
    if ids_grados is not None or not actuales:
        return resolver_grados(db, colegio, ids_grados)
    # Se conservan los grados actuales tal como están asignados.
    return list(db.exec(select(Grado).where(col(Grado.id_grado).in_(actuales)).order_by(Grado.id_grado)).all())


def _filas_que_deja(
    db: Session, id_docente: Optional[int], anio: AnioEscolar, conservados: set[int]
) -> list[DocenteColegioGrado]:
    """Filas del docente en ese año en colegios que no conserva, de PA no terminados en el
    calendario de cada uno (los culminados, incluido el retroactivo en su PR, son historial)."""
    if id_docente is None:
        return []
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(
            DocenteColegioGrado.id_docente == id_docente,
            PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar,
            col(DocenteColegioGrado.id_colegio).not_in(conservados),
        )
    ).all()
    return [fila for fila, _ in _de_pa_no_terminados(db, filas)]


def _de_pa_no_terminados(
    db: Session, filas: list[tuple[DocenteColegioGrado, PeriodoAcademico]]
) -> list[tuple[DocenteColegioGrado, PeriodoAcademico]]:
    """Las filas cuyo PA no terminó en el calendario de su colegio."""
    if not filas:
        return []
    calendarios = cargar_calendarios(db, ids_colegio={f.id_colegio for f, _ in filas})
    hoy = hoy_lima()
    return [(f, p) for f, p in filas if not calendarios.pa_terminado(f.id_colegio, p, hoy)]


# ── Escritura ───────────────────────────────────────────────────────────────────────

def aplicar_asignacion(
    db: Session, id_docente: int, plan: PlanAsignacion, id_actor: int, ahora: datetime
) -> None:
    """Escribe un plan ya validado: reemplaza cada colegio pedido y libera los que deja."""
    if plan.liberadas:
        _borrar(db, [f.id for f in plan.liberadas])
    for asignacion in plan.colegios:
        _reemplazar_en_colegio(db, id_docente, asignacion, id_actor, ahora)


def _borrar(db: Session, ids: list[int]) -> None:
    db.execute(delete(DocenteColegioGrado).where(col(DocenteColegioGrado.id).in_(ids)))


def _reemplazar_en_colegio(
    db: Session, id_docente: int, asignacion: Asignacion, id_actor: int, ahora: datetime
) -> None:
    """Deja al docente con esos grados del colegio en esos periodos; no toca sus filas de
    otros colegios.

    Solo borra las filas que sobran e inserta las que faltan: las que no cambian se
    conservan con su `creado_en`, que así indica desde cuándo el docente atiende ese
    colegio y grado (lo usa la alerta de inactividad, CU011). Son periodos vigentes o
    futuros, así que no se pierde historial; en el PA retroactivo (ya culminado) solo se
    agregan filas, marcadas `retroactiva`, o se marcan las que el docente ya tenía como
    titular (sin crear otra ni cambiar al titular).
    """
    id_retroactivo = asignacion.retroactivo.id_periodo_academico if asignacion.retroactivo else None
    deseadas = {(g.id_grado, p.id_periodo_academico) for p in asignacion.periodos for g in asignacion.grados}
    actuales = db.exec(
        select(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == id_docente,
            DocenteColegioGrado.id_colegio == asignacion.colegio.id_colegio,
            col(DocenteColegioGrado.id_periodo_academico).in_([p.id_periodo_academico for p in asignacion.periodos]),
        )
    ).all()
    conservadas = {(f.id_grado, f.id_periodo_academico) for f in actuales} & deseadas
    for fila in actuales:
        clave = (fila.id_grado, fila.id_periodo_academico)
        if clave in deseadas and fila.id_periodo_academico == id_retroactivo and not fila.retroactiva:
            _marcar_retroactiva(db, fila, id_actor, ahora)
    sobrantes = [
        f.id for f in actuales
        if (f.id_grado, f.id_periodo_academico) not in deseadas and f.id_periodo_academico != id_retroactivo
    ]
    if sobrantes:
        _borrar(db, sobrantes)
    db.add_all(
        DocenteColegioGrado(
            id_docente=id_docente,
            id_colegio=asignacion.colegio.id_colegio,
            id_grado=id_grado,
            id_periodo_academico=id_periodo,
            retroactiva=id_periodo == id_retroactivo,
            creado_por=id_actor,
            creado_en=ahora,
        )
        for id_grado, id_periodo in sorted(deseadas - conservadas)
    )


def _marcar_retroactiva(db: Session, fila: DocenteColegioGrado, id_actor: int, ahora: datetime) -> None:
    fila.retroactiva = True
    fila.modificado_por = id_actor
    fila.modificado_en = ahora
    db.add(fila)


def resumen_actual(db: Session, id_docente: Optional[int], pares: set[tuple[int, int]], anio: AnioEscolar) -> str:
    """Resumen de lo que el docente tiene hoy en esos (colegio, periodo) de un año: el
    "antes" de un cambio. Por colegio, sus grados en el primero de esos periodos en que lo
    atiende, unidos por "; " y en orden de nombre."""
    if id_docente is None or not pares:
        return SIN_ASIGNACION
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico, Colegio, Grado)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .where(
            DocenteColegioGrado.id_docente == id_docente,
            col(DocenteColegioGrado.id_periodo_academico).in_({p for _, p in pares}),
        )
        .order_by(comparable_sql(Colegio.nombre), Colegio.id_colegio, PeriodoAcademico.numero, Grado.id_grado)
    ).all()
    primeros: dict[int, _PrimerPeriodo] = {}
    for fila, periodo, colegio, grado in filas:
        if (fila.id_colegio, periodo.id_periodo_academico) in pares:
            primeros.setdefault(fila.id_colegio, _PrimerPeriodo(colegio, periodo)).sumar(fila, periodo, grado)
    return unir_resumenes(p.resumen(anio) for p in primeros.values())


@dataclass
class _PrimerPeriodo:
    """Grados de un colegio en el primer periodo en que el docente lo atiende."""

    colegio: Colegio
    periodo: PeriodoAcademico
    grados: list[Grado] = field(default_factory=list)
    retroactiva: bool = False

    def sumar(self, fila: DocenteColegioGrado, periodo: PeriodoAcademico, grado: Grado) -> None:
        if periodo.id_periodo_academico == self.periodo.id_periodo_academico:
            self.grados.append(grado)
            self.retroactiva = self.retroactiva or fila.retroactiva

    def resumen(self, anio: AnioEscolar) -> str:
        return resumen_asignacion(self.colegio.nombre, self.grados, self.periodo, anio, self.retroactiva)


def liberar_asignaciones(db: Session, id_docente: int) -> str:
    """Quita al docente de sus grados desde el PA vigente de cada colegio (incluido) en
    adelante.

    Se usa al desactivarlo y al pasarlo a otro rol: así otro docente puede tomar esos
    grados de inmediato. Las asignaciones de PA terminados (en el calendario de su
    colegio) se conservan como historial. Devuelve el resumen de lo liberado en el primer
    año afectado (para la auditoría) o SIN_ASIGNACION.
    """
    filas = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .where(DocenteColegioGrado.id_docente == id_docente)
        .order_by(AnioEscolar.fecha_inicio, PeriodoAcademico.numero)
    ).all()
    liberables = _de_pa_no_terminados(db, list(filas))
    if not liberables:
        return SIN_ASIGNACION
    anio = db.get(AnioEscolar, liberables[0][1].id_anio_escolar)
    pares = {(f.id_colegio, p.id_periodo_academico) for f, p in liberables if p.id_anio_escolar == anio.id_anio_escolar}
    resumen = resumen_actual(db, id_docente, pares, anio)
    _borrar(db, [f.id for f, _ in liberables])
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
    ).all()
    # Se ordena aquí: con DISTINCT, PostgreSQL no admite ordenar por una expresión fuera del SELECT.
    por_docente: dict[int, list[ItemCatalogo]] = defaultdict(list)
    for id_docente, id_colegio, nombre in sorted(filas, key=lambda f: (comparable(f[2]), f[1])):
        por_docente[id_docente].append(ItemCatalogo(id=id_colegio, nombre=nombre))
    return por_docente


def detalle_asignacion(db: Session, id_docente: int) -> Optional[AsignacionDetalle]:
    """Asignación del docente para prellenar el formulario de edición, con un bloque por
    colegio (por nombre) y sus periodos (por número).

    Los periodos del año escolar de referencia si tiene asignaciones en él; si no, los
    del año más reciente en que tenga. None si nunca tuvo asignaciones. `vigente` marca
    el PA en que hoy está cada colegio y `retroactiva`, el PA habilitado con "Retroactivo".
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
        .order_by(
            AnioEscolar.fecha_inicio, comparable_sql(Colegio.nombre), Colegio.id_colegio,
            PeriodoAcademico.numero, Grado.id_grado,
        )
    ).all()
    if not filas:
        return None
    id_anio = _anio_del_detalle(db, filas)
    return AsignacionDetalle(
        id_anio_escolar=id_anio,
        colegios=_por_colegio([f for f in filas if f[1].id_anio_escolar == id_anio], pa_actual_de_colegios(db)),
    )


def _anio_del_detalle(db: Session, filas: list) -> int:
    """El año de referencia si el docente tiene asignaciones en él; si no, el más reciente."""
    referencia = anio_de_referencia(db)
    if referencia is not None and referencia.id_anio_escolar in {p.id_anio_escolar for _, p, _, _ in filas}:
        return referencia.id_anio_escolar
    return filas[-1][1].id_anio_escolar  # las filas vienen por fecha de inicio del año


def _por_colegio(filas: list, pa_actual: dict[int, PeriodoCalendario]) -> list[ColegioAsignadoDetalle]:
    """Agrupa las filas (ya ordenadas) por colegio y periodo."""
    colegios: dict[int, ColegioAsignadoDetalle] = {}
    periodos: dict[tuple[int, int], PeriodoAsignado] = {}
    for fila, periodo, colegio, grado in filas:
        bloque = colegios.setdefault(
            colegio.id_colegio, ColegioAsignadoDetalle(id_colegio=colegio.id_colegio, colegio=colegio.nombre, periodos=[])
        )
        clave = (colegio.id_colegio, periodo.id_periodo_academico)
        if clave not in periodos:
            periodos[clave] = PeriodoAsignado(
                id_periodo=periodo.id_periodo_academico,
                numero=periodo.numero,
                vigente=_es_pa_actual(pa_actual, colegio.id_colegio, periodo.id_periodo_academico),
                retroactiva=False,
                grados=[],
            )
            bloque.periodos.append(periodos[clave])
        item = periodos[clave]
        item.retroactiva = item.retroactiva or fila.retroactiva
        item.grados.append(GradoAsignado(id_grado=grado.id_grado, nombre=grado.nombre))
    return list(colegios.values())


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
        AnioEscolar.fecha_inicio, PeriodoAcademico.numero, comparable_sql(Colegio.nombre), Colegio.id_colegio,
        Grado.id_grado, comparable_sql(Docente.apellidos), comparable_sql(Docente.nombres), DocenteColegioGrado.id,
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
        for id_colegio in sorted(ids_colegio, key=lambda c: (comparable(colegios[c].nombre), c))
    ]
    return asignaciones, TotalesACargo(**total.campos())


def mis_asignaciones(db: Session, usuario: Usuario) -> list[MiAsignacion]:
    """Lo que el docente tiene a cargo hoy, agrupado por colegio."""
    return a_cargo(db, usuario)[0]
