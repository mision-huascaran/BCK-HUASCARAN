"""Sincronización de operaciones hechas sin conexión (CU017-CU021). Solo Docente."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.sincronizacion import (
    MAX_PENDIENTES_POR_LOTE,
    EstadoOperacion,
    PendienteInformado,
    PendientesRegistrados,
)
from app.services import sincronizacion
from app.services.usuario_service import DOCENTE

router = APIRouter(prefix="/sincronizacion", tags=["sincronizacion"])

Db = Annotated[Session, Depends(get_db)]
Docente = Annotated[Usuario, Depends(require_role(DOCENTE))]


@router.post("/pendientes", response_model=PendientesRegistrados)
def informar_pendientes(
    db: Db,
    docente: Docente,
    items: Annotated[list[PendienteInformado], Body(max_length=MAX_PENDIENTES_POR_LOTE)],
):
    """Aviso de las operaciones creadas sin conexión que todavía no se enviaron. Se manda
    al reconectar, antes de las operaciones. Idempotente: los `id_operacion` repetidos o
    ya procesados se ignoran. Cuentan para el estado de la actividad y del docente
    (`pendiente`) hasta que la operación se procesa."""
    registrados = sincronizacion.registrar_pendientes(db, docente, items)
    return PendientesRegistrados(recibidos=len(items), registrados=registrados)


@router.get("/operaciones/{id_operacion}", response_model=EstadoOperacion)
def consultar_operacion(id_operacion: uuid.UUID, db: Db, docente: Docente):
    """Resultado de una operación enviada que se quedó sin respuesta: `aceptada`,
    `rechazada` (con su `motivo`) o `desconocida` (no llegó, o no es propia)."""
    return sincronizacion.consultar_operacion(db, docente, id_operacion)


@router.post("/operaciones/{id_operacion}/descartar", response_model=EstadoOperacion)
def descartar_operacion(id_operacion: uuid.UUID, db: Db, docente: Docente):
    """"Cargar versión actual" / "Descartar": una operación propia `rechazada` pasa a
    `descartada` y deja de contar como error en los estados de la actividad y del
    docente. Errores: 404 `operacion_no_encontrada` (no existe o es ajena), 409
    `operacion_no_rechazada`."""
    return sincronizacion.descartar_operacion(db, docente, id_operacion)
