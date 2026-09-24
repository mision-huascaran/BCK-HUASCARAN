from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.asignacion import AsignacionCreate, AsignacionListItem, AsignacionResponse
from app.services.asignacion_service import (
    AsignacionDuplicada,
    AsignacionNoExiste,
    ColegioNoExiste,
    DocenteNoExiste,
    GradoNoExiste,
    PeriodoNoExiste,
    crear_asignacion,
    eliminar_asignacion,
    listar_asignaciones,
)

router = APIRouter(tags=["asignaciones"])


@router.get("/asignaciones", response_model=list[AsignacionListItem])
def listar_asignaciones_endpoint(
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor", "Docente")),
):
    """El Supervisor ve todas las asignaciones; el Docente, solo las suyas.

    El recorte se hace por `id_docente` del propio token, no por un parametro: asi un
    docente no puede pedir las asignaciones de otro cambiando la URL.

    El Directivo queda fuera (403): las asignaciones son la gestion de docentes, que no
    esta en su parcela, igual que GET /profesores.
    """
    if usuario_actual.id_docente is not None:
        return listar_asignaciones(db, id_docente=usuario_actual.id_docente)
    return listar_asignaciones(db)


@router.post("/asignaciones", response_model=AsignacionResponse, status_code=status.HTTP_201_CREATED)
def crear_asignacion_endpoint(
    data: AsignacionCreate,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        return crear_asignacion(db, data, usuario_actual)
    except DocenteNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un docente con id_docente={data.id_docente}",
        )
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
    except PeriodoNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un periodo académico con id_periodo_academico={data.id_periodo_academico}",
        )
    except AsignacionDuplicada:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese docente ya tiene asignado ese colegio y grado en ese periodo",
        )


@router.delete("/asignaciones/{id_asignacion}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_asignacion_endpoint(
    id_asignacion: int,
    db: Session = Depends(get_db),
    usuario_actual: Usuario = Depends(require_role("Supervisor")),
):
    try:
        eliminar_asignacion(db, id_asignacion)
    except AsignacionNoExiste:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe una asignación con id={id_asignacion}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
