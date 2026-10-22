"""Sincronización de operaciones hechas sin conexión y su estado (CU017 a CU021).

Dos tablas sostienen todo:

- `operacion_pendiente`: el aviso de que un dispositivo tiene una operación todavía no
  enviada (POST /sincronizacion/pendientes, al reconectar y antes de enviarlas).
- `operacion_sincronizacion`: el resultado de cada operación recibida, aceptada,
  rechazada o descartada (le ganó un registro más reciente, o el usuario descartó su
  rechazo con "Cargar versión actual"). Hace idempotente el envío.

`procesar_operacion` es el único camino de escritura de los módulos con cola offline: la
aplica en un SAVEPOINT, registra el resultado y borra su aviso de pendiente.

Estados (CU017 a CU021), calculados en SQL para mostrar, filtrar, ordenar y paginar en
la BD:

- actividad: `error` si tiene operaciones rechazadas; si no, `pendiente` si tiene
  avisos de pendientes; si no, `sincronizada`.
- docente: lo mismo (`al_dia` en vez de `sincronizada`), y `sin_informacion` solo si
  nunca registró una actividad (sus "Registros pendientes informados" van en null).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional

from fastapi import status
from sqlalchemy import Integer, String, case, cast, delete, exists, func, null, select as sa_select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import (
    ACEPTADA,
    DESCARTADA,
    RECHAZADA,
    Actividad,
    OperacionPendiente,
    OperacionSincronizacion,
)
from app.schemas.sincronizacion import EstadoOperacion, ItemOperacion, PendienteInformado, ResultadoOperacion
from app.services.sesiones import MARGEN_RELOJ

SINCRONIZADA = "sincronizada"
PENDIENTE = "pendiente"
ERROR = "error"
AL_DIA = "al_dia"
SIN_INFORMACION = "sin_informacion"
DESCONOCIDA = "desconocida"

EstadoSincronizacionActividad = Literal["sincronizada", "pendiente", "error"]
EstadoSincronizacionDocente = Literal["error", "pendiente", "sin_informacion", "al_dia"]

# Orden del listado de CU020: primero los que requieren atención.
PRIORIDAD_ESTADO_DOCENTE = {ERROR: 0, PENDIENTE: 1, SIN_INFORMACION: 2, AL_DIA: 3}

FUERA_DE_ACTIVIDAD = "fuera_de_actividad"
ACTIVIDAD_REQUERIDA = "actividad_requerida"
REALIZADO_EN_FUTURO = "realizado_en_futuro"
OPERACION_AJENA = "operacion_ajena"
MENSAJE_FUERA_DE_ACTIVIDAD = "La operación se realizó fuera de una actividad de trabajo del docente."
MENSAJE_ACTIVIDAD_REQUERIDA = "Inicia una actividad de trabajo para registrar información."
MENSAJE_REALIZADO_EN_FUTURO = "La hora de la operación es posterior a la hora actual."
MENSAJE_OPERACION_AJENA = "El identificador de la operación ya fue usado."
MENSAJE_OPERACION_NO_ENCONTRADA = "La operación no existe."
MENSAJE_NO_RECHAZADA = "Solo se puede descartar una operación rechazada."


# ── Estados en SQL (los usan Seguimiento y las listas de actividades) ────────────────

def _contar(tabla, *condiciones) -> ColumnElement[int]:
    """Subconsulta correlacionada: cuántas filas de `tabla` cumplen las condiciones."""
    return sa_select(func.count()).select_from(tabla).where(*condiciones).scalar_subquery()


def pendientes_actividad_sql() -> ColumnElement[int]:
    """Avisos de pendientes de la actividad (`Actividad.id_actividad`)."""
    return _contar(OperacionPendiente, OperacionPendiente.id_actividad == Actividad.id_actividad)


def errores_actividad_sql() -> ColumnElement[int]:
    """Operaciones de la actividad rechazadas en forma definitiva."""
    return _contar(
        OperacionSincronizacion,
        OperacionSincronizacion.id_actividad == Actividad.id_actividad,
        OperacionSincronizacion.estado == RECHAZADA,
    )


def estado_actividad_sql(pendientes: ColumnElement[int], errores: ColumnElement[int]) -> ColumnElement[str]:
    """`error` si hay rechazos, `pendiente` si hay pendientes; si no, `sincronizada`.
    Independiente de que la actividad esté en curso o finalizada."""
    return cast(
        case((errores > 0, ERROR), (pendientes > 0, PENDIENTE), else_=SINCRONIZADA),
        String,
    )


def pendientes_docente_sql() -> ColumnElement[int]:
    """Avisos de pendientes del usuario (`Usuario.id_usuario`); NULL si el docente nunca
    registró una actividad (no hay información)."""
    tiene_actividades = exists().where(Actividad.id_docente == Usuario.id_docente)
    pendientes = _contar(OperacionPendiente, OperacionPendiente.id_usuario == Usuario.id_usuario)
    return case((tiene_actividades, pendientes), else_=cast(null(), Integer))


def errores_docente_sql() -> ColumnElement[int]:
    """Operaciones del usuario rechazadas en forma definitiva."""
    return _contar(
        OperacionSincronizacion,
        OperacionSincronizacion.id_usuario == Usuario.id_usuario,
        OperacionSincronizacion.estado == RECHAZADA,
    )


def estado_docente_sql(pendientes: ColumnElement[int], errores: ColumnElement[int]) -> ColumnElement[str]:
    """`error` si hay rechazos, `pendiente` si hay pendientes, `sin_informacion` si nunca
    registró una actividad y `al_dia` si no queda nada."""
    return cast(
        case(
            (errores > 0, ERROR),
            (pendientes > 0, PENDIENTE),
            (pendientes.is_(None), SIN_INFORMACION),
            else_=AL_DIA,
        ),
        String,
    )


def prioridad_estado_docente_sql(estado: ColumnElement[str]) -> ColumnElement[int]:
    """Posición del estado en el orden de CU020 (error → pendiente → sin información →
    al día), para ordenar en la consulta y que la paginación sea correcta."""
    return case(PRIORIDAD_ESTADO_DOCENTE, value=estado, else_=len(PRIORIDAD_ESTADO_DOCENTE))


def pendientes_del_usuario(db: Session, id_usuario: int) -> int:
    """Avisos de pendientes del usuario (Inicio del Docente)."""
    return db.exec(
        select(func.count()).select_from(OperacionPendiente).where(OperacionPendiente.id_usuario == id_usuario)
    ).one()


# ── Procesar una operación ──────────────────────────────────────────────────────────

class RechazoOperacion(Exception):
    """Una regla de negocio impide aplicar la operación: se registra como rechazada."""

    def __init__(self, motivo: str, detail: str, registro: Optional[dict[str, Any]] = None):
        super().__init__(detail)
        self.motivo = motivo
        self.detail = detail
        self.registro = registro


class OperacionDescartada(Exception):
    """El registro ya tiene un valor más reciente: la operación no se aplica, no es un
    error y se responde con el valor vigente (p. ej. Asistencias, CU022)."""

    def __init__(self, motivo: str, registro: Optional[dict[str, Any]]):
        super().__init__(motivo)
        self.motivo = motivo
        self.registro = registro


@dataclass(frozen=True)
class Operacion:
    """Qué toca la operación: módulo, tabla y clave del registro. `mensaje_sin_actividad`
    reemplaza el mensaje genérico de `actividad_requerida` (cada CU tiene el suyo)."""

    modulo: str
    entidad: str
    id_entidad: str
    mensaje_sin_actividad: Optional[str] = None


@dataclass(frozen=True)
class ContextoOperacion:
    """Con qué se aplica: la habilitación se evalúa en `en` (la hora real), no ahora."""

    en: datetime
    id_actividad: Optional[uuid.UUID]
    version: Optional[int]


def _limite_de(db: Session, actividad: Actividad) -> datetime:
    """Último instante válido de la actividad: su fin o, si sigue abierta, el
    vencimiento de su sesión."""
    if actividad.fin is not None:
        return actividad.fin
    return db.get(Sesion, actividad.id_sesion).expira


def _actividad_activa(db: Session, id_docente: int) -> Optional[Actividad]:
    return db.exec(
        select(Actividad)
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(
            Actividad.id_docente == id_docente,
            col(Actividad.fin).is_(None),
            col(Sesion.fin).is_(None),
            Sesion.expira > ahora_utc(),
        )
    ).first()


def _actividad_de_la_operacion(
    db: Session, usuario: Usuario, id_actividad: Optional[uuid.UUID], en: datetime, mensaje_sin_actividad: str
) -> Optional[Actividad]:
    """La actividad del Docente en que cae `en` (la indicada o, sin ella, la activa).
    Para otros roles, None (no trabajan con actividades)."""
    if usuario.id_docente is None:
        return None
    if id_actividad is None:
        actividad = _actividad_activa(db, usuario.id_docente)
        if actividad is None:
            raise RechazoOperacion(ACTIVIDAD_REQUERIDA, mensaje_sin_actividad)
    else:
        actividad = db.get(Actividad, id_actividad)
    valida = (
        actividad is not None
        and actividad.id_docente == usuario.id_docente
        and actividad.inicio <= en <= _limite_de(db, actividad)
    )
    if not valida:
        raise RechazoOperacion(FUERA_DE_ACTIVIDAD, MENSAJE_FUERA_DE_ACTIVIDAD)
    return actividad


def _instante_de(item: ItemOperacion, ahora: datetime) -> datetime:
    en = item.realizado_en or ahora
    if en > ahora + MARGEN_RELOJ:
        raise RechazoOperacion(REALIZADO_EN_FUTURO, MENSAJE_REALIZADO_EN_FUTURO)
    return en


def _aplicar(
    db: Session, fn_aplicar, contexto: ContextoOperacion, id_operacion: uuid.UUID
) -> tuple[str, ResultadoOperacion]:
    """Ejecuta `fn_aplicar` en un SAVEPOINT. Un error de negocio deshace solo esta
    operación; uno técnico se propaga (y no se registra nada, para que el cliente
    reintente)."""
    resultado = {"id_operacion": id_operacion}
    try:
        with db.begin_nested():
            registro = fn_aplicar(contexto)
        return ACEPTADA, ResultadoOperacion(**resultado, estado=ACEPTADA, registro=registro)
    except OperacionDescartada as descarte:
        return DESCARTADA, ResultadoOperacion(
            **resultado, estado=ACEPTADA, motivo=descarte.motivo, registro=descarte.registro
        )
    except RechazoOperacion as rechazo:
        return RECHAZADA, _rechazada(resultado, rechazo.motivo, rechazo.detail, rechazo.registro)
    except ErrorNegocio as error:
        if error.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            raise
        return RECHAZADA, _rechazada(resultado, error.motivo, error.detail)


def _rechazada(base: dict, motivo: Optional[str], detail: str, registro=None) -> ResultadoOperacion:
    return ResultadoOperacion(**base, estado=RECHAZADA, motivo=motivo, detail=detail, registro=registro)


def _registrar(
    db: Session,
    usuario: Usuario,
    item: ItemOperacion,
    operacion: Operacion,
    estado: str,
    resultado: ResultadoOperacion,
    en: datetime,
    actividad: Optional[Actividad],
) -> ResultadoOperacion:
    ahora = ahora_utc()
    db.add(
        OperacionSincronizacion(
            id_operacion=item.id_operacion,
            id_usuario=usuario.id_usuario,
            id_actividad=actividad.id_actividad if actividad is not None else None,
            modulo=operacion.modulo,
            entidad=operacion.entidad,
            id_entidad=operacion.id_entidad,
            version_origen=item.version,
            estado=estado,
            motivo=resultado.motivo,
            respuesta=resultado.model_dump(mode="json"),
            fecha_realizacion=en,
            recibida_en=ahora,
        )
    )
    db.exec(delete(OperacionPendiente).where(OperacionPendiente.id_operacion == item.id_operacion))
    if actividad is not None:
        actividad.sincronizado_en = ahora
        db.add(actividad)
    db.flush()
    return resultado


def procesar_operacion(
    db: Session,
    usuario: Usuario,
    item: ItemOperacion,
    fn_aplicar: Callable[[ContextoOperacion], Optional[dict[str, Any]]],
    operacion: Operacion,
) -> ResultadoOperacion:
    """Aplica una operación de escritura de forma idempotente (CU017-CU022).

    1. Si `id_operacion` ya se procesó, devuelve la respuesta guardada sin aplicar nada.
    2. Valida la hora real (`realizado_en`, por defecto ahora) y que caiga dentro de una
       actividad del docente (la indicada o la activa): si no, rechazada
       (`fuera_de_actividad`, `actividad_requerida`, `realizado_en_futuro`).
    3. Ejecuta `fn_aplicar(contexto)` en un SAVEPOINT. Devuelve el registro; para
       rechazar, lanza `RechazoOperacion` (o un `ErrorNegocio` 4xx); para descartar
       ante un valor más reciente, `OperacionDescartada`.
    4. Registra el resultado y borra el aviso de pendiente. Quien llama hace el commit.

    Un error técnico se propaga sin registrar nada, para que el cliente reintente.
    """
    previa = db.get(OperacionSincronizacion, item.id_operacion)
    if previa is not None:
        if previa.id_usuario != usuario.id_usuario:
            return ResultadoOperacion(
                id_operacion=item.id_operacion, estado=RECHAZADA, motivo=OPERACION_AJENA, detail=MENSAJE_OPERACION_AJENA
            )
        return ResultadoOperacion.model_validate(previa.respuesta)

    ahora = ahora_utc()
    en, actividad = item.realizado_en or ahora, None
    try:
        en = _instante_de(item, ahora)
        actividad = _actividad_de_la_operacion(
            db, usuario, item.id_actividad, en, operacion.mensaje_sin_actividad or MENSAJE_ACTIVIDAD_REQUERIDA
        )
    except RechazoOperacion as rechazo:
        resultado = _rechazada({"id_operacion": item.id_operacion}, rechazo.motivo, rechazo.detail)
        return _registrar(db, usuario, item, operacion, RECHAZADA, resultado, en, None)

    contexto = ContextoOperacion(
        en=en, id_actividad=actividad.id_actividad if actividad is not None else None, version=item.version
    )
    estado, resultado = _aplicar(db, fn_aplicar, contexto, item.id_operacion)
    return _registrar(db, usuario, item, operacion, estado, resultado, en, actividad)


# ── Endpoints ───────────────────────────────────────────────────────────────────────

def registrar_pendientes(db: Session, usuario: Usuario, items: list[PendienteInformado]) -> int:
    """Guarda los avisos nuevos del usuario. Idempotente: ignora los repetidos (ON
    CONFLICT) y los `id_operacion` ya procesados. Devuelve cuántos se guardaron."""
    ids = {i.id_operacion for i in items}
    procesadas = set(
        db.exec(select(OperacionSincronizacion.id_operacion).where(col(OperacionSincronizacion.id_operacion).in_(ids))).all()
    ) if ids else set()
    ahora = ahora_utc()
    filas = {
        i.id_operacion: {
            "id_operacion": i.id_operacion,
            "id_usuario": usuario.id_usuario,
            "id_dispositivo": i.id_dispositivo,
            "id_actividad": i.id_actividad,
            "modulo": i.modulo,
            "fecha_realizacion": i.realizado_en,
            "informado_en": ahora,
        }
        for i in items
        if i.id_operacion not in procesadas
    }
    if not filas:
        return 0
    tabla = OperacionPendiente.__table__
    sentencia = insert_con_conflicto(db, tabla).values(list(filas.values()))
    resultado = db.exec(sentencia.on_conflict_do_nothing(index_elements=["id_operacion"]))
    db.commit()
    return resultado.rowcount


def _estado_consultado(operacion: OperacionSincronizacion) -> str:
    """El estado que ve el cliente: el de su respuesta (una descartada por un registro más
    reciente se respondió como aceptada), o `descartada` si el usuario descartó un rechazo."""
    respuesta = operacion.respuesta or {}
    estado = respuesta.get("estado", operacion.estado)
    return DESCARTADA if operacion.estado == DESCARTADA and estado == RECHAZADA else estado


def consultar_operacion(db: Session, usuario: Usuario, id_operacion: uuid.UUID) -> EstadoOperacion:
    """El resultado de una operación propia; las ajenas o no recibidas, `desconocida`."""
    operacion = db.get(OperacionSincronizacion, id_operacion)
    if operacion is None or operacion.id_usuario != usuario.id_usuario:
        return EstadoOperacion(estado=DESCONOCIDA)
    return EstadoOperacion(
        estado=_estado_consultado(operacion), motivo=operacion.motivo, respuesta=operacion.respuesta or {}
    )


def descartar_operacion(db: Session, usuario: Usuario, id_operacion: uuid.UUID) -> EstadoOperacion:
    """"Cargar versión actual" / "Descartar" de los CU: una operación propia `rechazada`
    pasa a `descartada` y deja de contar como error en el estado de su actividad y del
    docente. 404 `operacion_no_encontrada` si no existe o es ajena; 409
    `operacion_no_rechazada` si no estaba rechazada."""
    operacion = db.exec(
        select(OperacionSincronizacion).where(OperacionSincronizacion.id_operacion == id_operacion).with_for_update()
    ).first()
    if operacion is None or operacion.id_usuario != usuario.id_usuario:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, MENSAJE_OPERACION_NO_ENCONTRADA, motivo="operacion_no_encontrada")
    if operacion.estado != RECHAZADA:
        raise ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_NO_RECHAZADA, motivo="operacion_no_rechazada")
    operacion.estado = DESCARTADA
    db.add(operacion)
    db.commit()
    return EstadoOperacion(estado=DESCARTADA, motivo=operacion.motivo, respuesta=operacion.respuesta or {})
