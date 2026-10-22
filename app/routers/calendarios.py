"""Calendario académico (CU027).

Gestiona solo el Supervisor, con conexión y sin actividad de trabajo. El Docente puede
leer (solo GET) el calendario aplicable a sus colegios, para validar fechas sin conexión.
Directivo: 403.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.calendario import (
    CalendarioCrear,
    CalendarioEditar,
    CalendarioParticularCrear,
    CalendarioRespuesta,
    UsarGeneral,
)
from app.services import calendario_gestion
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(prefix="/calendarios", tags=["calendarios"])

Db = Annotated[Session, Depends(get_db)]
SoloSupervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]


@router.get("", response_model=CalendarioRespuesta)
def consultar_calendario(
    db: Db,
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_anio_escolar: Optional[int] = None,
    id_colegio: Optional[int] = None,
):
    """Calendario aplicable: sin `id_colegio`, el general del año; con él, el particular
    activo del colegio o, si no tiene, el general (`origen`). `id_anio_escolar` por
    defecto es el año de referencia. Incluye `version` (para editar) y `periodo_actual`
    (null = "Sin periodo aplicable a la fecha actual").

    Errores: 404 `calendario_no_configurado`, 422 `anio_inexistente` y
    `colegio_inexistente`, 403 `colegio_fuera_de_alcance` (Docente: solo sus colegios y
    con `id_colegio`).
    """
    return calendario_gestion.consultar(db, usuario, id_anio_escolar, id_colegio)


@router.post("", response_model=CalendarioRespuesta, status_code=status.HTTP_201_CREATED)
def crear_calendario_general(data: CalendarioCrear, db: Db, supervisor: SoloSupervisor):
    """Crea el calendario general del año: los 8 periodos (PA1, PR1, ..., PR4) y sus días
    no lectivos.

    Errores: 409 `calendario_existente`, 422 `anio_inexistente` y 422 `validacion` (con
    el campo afectado: periodos faltantes, repetidos, fuera de orden, superpuestos o con
    inicio > fin; choque con el año vecino; días no lectivos fuera de un PA o
    superpuestos; motivo vacío).
    """
    return calendario_gestion.crear_general(db, data, supervisor)


@router.post("/particulares", response_model=CalendarioRespuesta, status_code=status.HTTP_201_CREATED)
def crear_calendario_particular(data: CalendarioParticularCrear, db: Db, supervisor: SoloSupervisor):
    """Fechas particulares de un colegio para un año. Sin `dias_no_lectivos` se copian
    los del general.

    Errores: 409 `calendario_general_requerido`, `calendario_particular_existente` y
    `calendario_incompatible` (lista `incompatibilidades`), 422 `anio_inexistente`,
    `colegio_inexistente` y `validacion`.
    """
    return calendario_gestion.crear_particular(db, data, supervisor)


@router.put("/{id_calendario}", response_model=CalendarioRespuesta)
def editar_calendario(id_calendario: int, data: CalendarioEditar, db: Db, supervisor: SoloSupervisor):
    """Reemplaza periodos y días no lectivos del general o de un particular. Requiere la
    `version` leída; sin cambios reales no sube la versión ni audita.

    Errores: 404 `calendario_no_encontrado`, 409 `modificacion_concurrente`,
    `calendario_inactivo` y `calendario_incompatible` (en el general se evalúan todos
    los colegios que lo usan), 422 `validacion`.
    """
    return calendario_gestion.editar(db, id_calendario, data, supervisor)


@router.post("/{id_calendario}/usar-general", response_model=CalendarioRespuesta)
def usar_calendario_general(
    id_calendario: int,
    db: Db,
    supervisor: SoloSupervisor,
    data: Annotated[Optional[UsarGeneral], Body()] = None,
):
    """El colegio deja sus fechas particulares (se conservan como historial) y vuelve al
    general. Responde el calendario que le aplica desde ahora. Cuerpo opcional.

    Errores: 404 `calendario_no_encontrado`, 409 `calendario_es_general`,
    `calendario_inactivo`, `modificacion_concurrente` y `calendario_incompatible`.
    """
    version = data.version if data is not None else None
    return calendario_gestion.usar_general(db, id_calendario, version, supervisor)
