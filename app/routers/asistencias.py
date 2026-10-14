"""Asistencia por clase de los alumnos (CU022).

Una clase es fecha + colegio + grado + subprograma. Docente: lee y registra en las aulas
de su alcance vigente, en días lectivos del periodo vigente (registrar exige actividad
activa). Supervisor: solo lectura, de cualquier colegio y día lectivo ya llegado.
Directivo: sin acceso (403).
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
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["asistencias"])

Db = Annotated[Session, Depends(get_db)]


@router.get("/asistencias", response_model=AsistenciaClase)
def consultar_asistencia(
    db: Db,
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_colegio: int,
    id_grado: int,
    id_programa: int,
    fecha: date,
):
    """Asistencia de la clase: los alumnos activos del colegio, grado y subprograma, más
    los que tienen registro en ella aunque hoy estén inactivos (`activo: false`) o en otro
    subprograma; por apellidos y nombres, sin paginar. `estado: null` = sin marcar.
    `editable`: el usuario es Docente (la fecha ya es un día lectivo del periodo vigente).

    Errores: 403 `aula_fuera_de_alcance` (Docente), 422 `colegio_inexistente`,
    `grado_no_ofrecido`, `programa_no_ofrecido` y `fecha_no_lectiva` (Docente: fuera de
    un día lectivo del periodo vigente; Supervisor: fin de semana, feriado, fuera de todo
    periodo o futura).
    """
    return asistencia_service.consultar_clase(db, usuario, id_colegio, id_grado, id_programa, fecha)


@router.put("/asistencias", response_model=AsistenciaGuardada)
def guardar_asistencia(
    data: AsistenciaGuardar, db: Db, docente: Annotated[Usuario, Depends(require_role(DOCENTE))]
):
    """Registra el estado o la observación de uno o varios alumnos en la clase de la
    fecha (clic, modal o "Marcar todos como presentes"), todo o nada. Cada alumno va a la
    clase de su subprograma actual. Con dos dispositivos gana la captura más reciente: un
    registro con `fecha_registro` anterior a la guardada vuelve con `aplicado: false` y
    el estado vigente.

    Errores: 409 `actividad_requerida`, 422 `fecha_no_lectiva`, 422 `validacion`
    (registros vacíos o sin estado ni observación, estado null o no permitido, alumno
    repetido, inexistente, inactivo o fuera del alcance, `fecha_registro` en el futuro).
    """
    return asistencia_service.guardar_asistencia(db, docente, data)
