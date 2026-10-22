"""Seguimiento de Lectura (CU023 a CU026).

Docente: consulta en su alcance de lectura y escribe con actividad y habilitación del
módulo `lectura`. Supervisor: consulta todo, en solo lectura (403 al escribir).
Directivo: 403 en todo el módulo.
"""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.lectura import (
    MAX_FILAS_POR_LOTE,
    ClaseReferenciaCambiar,
    FilaLecturaGuardar,
    NoRealizadaMarcar,
    TablaLectura,
    TablaLecturaCrear,
)
from app.schemas.sincronizacion import ResultadoOperacion
from app.services import lectura_service
from app.services.lectura_service import FiltroLectura
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(prefix="/lectura", tags=["lectura"])

Db = Annotated[Session, Depends(get_db)]
Lector = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]
Docente = Annotated[Usuario, Depends(require_role(DOCENTE))]


@router.get("/tablas", response_model=TablaLectura)
def consultar_tabla(
    db: Db,
    usuario: Lector,
    id_colegio: int,
    id_programa: int,
    id_grado: int,
    id_seccion: int,
    id_anio_escolar: int,
    mes: Annotated[int, Query(ge=1, le=12)],
    id_semana: int,
):
    """La tabla de lectura del grupo, subprograma y semana, con sus filas (la lista
    histórica de la clase de referencia con ese subprograma) y si el usuario puede
    escribir (`escritura`).

    Errores: 404 `tabla_no_existe` (con `clase_referencia_sugerida` y `escritura`, lo
    necesario para crearla), 422 `semana_sin_clase`, 403 `aula_fuera_de_alcance`, 404
    `colegio_no_encontrado` y `semana_no_encontrada`, 422 `seccion_inexistente` y
    `programa_inexistente`.
    """
    filtro = FiltroLectura(id_colegio, id_programa, id_grado, id_seccion, id_anio_escolar, mes, id_semana)
    return lectura_service.consultar_tabla(db, usuario, filtro)


@router.post("/tablas", response_model=TablaLectura, status_code=status.HTTP_201_CREATED)
def crear_tabla(data: TablaLecturaCrear, db: Db, docente: Docente):
    """Crea la tabla con una fila vacía por alumno de la lista de la clase de referencia
    (por defecto, la habitual o reprogramada de la semana; si no tiene fila, se crea y
    se congela su lista). Idempotente por `id_operacion`.

    Errores: 409 `tabla_existente` (con su `id`); 422 `semana_futura`,
    `clase_no_realizada`, `semana_sin_clase`, `clase_fuera_de_la_semana`,
    `clase_referencia_requerida`, `sin_alumnos`, `sin_habilitacion`,
    `actividad_requerida`, `fuera_de_actividad`; además los de la consulta.
    """
    return lectura_service.crear_tabla(db, docente, data)


@router.patch("/tablas/{id_tabla}/clase-referencia", response_model=TablaLectura)
def cambiar_clase_referencia(id_tabla: int, data: ClaseReferenciaCambiar, db: Db, docente: Docente):
    """Cambia la clase de referencia a otra del grupo en la misma semana y mes (por
    `id_clase` o por `fecha`), solo sin datos de lectura y si no está "No realizada".

    Errores: 404 `tabla_no_encontrada`; 409 `modificacion_concurrente`,
    `tabla_con_datos`, `lectura_no_realizada`, `clase_ya_usada`; 422
    `clase_fuera_de_la_semana`, `clase_no_realizada`, `sin_alumnos`, `sin_habilitacion`.
    """
    return lectura_service.cambiar_clase_referencia(db, docente, id_tabla, data)


@router.put("/tablas/{id_tabla}/filas", response_model=list[ResultadoOperacion])
def guardar_filas(
    id_tabla: int,
    db: Db,
    docente: Docente,
    items: Annotated[list[FilaLecturaGuardar], Body(min_length=1, max_length=MAX_FILAS_POR_LOTE)],
):
    """Registra o corrige filas (LSN, LSL, observación). Cada ítem es una operación con su
    resultado: `aceptada` (con la fila actualizada en `registro`) o `rechazada`
    (`sin_asistencia`, `lsn_incompleto`, `lsn_invalido`, `alumno_fuera_de_la_tabla`,
    `lectura_no_realizada`, `modificacion_concurrente` con la fila vigente,
    `sin_habilitacion`, `actividad_requerida`, `fuera_de_actividad`).

    Errores del lote: 404 `tabla_no_encontrada`, 403 `aula_fuera_de_alcance`, 422
    `validacion` (p. ej. `cantidad_lsl` negativa o no entera).
    """
    return lectura_service.guardar_filas(db, docente, id_tabla, items)


@router.post("/tablas/{id_tabla}/no-realizada", response_model=TablaLectura)
def marcar_no_realizada(id_tabla: int, data: NoRealizadaMarcar, db: Db, docente: Docente):
    """Marca la tabla como "No realizada" con su motivo: bloquea los datos de lectura y
    las observaciones.

    Errores: 404 `tabla_no_encontrada`; 422 `validacion` (motivo vacío),
    `sin_habilitacion`, `actividad_requerida`; 409 `tabla_con_datos`,
    `modificacion_concurrente`.
    """
    return lectura_service.marcar_no_realizada(db, docente, id_tabla, data)
