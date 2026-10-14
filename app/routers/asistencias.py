"""Asistencia semanal de los alumnos (CU022).

Docente: lee y registra en las aulas de su alcance vigente (registrar exige actividad
activa). Supervisor: solo lectura, de cualquier colegio. Directivo: sin acceso (403).
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.asistencia import AsistenciaAula, AsistenciaGuardada, AsistenciaGuardar
from app.services import asistencia_service
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["asistencias"])

Db = Annotated[Session, Depends(get_db)]


@router.get("/asistencias", response_model=AsistenciaAula)
def consultar_asistencia(
    db: Db,
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_colegio: int,
    id_grado: int,
    id_programa: int,
    id_semana: int,
):
    """Asistencia de la semana para los alumnos activos del colegio, grado y subprograma,
    por apellidos y nombres, sin paginar. `asistio: null` = aún no se tomó.

    Errores: 403 `aula_fuera_de_alcance` (Docente), 422 `colegio_inexistente`,
    `grado_no_ofrecido`, `programa_no_ofrecido`, `semana_inexistente`.
    """
    return asistencia_service.consultar_aula(db, usuario, id_colegio, id_grado, id_programa, id_semana)


@router.put("/asistencias", response_model=AsistenciaGuardada)
def guardar_asistencia(
    data: AsistenciaGuardar, db: Db, docente: Annotated[Usuario, Depends(require_role(DOCENTE))]
):
    """Registra la asistencia de uno o varios alumnos en una semana (clic individual o
    "Marcar todos como presentes"), todo o nada.

    Errores: 409 `actividad_requerida`, 422 `semana_inexistente`, 409
    `semana_no_editable`, 422 `validacion` (registros vacíos, alumno repetido,
    inexistente, inactivo o fuera del alcance, `fecha_registro` en el futuro).
    """
    return asistencia_service.guardar_asistencia(db, docente, data)
