"""Asistencia por clase de los alumnos (CU022).

La clase es colegio × grado × sección en una fecha; el subprograma es del alumno (el
filtro de subprograma es visual). Docente: lee en su alcance de lectura y registra con
actividad y habilitación. Supervisor: solo lectura, de todos los colegios. Directivo: 403.
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.asistencia import AsistenciaClase, AsistenciaGuardada, AsistenciaGuardar
from app.services import asistencia_service
from app.services.programacion import FiltroGrupos
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["asistencias"])

Db = Annotated[Session, Depends(get_db)]


@router.get("/asistencias", response_model=AsistenciaClase)
def consultar_asistencia(
    db: Db,
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_colegio: int,
    id_grado: int,
    id_seccion: int,
    fecha: date,
):
    """La clase del grupo en la fecha (`clase`, con su etiqueta y avisos), su lista de
    alumnos (la congelada si ya tiene asistencias; si no, los del grupo en esa fecha),
    por apellidos y nombres, y si el usuario puede escribir (`escritura`).

    Errores: 403 `aula_fuera_de_alcance` (Docente), 404 `colegio_no_encontrado`, 422
    `seccion_inexistente`, `fecha_futura`, `fecha_en_receso`, `fecha_no_lectiva` y
    `fecha_fuera_de_periodo` (con el mensaje para mostrar en `detail`).
    """
    return asistencia_service.consultar_clase(db, usuario, FiltroGrupos(id_colegio, id_grado, id_seccion), fecha)


@router.put("/asistencias", response_model=AsistenciaGuardada)
def guardar_asistencia(
    data: AsistenciaGuardar, db: Db, docente: Annotated[Usuario, Depends(require_role(DOCENTE))]
):
    """Registra el estado o la observación de uno o varios alumnos de la clase. Cada
    registro es una operación idempotente (`id_operacion`) con su resultado: `aceptada`
    (con el `registro` guardado; `motivo: registro_mas_reciente` si ganó una captura
    posterior y no se aplicó) o `rechazada` (`fecha_futura`, `fecha_en_receso`,
    `fecha_no_lectiva`, `fecha_fuera_de_periodo`, `sin_habilitacion`,
    `fuera_de_actividad`, `actividad_requerida`, `sin_clase_programada`,
    `alumno_fuera_de_la_clase`, `realizado_en_futuro`).

    Errores del lote: 403 `aula_fuera_de_alcance`, 404 `clase_no_encontrada`, 422
    `seccion_inexistente` y `validacion`.
    """
    return asistencia_service.guardar_asistencia(db, docente, data)
