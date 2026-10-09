"""Actividad de trabajo del Docente (CU009, CU010).

Toda escritura de un Docente sobre datos de alumnos requiere una actividad activa; el
Supervisor no la necesita. La actividad se vincula a la auditoría de cada cambio.

Una sesión puede tener varias actividades seguidas, pero el docente solo una activa a la
vez (índice único parcial en la BD), y ninguna pasa de la expiración de su sesión.

Sin conexión (CU008, CU009) el cliente puede iniciar y finalizar actividades que llegan
después de un nuevo login: se registran en la sesión en que ocurrieron (`id_sesion`), ya
cerradas si esa sesión terminó, y un `finalizar` con la hora real corrige el cierre
automático que el servidor les haya puesto mientras tanto.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, fecha_lima
from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION
from app.models.organizacion import Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadItem, ActividadRespuesta
from app.services.alcance import alcance_docente, tenia_asignacion
from app.services.sesiones import (
    CIERRE_ACTIVIDAD_FORZADO,
    CIERRE_ACTIVIDAD_MANUAL,
    CIERRE_ACTIVIDAD_POR_EXPIRACION,
    CIERRE_POR_EXPIRACION,
    cerrar_sesiones_vencidas,
)
from app.services.usuario_service import DOCENTE, nombre_rol

# Tolerancia para el reloj del cliente: una hora enviada hasta 2 minutos en el futuro
# se acepta (relojes que se adelantan un poco); más allá es un dato inválido.
MARGEN_RELOJ = timedelta(minutes=2)

EN_CURSO = "en_curso"
FINALIZADA = "finalizada"

# Cierres que puso el servidor sin saber la hora real: un `finalizar` posterior los corrige.
CIERRES_AUTOMATICOS = (CIERRE_ACTIVIDAD_POR_EXPIRACION, CIERRE_ACTIVIDAD_FORZADO)


def a_item(actividad: Actividad) -> ActividadItem:
    return ActividadItem(
        id=actividad.id_actividad,
        id_sesion=actividad.id_sesion,
        inicio=actividad.inicio,
        fin=actividad.fin,
        tipo_cierre=actividad.tipo_cierre,
        estado=EN_CURSO if actividad.fin is None else FINALIZADA,
    )


def a_respuesta(db: Session, actividad: Actividad, sesion_peticion: Sesion) -> ActividadRespuesta:
    """Respuesta de iniciar y finalizar: la actividad, el vencimiento de la sesión de la
    petición y el de la sesión de la actividad (el mismo salvo en una de otra sesión)."""
    if actividad.id_sesion == sesion_peticion.id_sesion:
        expira_actividad = sesion_peticion.expira
    else:
        expira_actividad = db.exec(select(Sesion.expira).where(Sesion.id_sesion == actividad.id_sesion)).one()
    return ActividadRespuesta(
        actividad=a_item(actividad),
        sesion_expira=sesion_peticion.expira,
        actividad_sesion_expira=expira_actividad,
    )


def cerrar_vencidas_y_confirmar(db: Session, id_usuario: Optional[int] = None) -> None:
    """Cierra las sesiones vencidas (del usuario, o de todos sin `id_usuario`) y sus
    actividades, y lo confirma de inmediato: queda guardado aunque la petición termine
    en error."""
    if cerrar_sesiones_vencidas(db, id_usuario):
        db.commit()


def actividad_activa(db: Session, id_docente: Optional[int]) -> Optional[Actividad]:
    """La actividad activa (`fin IS NULL`) del docente cuya sesión siga abierta y vigente."""
    if id_docente is None:
        return None
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


def requerir_actividad_activa(db: Session, usuario: Usuario) -> Optional[uuid.UUID]:
    """`id_actividad` con el que se auditan los cambios de este usuario.

    - Supervisor (o cualquier rol que no sea Docente): None, no necesita actividad.
    - Docente: su actividad activa cuya sesión siga abierta y vigente. Antes se cierran
      sus sesiones vencidas (y con ellas sus actividades), para que una actividad
      "activa" de una sesión ya vencida no habilite escrituras. Ese cierre se confirma
      aunque la petición termine en 409.
      Sin actividad válida: 409 `actividad_requerida`.
    """
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return None
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    actividad = actividad_activa(db, usuario.id_docente)
    if actividad is None:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "Debe iniciar una actividad para realizar cambios.",
            motivo="actividad_requerida",
        )
    return actividad.id_actividad


# ── Iniciar ─────────────────────────────────────────────────────────────────────────

def _registrada(db: Session, id_actividad: uuid.UUID, id_docente: Optional[int]) -> Optional[Actividad]:
    """Idempotencia: la actividad ya registrada con ese id si es del docente; 409 si el
    id lo usa otro docente; None si el id está libre."""
    actividad = db.get(Actividad, id_actividad, populate_existing=True)
    if actividad is None:
        return None
    if actividad.id_docente != id_docente:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "El identificador de la actividad ya está en uso.",
            motivo="actividad_id_en_uso",
        )
    return actividad


def _ya_hay_una_activa() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_409_CONFLICT,
        "Debe finalizar la actividad actual antes de iniciar una nueva.",
        motivo="actividad_activa_existente",
    )


def _sin_asignaciones() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_409_CONFLICT,
        "No puede iniciar una actividad porque no tiene asignaciones activas.",
        motivo="sin_asignaciones",
    )


def _inicio_invalido(detail: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo="inicio_invalido")


def _sin_otra_activa(db: Session, id_docente: Optional[int], id_actividad: uuid.UUID) -> Optional[Actividad]:
    """La actividad activa del docente si es esta misma (un reintento simultáneo la
    registró después de la consulta de idempotencia); 409 si es otra; None si no hay."""
    activa = actividad_activa(db, id_docente)
    if activa is None or activa.id_actividad == id_actividad:
        return activa
    raise _ya_hay_una_activa()


def _guardar_nueva(db: Session, actividad: Actividad) -> tuple[Actividad, bool]:
    db.add(actividad)
    try:
        db.commit()
    except IntegrityError as error:
        # Dos inicios simultáneos que pasaron las validaciones a la vez: el mismo id
        # (reintento del cliente) o el índice de una sola actividad activa por docente.
        db.rollback()
        registrada = _registrada(db, actividad.id_actividad, actividad.id_docente)
        if registrada is not None:
            return registrada, False
        raise _ya_hay_una_activa() from error
    db.refresh(actividad)
    return actividad, True


def iniciar_actividad(
    db: Session,
    usuario: Usuario,
    sesion: Sesion,
    id_actividad: uuid.UUID,
    inicio: Optional[datetime],
    id_sesion: Optional[uuid.UUID] = None,
) -> tuple[Actividad, bool]:
    """Inicia una actividad (CU009) en la sesión de la petición o, con `id_sesion`, en
    otra sesión del mismo usuario (ver `_registrar_en_otra_sesion`).

    Devuelve (actividad, creada). `creada` es False cuando el id ya estaba registrado
    para este docente: se devuelve esa actividad sin cambios, esté activa o finalizada.
    """
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    registrada = _registrada(db, id_actividad, usuario.id_docente)
    if registrada is not None:
        return registrada, False
    if id_sesion is not None and id_sesion != sesion.id_sesion:
        return _registrar_en_otra_sesion(db, usuario, id_sesion, id_actividad, inicio)

    activa = _sin_otra_activa(db, usuario.id_docente, id_actividad)
    if activa is not None:
        return activa, False
    if usuario.id_docente is None or not alcance_docente(db, usuario.id_docente):
        raise _sin_asignaciones()

    ahora = ahora_utc()
    inicio = ahora if inicio is None else inicio.astimezone(timezone.utc)
    if not sesion.inicio <= inicio <= ahora + MARGEN_RELOJ:
        raise _inicio_invalido("La hora de inicio debe estar entre el inicio de la sesión y la hora actual.")

    return _guardar_nueva(
        db,
        Actividad(
            id_actividad=id_actividad,
            id_sesion=sesion.id_sesion,
            id_docente=usuario.id_docente,
            inicio=inicio,
            sincronizado_en=ahora,
        ),
    )


# ── Iniciar en otra sesión (sincronización después de un nuevo login) ────────────────

def _sesion_propia_bloqueada(db: Session, id_usuario: int, id_sesion: uuid.UUID) -> Sesion:
    """La sesión `id_sesion` del usuario, bloqueada (FOR UPDATE) para que un logout o
    una invalidación simultáneos esperen y dos registros en ella no se crucen. Abierta,
    cerrada o vencida. Inexistente o ajena: el mismo 422, para no revelar sesiones ajenas."""
    otra = db.exec(
        select(Sesion)
        .where(Sesion.id_sesion == id_sesion, Sesion.id_usuario == id_usuario)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if otra is None:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La sesión indicada no existe.",
            motivo="id_sesion_invalido",
        )
    return otra


def _cierre_heredado(otra: Sesion, ahora: datetime) -> tuple[Optional[datetime], Optional[str]]:
    """(fin, tipo_cierre) con que nace una actividad de esa sesión: (None, None) si la
    sesión sigue abierta y vigente; si no, el cierre que habría recibido estando en curso
    cuando la sesión terminó."""
    if otra.fin is None and otra.expira > ahora:
        return None, None
    if otra.fin is None or otra.tipo_cierre == CIERRE_POR_EXPIRACION:
        # Vencida (aunque el cierre perezoso aún no la haya marcado): en el instante en que venció.
        return otra.expira, CIERRE_ACTIVIDAD_POR_EXPIRACION
    # Logout o invalidación (restablecimiento de contraseña, desactivación): cuando se cerró.
    return otra.fin, CIERRE_ACTIVIDAD_FORZADO


def _validar_inicio_en(otra: Sesion, inicio: Optional[datetime], ahora: datetime) -> datetime:
    """`inicio` obligatorio y dentro de la sesión: desde su inicio hasta que terminó
    (o venció) y no más allá de la hora actual más el margen de reloj."""
    if inicio is None:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MENSAJE_GENERAL,
            motivo=MOTIVO_VALIDACION,
            extra={"errores": [{"campo": "inicio", "mensaje": "Es obligatorio si se indica id_sesion."}]},
        )
    inicio = inicio.astimezone(timezone.utc)
    termino = otra.expira if otra.fin is None else otra.fin
    if not otra.inicio <= inicio <= min(termino, ahora + MARGEN_RELOJ):
        raise _inicio_invalido(
            "La hora de inicio debe estar dentro de la sesión indicada y no ser posterior a la hora actual."
        )
    return inicio


def _verificar_sin_superposicion(
    db: Session, id_sesion: uuid.UUID, id_docente: int, inicio: datetime, fin: Optional[datetime]
) -> None:
    """409 si el intervalo [inicio, fin] (abierto si `fin` es None) se cruza con otra
    actividad del docente en la misma sesión. Tocarse en un extremo no es cruzarse."""
    filtro = [
        Actividad.id_sesion == id_sesion,
        Actividad.id_docente == id_docente,
        or_(col(Actividad.fin).is_(None), col(Actividad.fin) > inicio),
    ]
    if fin is not None:
        filtro.append(col(Actividad.inicio) < fin)
    if db.exec(select(Actividad.id_actividad).where(*filtro).limit(1)).first() is not None:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "La actividad se superpone con otra actividad de la misma sesión.",
            motivo="actividad_superpuesta",
        )


def _registrar_en_otra_sesion(
    db: Session, usuario: Usuario, id_sesion: uuid.UUID, id_actividad: uuid.UUID, inicio: Optional[datetime]
) -> tuple[Actividad, bool]:
    """Registra una actividad ocurrida en otra sesión del usuario (CU008, CU009): p. ej.
    iniciada sin conexión en una sesión que venció antes de sincronizar, o en otro
    dispositivo. La idempotencia ya se resolvió antes.

    - Sesión abierta y vigente: queda en curso (una sola en curso por docente).
    - Sesión terminada: nace cerrada, con `fin` y `tipo_cierre` en el mismo INSERT (no
      pasa nunca por "en curso", así que no choca con el índice de una sola activa).
    - El docente debía tener una asignación en el periodo del día (Lima) de `inicio`.
    - No puede cruzarse con otra actividad suya de esa sesión.
    """
    otra = _sesion_propia_bloqueada(db, usuario.id_usuario, id_sesion)
    ahora = ahora_utc()
    inicio = _validar_inicio_en(otra, inicio, ahora)
    fin, tipo_cierre = _cierre_heredado(otra, ahora)
    en_curso = fin is None
    if en_curso:
        activa = _sin_otra_activa(db, usuario.id_docente, id_actividad)
        if activa is not None:
            return activa, False
    # Lo que queda en curso habilita escrituras hoy: exige colegio activo, como el inicio
    # normal. Lo ya terminado es historia: basta con que tuviera la asignación ese día.
    if usuario.id_docente is None or not tenia_asignacion(
        db, usuario.id_docente, fecha_lima(inicio), solo_colegios_activos=en_curso
    ):
        raise _sin_asignaciones()
    _verificar_sin_superposicion(db, otra.id_sesion, usuario.id_docente, inicio, fin)

    return _guardar_nueva(
        db,
        Actividad(
            id_actividad=id_actividad,
            id_sesion=otra.id_sesion,
            id_docente=usuario.id_docente,
            inicio=inicio,
            fin=fin,
            tipo_cierre=tipo_cierre,
            sincronizado_en=ahora,
        ),
    )


# ── Finalizar ───────────────────────────────────────────────────────────────────────

def _fin_invalido() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "La hora de fin debe estar entre el inicio de la actividad y la hora actual, "
        "sin pasar la expiración de la sesión.",
        motivo="fin_invalido",
    )


def _es_correccion(actividad: Actividad, fin: Optional[datetime]) -> bool:
    """Si `fin` corrige un cierre que el servidor puso sin saber la hora real: la
    actividad se cerró por expiración o por cierre de sesión y el cliente informa que
    la finalizó antes (p. ej. sin conexión)."""
    return (
        fin is not None
        and actividad.fin is not None
        and actividad.tipo_cierre in CIERRES_AUTOMATICOS
        and fin.astimezone(timezone.utc) < actividad.fin
    )


def finalizar_actividad(
    db: Session, usuario: Usuario, id_actividad: uuid.UUID, fin: Optional[datetime]
) -> Actividad:
    """Finaliza una actividad del docente (CU009). No cierra la sesión.

    Si ya estaba finalizada, la devuelve sin cambios, salvo que la haya cerrado el
    servidor (expiración o cierre de sesión) y `fin` sea anterior a ese cierre: entonces
    se corrige a la hora real y queda como finalizada por el docente.
    """
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    # FOR UPDATE: un logout o una expiración simultáneos esperan a este cierre (o a esta
    # corrección) y después ya no la ven activa.
    actividad = db.exec(
        select(Actividad)
        .where(Actividad.id_actividad == id_actividad)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if actividad is None or actividad.id_docente != usuario.id_docente:
        raise ErrorNegocio(
            status.HTTP_404_NOT_FOUND, "La actividad no existe.", motivo="actividad_no_encontrada"
        )
    if actividad.fin is not None and not _es_correccion(actividad, fin):
        return actividad

    ahora = ahora_utc()
    expira = db.exec(select(Sesion.expira).where(Sesion.id_sesion == actividad.id_sesion)).one()
    fin = min(ahora, expira) if fin is None else fin.astimezone(timezone.utc)
    if not actividad.inicio <= fin <= min(expira, ahora + MARGEN_RELOJ):
        raise _fin_invalido()
    actividad.fin = fin
    actividad.tipo_cierre = CIERRE_ACTIVIDAD_MANUAL
    actividad.sincronizado_en = ahora
    db.add(actividad)
    db.commit()
    db.refresh(actividad)
    return actividad
