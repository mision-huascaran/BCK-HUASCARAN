from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.organizacion import Usuario
from app.schemas.alumno import AlumnoCreate, AlumnoPagina, AlumnoResponse, AlumnoUpdate
from app.services.alumno_service import (
    AlumnoNoExiste,
    ColegioNoExiste,
    FueraDeTuAlcance,
    GradoNoExiste,
    ProgramaNoExiste,
    actualizar_alumno,
    crear_alumno,
    listar_alumnos,
)
from app.services.asignacion_service import alcance_del_docente
from app.services.usuario_service import nombre_rol

router = APIRouter(tags=["alumnos"])

ERROR_FUERA_DE_ALCANCE = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Ese alumno no pertenece a un colegio y grado que tengas asignado",
)


def _alcance_si_es_docente(db: Session, usuario_actual: Usuario) -> Optional[list]:
    """Pares (colegio, grado) del docente, o None si quien pregunta no es docente.

    `None` significa "sin recorte"; una lista vacia significa "no tiene nada a cargo",
    que no es lo mismo y no debe confundirse.
    """
    if usuario_actual.id_docente is None:
        return None
    return alcance_del_docente(db, usuario_actual.id_docente)


@router.post("/alumnos", response_model=AlumnoResponse, status_code=status.HTTP_201_CREATED)
def crear_alumno_endpoint(
    data: AlumnoCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Docente")),
):
    """Alta de alumno. La hace el Docente, sobre sus colegios y grados asignados."""
    try:
        return crear_alumno(
            db, data, usuario_actual, alcance=_alcance_si_es_docente(db, usuario_actual)
        )
    except FueraDeTuAlcance:
        raise ERROR_FUERA_DE_ALCANCE
    except ColegioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un colegio con id_colegio={data.id_colegio}",
        )
    except GradoNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un grado con id_grado={data.id_grado}",
        )
    except ProgramaNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un programa con id_programa_actual={data.id_programa_actual}",
        )


@router.get("/alumnos", response_model=AlumnoPagina)
def listar_alumnos_endpoint(
    colegio: Optional[int] = Query(default=None, description="Filtra por id_colegio"),
    grado: Optional[int] = Query(default=None, description="Filtra por id_grado"),
    programa: Optional[int] = Query(default=None, description="Filtra por id_programa_actual"),
    q: Optional[str] = Query(default=None, description="Busca en nombres y apellidos"),
    activo: Optional[bool] = Query(default=None, description="Filtra por estado"),
    limit: int = Query(default=50, ge=1, le=500, description="Alumnos por página"),
    offset: int = Query(default=0, ge=0, description="Alumnos a saltar"),
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(get_current_user),
):
    """Listado de alumnos, recortado segun quien pregunta.

    - Docente: solo los de sus colegios y grados asignados vigentes.
    - Directivo: todos, pero sin nombres ni apellidos (vista ejecutiva).
    - Supervisor: todos, completos, en solo lectura.
    """
    es_directivo = nombre_rol(db, usuario_actual.id_rol) == "Directivo"

    # Buscar por nombre estando anonimizado permitiria deducir quien es cada id
    # probando nombres y mirando que filas vuelven, asi que el filtro se rechaza
    # en vez de ignorarse en silencio.
    if es_directivo and q:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La vista de Directivo no permite buscar por nombre",
        )

    total, items = listar_alumnos(
        db,
        colegio=colegio,
        grado=grado,
        programa=programa,
        q=q,
        activo=activo,
        alcance=_alcance_si_es_docente(db, usuario_actual),
        limit=limit,
        offset=offset,
    )

    respuesta = [AlumnoResponse.model_validate(a) for a in items]
    if es_directivo:
        for alumno in respuesta:
            alumno.nombres = None
            alumno.apellidos = None

    return AlumnoPagina(total=total, limit=limit, offset=offset, items=respuesta)


@router.patch("/alumnos/{id_alumno}", response_model=AlumnoResponse)
def actualizar_alumno_endpoint(
    id_alumno: int,
    data: AlumnoUpdate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Docente")),
):
    """Edicion de alumno, incluida la baja logica con `{"activo": false}`.

    No hay DELETE a proposito: el borrado definitivo de alumnos esta descartado.
    """
    try:
        return actualizar_alumno(
            db, id_alumno, data, usuario_actual,
            alcance=_alcance_si_es_docente(db, usuario_actual),
        )
    except AlumnoNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un alumno con id_alumno={id_alumno}",
        )
    except FueraDeTuAlcance:
        raise ERROR_FUERA_DE_ALCANCE
    except ColegioNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un colegio con id_colegio={data.id_colegio}",
        )
    except GradoNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un grado con id_grado={data.id_grado}",
        )
    except ProgramaNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un programa con id_programa_actual={data.id_programa_actual}",
        )
