"""Gestión del calendario académico (CU027): consultar, crear el general, editar, fijar
fechas particulares de un colegio y volver al general.

Reglas (todas validadas aquí, además de lo que impone la BD):
- Los 8 periodos son obligatorios, en orden PA1 < PR1 < ... < PA4 < PR4, sin
  superponerse y con inicio <= fin (fechas inclusivas).
- El PR4 no puede chocar con el PA1 del año escolar siguiente del mismo colegio (ni el
  PA1 con el PR4 del año anterior).
- Días no lectivos: motivo no vacío, dentro de un PA y sin superponerse entre sí.
- Ningún cambio puede dejar una fecha con asistencias como no lectiva, fuera de su PA o
  en otro PA (409 `calendario_incompatible`). En el general se evalúan todos los colegios
  que lo usan; en un particular, solo su colegio. Todo o nada.
- Concurrencia: cada edición trae la `version` que se leyó; si cambió, 409
  `modificacion_concurrente`, sin combinar propuestas.
- Auditoría en `auditoria` (tabla `calendario`): crear, editar (campos virtuales
  `periodos` y `dias_no_lectivos`, con sus valores anterior y nuevo en JSON) e inactivar
  (al volver al general; el particular se conserva como historial).
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, hoy_lima
from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION
from app.models.evaluacion import Asistencia, Clase
from app.models.organizacion import (
    ORIGEN_MANUAL,
    TIPO_PA,
    TIPO_PR,
    AnioEscolar,
    Calendario,
    CalendarioPeriodo,
    Colegio,
    DiaNoLectivo,
    DocenteColegioGrado,
    PeriodoAcademico,
    Usuario,
)
from app.schemas.calendario import (
    CalendarioCrear,
    CalendarioEditar,
    CalendarioParticularCrear,
    CalendarioRespuesta,
    DiaNoLectivoEntrada,
    DiaNoLectivoSalida,
    PeriodoEnFecha,
    PeriodoEntrada,
    PeriodoSalida,
)
from app.schemas.comun import ItemCatalogo
from app.services.auditoria import CREAR, EDITAR, INACTIVAR, registrar_auditoria
from app.services.calendario import (
    NUMEROS_PA,
    ORDEN_PERIODOS,
    CalendarioResuelto,
    Calendarios,
    PeriodoCalendario,
    VIERNES,
    RangoNoLectivo,
    anio_de_referencia,
    cargar_calendarios,
    nombre_periodo,
    resolver,
)
from app.services.habilitacion import ORIGEN_PERMISO, habilitaciones_vigentes, pares_visibles
from app.services.usuario_service import DOCENTE, nombre_rol

TABLA = "calendario"
CAMPO_PERIODOS = "periodos"
CAMPO_NO_LECTIVOS = "dias_no_lectivos"
REGISTRO_ASISTENCIA = "asistencia"
FORMATO_FECHA = "%d/%m/%Y"
FIN_ANTES_DE_INICIO = "La fecha de fin no puede ser anterior a la fecha de inicio."

# Motivos de las incompatibilidades con registros existentes.
NO_LECTIVA = "no_lectiva"
FUERA_DE_PA = "fuera_de_pa"
CAMBIA_DE_PA = "cambia_de_pa"
_MENSAJES_INCOMPATIBILIDAD = {
    NO_LECTIVA: "La fecha tiene asistencias registradas y quedaría como no lectiva.",
    FUERA_DE_PA: "La fecha tiene asistencias registradas y quedaría fuera de su PA.",
    CAMBIA_DE_PA: "La fecha tiene asistencias registradas y pasaría a otro PA.",
}

Errores = list[tuple[str, str]]


# ── Errores ─────────────────────────────────────────────────────────────────────────

def _conflicto(detail: str, motivo: str, extra: Optional[dict] = None) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_409_CONFLICT, detail, motivo=motivo, extra=extra)


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def _error_validacion(errores: Errores) -> ErrorNegocio:
    """422 con el formato único de validación: un error por campo afectado."""
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        MENSAJE_GENERAL,
        motivo=MOTIVO_VALIDACION,
        extra={"errores": [{"campo": campo, "mensaje": mensaje} for campo, mensaje in errores]},
    )


def no_configurado() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_404_NOT_FOUND,
        "No hay un calendario académico configurado para este año escolar.",
        motivo="calendario_no_configurado",
    )


def _modificacion_concurrente() -> ErrorNegocio:
    return _conflicto(
        "El calendario fue modificado por otro Supervisor. Consulta la versión actual antes de volver a guardar.",
        "modificacion_concurrente",
    )


# ── Validación de la propuesta (sin BD) ─────────────────────────────────────────────

def _fecha(dia: date) -> str:
    return dia.strftime(FORMATO_FECHA)


def _indice_de_periodos(periodos: list[PeriodoEntrada]) -> tuple[dict[tuple[str, int], int], Errores]:
    """{(tipo, numero): posición} y los errores de repetidos, faltantes e intervalos."""
    indice: dict[tuple[str, int], int] = {}
    errores: Errores = []
    for i, periodo in enumerate(periodos):
        clave = (periodo.tipo, periodo.numero)
        if clave in indice:
            errores.append((f"periodos.{i}", f"El periodo {nombre_periodo(*clave)} está repetido."))
        indice.setdefault(clave, i)
        if periodo.fecha_fin < periodo.fecha_inicio:
            errores.append((f"periodos.{i}.fecha_fin", FIN_ANTES_DE_INICIO))
    faltan = [nombre_periodo(*clave) for clave in ORDEN_PERIODOS if clave not in indice]
    if faltan:
        errores.append(("periodos", f"Faltan los periodos: {', '.join(faltan)}."))
    return indice, errores


def errores_de_periodos(periodos: list[PeriodoEntrada]) -> Errores:
    """Los 8 periodos, sin repetir, con inicio <= fin y en orden PA1 < PR1 < ... < PR4
    sin superponerse."""
    indice, errores = _indice_de_periodos(periodos)
    if errores:
        return errores
    for anterior, siguiente in zip(ORDEN_PERIODOS, ORDEN_PERIODOS[1:]):
        a, s = periodos[indice[anterior]], periodos[indice[siguiente]]
        if s.fecha_inicio <= a.fecha_fin:
            errores.append((
                f"periodos.{indice[siguiente]}.fecha_inicio",
                f"{nombre_periodo(*siguiente)} debe empezar después del fin de {nombre_periodo(*anterior)} "
                f"({_fecha(a.fecha_fin)}).",
            ))
    return errores


def _dentro_de_un_pa(dia: DiaNoLectivoEntrada, periodos: list[PeriodoEntrada]) -> bool:
    return any(
        p.tipo == TIPO_PA and p.fecha_inicio <= dia.fecha_inicio and dia.fecha_fin <= p.fecha_fin for p in periodos
    )


def _superposiciones(dias: list[DiaNoLectivoEntrada]) -> Errores:
    """Un error por cada día no lectivo que se superpone con uno que empieza antes."""
    orden = sorted(range(len(dias)), key=lambda i: (dias[i].fecha_inicio, dias[i].fecha_fin))
    errores: Errores = []
    ultimo: Optional[int] = None
    for i in orden:
        if ultimo is not None and dias[i].fecha_inicio <= dias[ultimo].fecha_fin:
            errores.append((
                f"dias_no_lectivos.{i}",
                f"Se superpone con el día no lectivo «{dias[ultimo].motivo}».",
            ))
        if ultimo is None or dias[i].fecha_fin > dias[ultimo].fecha_fin:
            ultimo = i
    return errores


def errores_de_no_lectivos(dias: list[DiaNoLectivoEntrada], periodos: list[PeriodoEntrada]) -> Errores:
    """Cada día no lectivo con inicio <= fin, dentro de un PA y sin superponerse."""
    errores: Errores = []
    validos = []
    for i, dia in enumerate(dias):
        if dia.fecha_fin < dia.fecha_inicio:
            errores.append((f"dias_no_lectivos.{i}.fecha_fin", FIN_ANTES_DE_INICIO))
            continue
        validos.append(dia)
        if not _dentro_de_un_pa(dia, periodos):
            errores.append((f"dias_no_lectivos.{i}", "Debe estar comprendido dentro de un PA del calendario."))
    return errores + (_superposiciones(dias) if len(validos) == len(dias) else [])


def _validar_propuesta(periodos: list[PeriodoEntrada], dias: list[DiaNoLectivoEntrada]) -> None:
    errores = errores_de_periodos(periodos)
    if not errores:
        errores = errores_de_no_lectivos(dias, periodos)
    if errores:
        raise _error_validacion(errores)


def derivar_recesos(pas: list[tuple[date, date]], fin_del_anio: date) -> list[PeriodoEntrada]:
    """Los 8 periodos a partir de los 4 PA (en orden): cada PR n va del día siguiente al
    fin de PA n al día anterior al inicio de PA n+1, y el PR4 hasta el fin del año. Si un
    hueco queda vacío o negativo, el PR dura 1 día y se corre el inicio del PA siguiente
    (y su fin, si hiciera falta). Lo usan `cargar-calendario` y la migración."""
    periodos: list[PeriodoEntrada] = []
    inicio_minimo: Optional[date] = None
    for numero, (inicio, fin) in enumerate(pas, start=1):
        if inicio_minimo is not None and inicio < inicio_minimo:
            inicio, fin = inicio_minimo, max(fin, inicio_minimo)
        siguiente = pas[numero][0] if numero < len(pas) else fin_del_anio + timedelta(days=1)
        fin_receso = max(siguiente - timedelta(days=1), fin + timedelta(days=1))
        periodos.append(PeriodoEntrada(tipo="PA", numero=numero, fecha_inicio=inicio, fecha_fin=fin))
        periodos.append(PeriodoEntrada(tipo="PR", numero=numero, fecha_inicio=fin + timedelta(days=1), fecha_fin=fin_receso))
        inicio_minimo = fin_receso + timedelta(days=1)
    return periodos


# ── Años vecinos ────────────────────────────────────────────────────────────────────

def _anios_vecinos(db: Session, anio: AnioEscolar) -> tuple[Optional[AnioEscolar], Optional[AnioEscolar]]:
    anterior = db.exec(
        select(AnioEscolar).where(AnioEscolar.fecha_inicio < anio.fecha_inicio).order_by(
            col(AnioEscolar.fecha_inicio).desc()
        )
    ).first()
    siguiente = db.exec(
        select(AnioEscolar).where(AnioEscolar.fecha_inicio > anio.fecha_inicio).order_by(AnioEscolar.fecha_inicio)
    ).first()
    return anterior, siguiente


def _colegios_a_revisar(
    calendarios: Calendarios, id_anio: int, id_colegio: Optional[int], ids_vecinos: set[int]
) -> list[Optional[int]]:
    """Alcances cuyo calendario de los años vecinos hay que comparar: el colegio del
    particular; para el general, el general y los colegios que lo usan este año pero
    tienen fechas particulares en un año vecino."""
    if id_colegio is not None:
        return [id_colegio]
    con_particular_vecino = {c for (a, c) in calendarios.particulares.keys() if a in ids_vecinos}
    return [None, *sorted(c for c in con_particular_vecino if (id_anio, c) not in calendarios.particulares)]


def _campo_de(periodos: list[PeriodoEntrada], tipo: str, numero: int, atributo: str) -> str:
    """Ruta del campo de un periodo en el cuerpo, p. ej. `periodos.7.fecha_fin`."""
    indice = next(i for i, p in enumerate(periodos) if (p.tipo, p.numero) == (tipo, numero))
    return f"periodos.{indice}.{atributo}"


def _errores_con_vecinos(
    db: Session,
    calendarios: Calendarios,
    anio: AnioEscolar,
    id_colegio: Optional[int],
    periodos: list[PeriodoEntrada],
) -> Errores:
    """El PA1 debe empezar después del PR4 del año anterior y el PR4 debe terminar antes
    del PA1 del año siguiente, con el calendario de cada colegio afectado."""
    anterior, siguiente = _anios_vecinos(db, anio)
    ids_vecinos = {a.id_anio_escolar for a in (anterior, siguiente) if a is not None}
    inicio = min(p.fecha_inicio for p in periodos)
    fin = max(p.fecha_fin for p in periodos)
    errores: Errores = []
    for colegio in _colegios_a_revisar(calendarios, anio.id_anio_escolar, id_colegio, ids_vecinos):
        previo = calendarios.aplicable(colegio, anterior.id_anio_escolar) if anterior is not None else None
        posterior = calendarios.aplicable(colegio, siguiente.id_anio_escolar) if siguiente is not None else None
        if previo is not None and previo.fecha_fin >= inicio:
            errores.append((
                _campo_de(periodos, TIPO_PA, 1, "fecha_inicio"),
                f"El PA1 no puede superponerse con el PR4 del año escolar {anterior.nombre} "
                f"(termina el {_fecha(previo.fecha_fin)}).",
            ))
        if posterior is not None and fin >= posterior.fecha_inicio:
            errores.append((
                _campo_de(periodos, TIPO_PR, 4, "fecha_fin"),
                f"El PR4 no puede superponerse con el PA1 del año escolar {siguiente.nombre} "
                f"(empieza el {_fecha(posterior.fecha_inicio)}).",
            ))
    return list(dict.fromkeys(errores))


# ── Impacto sobre registros existentes ──────────────────────────────────────────────

@dataclass(frozen=True)
class _Registro:
    id_colegio: int
    fecha: date
    id_periodo_academico: int
    tipo: str


def _registros(db: Session, ids_colegio: set[int], id_anio: int) -> list[_Registro]:
    """Fechas con registros de esos colegios en los PA del año, con su PA. Hoy, las
    clases con asistencias, en su fecha vigente (los demás módulos se suman aquí cuando
    existan)."""
    if not ids_colegio:
        return []
    con_asistencias = select(Asistencia.id_clase)
    filas = db.exec(
        select(Clase.id_colegio, Clase.fecha, Clase.id_periodo_academico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == Clase.id_periodo_academico)
        .where(
            col(Clase.id_colegio).in_(ids_colegio),
            PeriodoAcademico.id_anio_escolar == id_anio,
            col(Clase.id_clase).in_(con_asistencias),
        )
        .distinct()
        .order_by(Clase.id_colegio, Clase.fecha)
    ).all()
    return [_Registro(id_colegio, fecha, id_periodo, REGISTRO_ASISTENCIA) for id_colegio, fecha, id_periodo in filas]


def _motivo_incompatible(id_periodo: int, propuesta: CalendarioResuelto, fecha: date) -> Optional[str]:
    despues = propuesta.periodo_de(fecha)
    if despues is None or not despues.es_pa:
        return FUERA_DE_PA
    if despues.id_periodo_academico != id_periodo:
        return CAMBIA_DE_PA
    if not propuesta.es_lectivo(fecha):
        return NO_LECTIVA
    return None


def _incompatibilidades(
    db: Session, id_anio: int, ids_colegio: set[int], propuesta: CalendarioResuelto
) -> list[dict]:
    """Registros de esos colegios que el cambio dejaría en un día no lectivo, fuera de su
    PA o en otro PA. Solo se evalúan los de los PA de ese año."""
    nombres = dict(db.exec(select(Colegio.id_colegio, Colegio.nombre).where(col(Colegio.id_colegio).in_(ids_colegio))).all())
    incompatibles = []
    for registro in _registros(db, ids_colegio, id_anio):
        motivo = _motivo_incompatible(registro.id_periodo_academico, propuesta, registro.fecha)
        if motivo is not None:
            incompatibles.append({
                "colegio": {"id": registro.id_colegio, "nombre": nombres.get(registro.id_colegio, "")},
                "fecha": registro.fecha.isoformat(),
                "tipo_registro": registro.tipo,
                "motivo": motivo,
                "mensaje": _MENSAJES_INCOMPATIBILIDAD[motivo],
            })
    return incompatibles


def _exigir_compatible(db: Session, id_anio: int, ids_colegio: set[int], propuesta: CalendarioResuelto) -> None:
    incompatibles = _incompatibilidades(db, id_anio, ids_colegio, propuesta)
    if incompatibles:
        raise _conflicto(
            "No se puede guardar el calendario: el cambio afecta registros existentes.",
            "calendario_incompatible",
            extra={"incompatibilidades": incompatibles},
        )


def _colegios_del_general(db: Session, calendarios: Calendarios, id_anio: int) -> set[int]:
    """Colegios que usan el calendario general del año (sin particular activo)."""
    todos = set(db.exec(select(Colegio.id_colegio)).all())
    return {c for c in todos if (id_anio, c) not in calendarios.particulares}


def _colegios_afectados(db: Session, calendarios: Calendarios, calendario: Calendario) -> set[int]:
    if calendario.id_colegio is not None:
        return {calendario.id_colegio}
    return _colegios_del_general(db, calendarios, calendario.id_anio_escolar)


# ── Lectura de filas y propuestas ───────────────────────────────────────────────────

def _anio_existente(db: Session, id_anio_escolar: int) -> AnioEscolar:
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise _no_procesable("No existe el año escolar indicado.", "anio_inexistente")
    return anio


def _colegio_existente(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    return colegio


def _calendario_bloqueado(db: Session, id_calendario: int) -> Calendario:
    """La fila del calendario, bloqueada hasta el commit (dos Supervisores a la vez)."""
    calendario = db.exec(
        select(Calendario)
        .where(Calendario.id_calendario == id_calendario)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if calendario is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "El calendario no existe.", motivo="calendario_no_encontrado")
    return calendario


def asegurar_periodos_academicos(db: Session, id_anio: int, id_actor: int, ahora: datetime) -> dict[int, int]:
    """{numero: id_periodo_academico} de PA1 a PA4 del año, creando los que falten."""
    existentes = {
        p.numero: p
        for p in db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == id_anio)).all()
    }
    for numero in NUMEROS_PA:
        if numero not in existentes:
            existentes[numero] = PeriodoAcademico(
                id_anio_escolar=id_anio, numero=numero, creado_por=id_actor, creado_en=ahora
            )
            db.add(existentes[numero])
    db.flush()
    return {numero: p.id_periodo_academico for numero, p in existentes.items()}


def _origenes(calendario: Optional[CalendarioResuelto]) -> dict[tuple[str, date, date], str]:
    """Origen de cada día no lectivo de un calendario, por (motivo, inicio, fin)."""
    if calendario is None:
        return {}
    return {(r.motivo, r.fecha_inicio, r.fecha_fin): r.origen for r in calendario.no_lectivos}


def resolver_propuesta(
    calendario: Calendario,
    periodos: list[PeriodoEntrada],
    dias: list[DiaNoLectivoEntrada],
    origenes: dict[tuple[str, date, date], str],
    ids_periodo: dict[int, int],
) -> CalendarioResuelto:
    """La propuesta como CalendarioResuelto, sin escribir nada. Un día no lectivo que ya
    existía (mismo motivo y fechas) conserva su origen; uno nuevo es manual."""
    filas_periodo = [
        CalendarioPeriodo(tipo=p.tipo, numero=p.numero, fecha_inicio=p.fecha_inicio, fecha_fin=p.fecha_fin)
        for p in periodos
    ]
    filas_dia = [
        DiaNoLectivo(
            motivo=d.motivo, fecha_inicio=d.fecha_inicio, fecha_fin=d.fecha_fin,
            origen=origenes.get((d.motivo, d.fecha_inicio, d.fecha_fin), ORIGEN_MANUAL),
        )
        for d in dias
    ]
    ids = {(calendario.id_anio_escolar, numero): id_periodo for numero, id_periodo in ids_periodo.items()}
    return resolver(calendario, filas_periodo, filas_dia, ids)


def _ids_periodo(db: Session, id_anio: int) -> dict[int, int]:
    return dict(
        db.exec(
            select(PeriodoAcademico.numero, PeriodoAcademico.id_periodo_academico).where(
                PeriodoAcademico.id_anio_escolar == id_anio
            )
        ).all()
    )


# ── Escritura ───────────────────────────────────────────────────────────────────────

def _escribir_periodos(db: Session, calendario: Calendario, propuesta: CalendarioResuelto) -> None:
    """Deja las 8 filas de periodos con las fechas de la propuesta."""
    actuales = {
        (p.tipo, p.numero): p
        for p in db.exec(
            select(CalendarioPeriodo).where(CalendarioPeriodo.id_calendario == calendario.id_calendario)
        ).all()
    }
    for periodo in propuesta.periodos:
        fila = actuales.get((periodo.tipo, periodo.numero)) or CalendarioPeriodo(
            id_calendario=calendario.id_calendario, tipo=periodo.tipo, numero=periodo.numero,
            fecha_inicio=periodo.fecha_inicio, fecha_fin=periodo.fecha_fin,
        )
        fila.fecha_inicio, fila.fecha_fin = periodo.fecha_inicio, periodo.fecha_fin
        db.add(fila)


def _escribir_no_lectivos(
    db: Session, calendario: Calendario, propuesta: CalendarioResuelto, id_actor: int, ahora: datetime
) -> None:
    """Conserva los días no lectivos que no cambian, borra los retirados e inserta los
    nuevos."""
    deseados = {(r.motivo, r.fecha_inicio, r.fecha_fin): r for r in propuesta.no_lectivos}
    for fila in db.exec(select(DiaNoLectivo).where(DiaNoLectivo.id_calendario == calendario.id_calendario)).all():
        if deseados.pop((fila.motivo, fila.fecha_inicio, fila.fecha_fin), None) is None:
            db.delete(fila)
    db.add_all(
        DiaNoLectivo(
            id_calendario=calendario.id_calendario, motivo=r.motivo, fecha_inicio=r.fecha_inicio,
            fecha_fin=r.fecha_fin, origen=r.origen, creado_por=id_actor, creado_en=ahora,
        )
        for r in deseados.values()
    )


def insertar_calendario(
    db: Session, id_anio: int, id_colegio: Optional[int], propuesta: CalendarioResuelto, id_actor: int, ahora: datetime
) -> Calendario:
    """Crea la fila del calendario con sus periodos y días no lectivos (sin commit)."""
    calendario = Calendario(id_anio_escolar=id_anio, id_colegio=id_colegio, creado_por=id_actor, creado_en=ahora)
    db.add(calendario)
    db.flush()
    _escribir_periodos(db, calendario, propuesta)
    _escribir_no_lectivos(db, calendario, propuesta, id_actor, ahora)
    return calendario


def _json_periodos(calendario: Optional[CalendarioResuelto]) -> Optional[str]:
    if calendario is None:
        return None
    return json.dumps(
        [{"periodo": p.nombre, "fecha_inicio": p.fecha_inicio.isoformat(), "fecha_fin": p.fecha_fin.isoformat()}
         for p in calendario.periodos],
        ensure_ascii=False,
    )


def _json_no_lectivos(calendario: Optional[CalendarioResuelto]) -> Optional[str]:
    if calendario is None:
        return None
    return json.dumps(
        [{"motivo": r.motivo, "fecha_inicio": r.fecha_inicio.isoformat(), "fecha_fin": r.fecha_fin.isoformat(),
          "origen": r.origen} for r in calendario.no_lectivos],
        ensure_ascii=False,
    )


def _cambios(antes: Optional[CalendarioResuelto], despues: CalendarioResuelto) -> dict[str, tuple]:
    """Campos virtuales de la auditoría que cambian, con sus valores en JSON."""
    cambios = {
        CAMPO_PERIODOS: (_json_periodos(antes), _json_periodos(despues)),
        CAMPO_NO_LECTIVOS: (_json_no_lectivos(antes), _json_no_lectivos(despues)),
    }
    return {campo: valores for campo, valores in cambios.items() if valores[0] != valores[1]}


def _auditar(db: Session, calendario: Calendario, accion: str, actor: Usuario, ahora: datetime, cambios=None) -> None:
    registrar_auditoria(
        db, tabla=TABLA, id_registro=calendario.id_calendario, accion=accion, cambios=cambios,
        id_usuario=actor.id_usuario, fecha=ahora,
    )


def _guardar(db: Session, error_si_choca: ErrorNegocio) -> None:
    """Commit; si dos peticiones simultáneas chocan en un índice único, el error dado."""
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise error_si_choca from error


# ── Respuesta ───────────────────────────────────────────────────────────────────────

def _periodo_salida(periodo: PeriodoCalendario) -> PeriodoSalida:
    return PeriodoSalida(
        tipo=periodo.tipo, numero=periodo.numero, id_periodo_academico=periodo.id_periodo_academico,
        fecha_inicio=periodo.fecha_inicio, fecha_fin=periodo.fecha_fin,
    )


def _dia_salida(rango: RangoNoLectivo) -> DiaNoLectivoSalida:
    return DiaNoLectivoSalida(
        motivo=rango.motivo, fecha_inicio=rango.fecha_inicio, fecha_fin=rango.fecha_fin, origen=rango.origen
    )


def respuesta(calendario: CalendarioResuelto, colegio: Optional[Colegio]) -> CalendarioRespuesta:
    hoy = hoy_lima()
    actual = calendario.periodo_de(hoy)
    return CalendarioRespuesta(
        id_calendario=calendario.id_calendario,
        id_anio_escolar=calendario.id_anio_escolar,
        colegio=ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre) if colegio is not None else None,
        origen="general" if calendario.es_general else "particular",
        version=calendario.version,
        periodos=[_periodo_salida(p) for p in calendario.periodos],
        dias_no_lectivos=[_dia_salida(r) for r in calendario.no_lectivos],
        periodo_actual=_periodo_salida(actual) if actual is not None else None,
        fecha_referencia=hoy,
    )


def _respuesta_de(db: Session, id_anio: int, colegio: Optional[Colegio]) -> CalendarioRespuesta:
    id_colegio = colegio.id_colegio if colegio is not None else None
    calendarios = cargar_calendarios(db, id_anio, ids_colegio=[] if id_colegio is None else [id_colegio])
    aplicable = calendarios.aplicable(id_colegio, id_anio)
    if aplicable is None:
        raise no_configurado()
    return respuesta(aplicable, colegio)


# ── Consulta ────────────────────────────────────────────────────────────────────────

def _colegios_del_docente(db: Session, usuario: Usuario, id_anio: int) -> set[int]:
    """Colegios en que el docente tiene alguna asignación de un PA de ese año, más los de
    sus permisos de corrección vigentes (CU028)."""
    if usuario.id_docente is None:
        return set()
    asignados = set(
        db.exec(
            select(DocenteColegioGrado.id_colegio)
            .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
            .where(DocenteColegioGrado.id_docente == usuario.id_docente, PeriodoAcademico.id_anio_escolar == id_anio)
        ).all()
    )
    return asignados | {h.id_colegio for h in habilitaciones_vigentes(db, usuario) if h.origen == ORIGEN_PERMISO}


def _exigir_lectura_docente(db: Session, usuario: Usuario, id_anio: int, id_colegio: Optional[int]) -> None:
    """El Docente solo lee el calendario aplicable a sus colegios: los de sus
    asignaciones del año y los de sus permisos vigentes (para validar fechas sin
    conexión)."""
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return
    if id_colegio is None or id_colegio not in _colegios_del_docente(db, usuario, id_anio):
        raise _fuera_de_alcance()


def _fuera_de_alcance() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_403_FORBIDDEN,
        "Solo puedes consultar el calendario de los colegios que tienes asignados.",
        motivo="colegio_fuera_de_alcance",
    )


def consultar(
    db: Session, usuario: Usuario, id_anio_escolar: Optional[int], id_colegio: Optional[int]
) -> CalendarioRespuesta:
    """Calendario aplicable al año (por defecto, el de referencia) y alcance: sin
    `id_colegio`, el general; con él, el particular activo del colegio o el general."""
    if id_anio_escolar is not None:
        anio = _anio_existente(db, id_anio_escolar)
    else:
        anio = anio_de_referencia(db)
        if anio is None:
            raise no_configurado()
    colegio = _colegio_existente(db, id_colegio) if id_colegio is not None else None
    _exigir_lectura_docente(db, usuario, anio.id_anio_escolar, id_colegio)
    return _respuesta_de(db, anio.id_anio_escolar, colegio)


FUERA_DE_PERIODO = "fuera_de_periodo"
RECESO = "receso"
FIN_DE_SEMANA = "fin_de_semana"


def _motivo_no_lectivo(calendario: Optional[CalendarioResuelto], fecha: date) -> Optional[str]:
    """Por qué la fecha no es lectiva en ese calendario (None si lo es), en este orden:
    fuera de todo periodo, receso, fin de semana o el motivo del día no lectivo."""
    periodo = calendario.periodo_de(fecha) if calendario is not None else None
    if periodo is None:
        return FUERA_DE_PERIODO
    if not periodo.es_pa:
        return RECESO
    if fecha.weekday() > VIERNES:
        return FIN_DE_SEMANA
    no_lectivo = calendario.no_lectivo_de(fecha)
    return no_lectivo.motivo if no_lectivo is not None else None


def periodo_en_fecha(db: Session, usuario: Usuario, id_colegio: int, fecha: date) -> PeriodoEnFecha:
    """El periodo y si es lectiva una fecha en el calendario de un colegio. Supervisor:
    cualquier colegio; Docente: los de su alcance de lectura (403 si no)."""
    _colegio_existente(db, id_colegio)
    if nombre_rol(db, usuario.id_rol) == DOCENTE and id_colegio not in {c for c, _ in pares_visibles(db, usuario)}:
        raise _fuera_de_alcance()
    calendario = cargar_calendarios(db, ids_colegio=[id_colegio]).calendario_en(id_colegio, fecha)
    periodo = calendario.periodo_de(fecha) if calendario is not None else None
    motivo = _motivo_no_lectivo(calendario, fecha)
    datos = {} if periodo is None else {
        "tipo": periodo.tipo, "numero": periodo.numero, "id_periodo_academico": periodo.id_periodo_academico,
        "fecha_inicio": periodo.fecha_inicio, "fecha_fin": periodo.fecha_fin,
    }
    return PeriodoEnFecha(fecha=fecha, es_lectivo=motivo is None, motivo_no_lectivo=motivo, **datos)


# ── Crear el general ────────────────────────────────────────────────────────────────

def _general_existente() -> ErrorNegocio:
    return _conflicto("Ya existe un calendario general para este año escolar.", "calendario_existente")


def crear_general(db: Session, data: CalendarioCrear, actor: Usuario) -> CalendarioRespuesta:
    anio = _anio_existente(db, data.id_anio_escolar)
    calendarios = cargar_calendarios(db)
    if anio.id_anio_escolar in calendarios.generales:
        raise _general_existente()
    _validar_propuesta(data.periodos, data.dias_no_lectivos)
    _exigir_sin_errores(_errores_con_vecinos(db, calendarios, anio, None, data.periodos))
    ahora = ahora_utc()
    ids_periodo = asegurar_periodos_academicos(db, anio.id_anio_escolar, actor.id_usuario, ahora)
    borrador = Calendario(id_calendario=0, id_anio_escolar=anio.id_anio_escolar, version=1)
    propuesta = resolver_propuesta(borrador, data.periodos, data.dias_no_lectivos, {}, ids_periodo)
    calendario = insertar_calendario(db, anio.id_anio_escolar, None, propuesta, actor.id_usuario, ahora)
    _auditar(db, calendario, CREAR, actor, ahora)
    _guardar(db, _general_existente())
    return _respuesta_de(db, anio.id_anio_escolar, None)


def _exigir_sin_errores(errores: Errores) -> None:
    if errores:
        raise _error_validacion(errores)


# ── Editar ──────────────────────────────────────────────────────────────────────────

def editar(db: Session, id_calendario: int, data: CalendarioEditar, actor: Usuario) -> CalendarioRespuesta:
    """Reemplaza periodos y días no lectivos del general o de un particular activo."""
    calendario = _calendario_bloqueado(db, id_calendario)
    if not calendario.activo:
        raise _conflicto("El colegio ya no usa este calendario particular.", "calendario_inactivo")
    if calendario.version != data.version:
        raise _modificacion_concurrente()
    _validar_propuesta(data.periodos, data.dias_no_lectivos)
    anio = db.get(AnioEscolar, calendario.id_anio_escolar)
    calendarios = cargar_calendarios(db)
    antes = calendarios.aplicable(calendario.id_colegio, anio.id_anio_escolar)
    ids_periodo = _ids_periodo(db, anio.id_anio_escolar)
    propuesta = resolver_propuesta(calendario, data.periodos, data.dias_no_lectivos, _origenes(antes), ids_periodo)
    _exigir_sin_errores(_errores_con_vecinos(db, calendarios, anio, calendario.id_colegio, data.periodos))
    _exigir_compatible(db, anio.id_anio_escolar, _colegios_afectados(db, calendarios, calendario), propuesta)
    colegio = db.get(Colegio, calendario.id_colegio) if calendario.id_colegio is not None else None
    cambios = _cambios(antes, propuesta)
    if not cambios:
        return respuesta(antes, colegio)
    ahora = ahora_utc()
    _escribir_periodos(db, calendario, propuesta)
    _escribir_no_lectivos(db, calendario, propuesta, actor.id_usuario, ahora)
    calendario.version += 1
    calendario.modificado_por, calendario.modificado_en = actor.id_usuario, ahora
    db.add(calendario)
    _auditar(db, calendario, EDITAR, actor, ahora, cambios)
    _guardar(db, _modificacion_concurrente())
    return _respuesta_de(db, anio.id_anio_escolar, colegio)


# ── Fechas particulares de un colegio ───────────────────────────────────────────────

def _particular_existente() -> ErrorNegocio:
    return _conflicto(
        "El colegio ya tiene un calendario con fechas particulares para este año escolar.",
        "calendario_particular_existente",
    )


def _dias_del_general(general: CalendarioResuelto, periodos: list[PeriodoEntrada]) -> list[DiaNoLectivoEntrada]:
    """Días no lectivos del general que caen dentro de un PA de las fechas particulares
    (los demás no tendrían dónde vivir en ese calendario)."""
    dias = [
        DiaNoLectivoEntrada(motivo=r.motivo, fecha_inicio=r.fecha_inicio, fecha_fin=r.fecha_fin)
        for r in general.no_lectivos
    ]
    return [d for d in dias if _dentro_de_un_pa(d, periodos)]


def crear_particular(db: Session, data: CalendarioParticularCrear, actor: Usuario) -> CalendarioRespuesta:
    """Fechas particulares del colegio para el año. Requiere el general; sin
    `dias_no_lectivos` copia los del general (con su origen) que caen dentro de un PA de
    las fechas particulares."""
    anio = _anio_existente(db, data.id_anio_escolar)
    colegio = _colegio_existente(db, data.id_colegio)
    calendarios = cargar_calendarios(db)
    general = calendarios.generales.get(anio.id_anio_escolar)
    if general is None:
        raise _conflicto(
            "Primero debes configurar el calendario general para poder definir fechas particulares de este colegio.",
            "calendario_general_requerido",
        )
    if (anio.id_anio_escolar, colegio.id_colegio) in calendarios.particulares:
        raise _particular_existente()
    _validar_propuesta(data.periodos, data.dias_no_lectivos or [])
    dias = data.dias_no_lectivos if data.dias_no_lectivos is not None else _dias_del_general(general, data.periodos)
    borrador = Calendario(id_calendario=0, id_anio_escolar=anio.id_anio_escolar, id_colegio=colegio.id_colegio, version=1)
    propuesta = resolver_propuesta(borrador, data.periodos, dias, _origenes(general), _ids_periodo(db, anio.id_anio_escolar))
    _exigir_sin_errores(_errores_con_vecinos(db, calendarios, anio, colegio.id_colegio, data.periodos))
    _exigir_compatible(db, anio.id_anio_escolar, {colegio.id_colegio}, propuesta)
    ahora = ahora_utc()
    calendario = insertar_calendario(db, anio.id_anio_escolar, colegio.id_colegio, propuesta, actor.id_usuario, ahora)
    _auditar(db, calendario, CREAR, actor, ahora)
    _guardar(db, _particular_existente())
    return _respuesta_de(db, anio.id_anio_escolar, colegio)


# ── Volver al general ───────────────────────────────────────────────────────────────

def usar_general(db: Session, id_calendario: int, version: Optional[int], actor: Usuario) -> CalendarioRespuesta:
    """Desactiva el particular (se conserva como historial) y el colegio vuelve al
    general, si el cambio no deja registros fuera de su PA ni en días no lectivos."""
    calendario = _calendario_bloqueado(db, id_calendario)
    if calendario.id_colegio is None:
        raise _conflicto("El calendario general no se puede desactivar.", "calendario_es_general")
    if not calendario.activo:
        raise _conflicto("El colegio ya usa el calendario general.", "calendario_inactivo")
    if version is not None and version != calendario.version:
        raise _modificacion_concurrente()
    calendarios = cargar_calendarios(db)
    general = calendarios.generales.get(calendario.id_anio_escolar)
    if general is None:
        raise no_configurado()
    _exigir_compatible(db, calendario.id_anio_escolar, {calendario.id_colegio}, general)
    ahora = ahora_utc()
    calendario.activo = False
    calendario.version += 1
    calendario.modificado_por, calendario.modificado_en = actor.id_usuario, ahora
    db.add(calendario)
    _auditar(db, calendario, INACTIVAR, actor, ahora)
    _guardar(db, _modificacion_concurrente())
    return _respuesta_de(db, calendario.id_anio_escolar, db.get(Colegio, calendario.id_colegio))
