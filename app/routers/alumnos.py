"""Alumnos (CU014) y su vista de detalle (CU015).

Supervisor: todos los alumnos. Docente: los de su alcance; sus escrituras requieren una
actividad activa. Directivo: sin acceso (403).
"""
import uuid
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import AfterValidator
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina, Paginado
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.alumno import (
    AlumnoCrear,
    AlumnoDetalle,
    AlumnoEditar,
    AlumnoItem,
    CambioEstadoAlumno,
    EventoHistorial,
    LecturaDeLaSemana,
    PeriodoDeVuelo,
    Resumen,
    Rubrica,
)
from app.schemas.importacion import ResultadoImportacion, VistaPreviaImportacion
from app.services import alumno_detalle_service as detalle
from app.services import alumno_service, importacion_service
from app.services.importacion_nomina import TAMANO_MAXIMO, archivo_invalido
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(tags=["alumnos"])

Db = Annotated[Session, Depends(get_db)]
ConAcceso = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]
SoloSupervisor = Annotated[Usuario, Depends(require_role(SUPERVISOR))]
MAXIMO_NOMBRE_ARCHIVO = 255
ESPACIO = 32  # Los códigos menores son caracteres de control.


def _nombre_base(nombre: str) -> str:
    """Solo el nombre del archivo, sin rutas ni espacios alrededor."""
    base = nombre.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base or len(base) > MAXIMO_NOMBRE_ARCHIVO or any(ord(c) < ESPACIO for c in base):
        raise ValueError(f"Indica un nombre de archivo válido, de hasta {MAXIMO_NOMBRE_ARCHIVO} caracteres.")
    return base


NombreArchivo = Annotated[str, AfterValidator(_nombre_base), Query(description="Nombre del archivo elegido")]
HuellaImportacion = Annotated[str, Query(pattern=r"^[0-9a-f]{64}$", description="`huella` de la vista previa")]
CUERPO_XLSX = {
    "requestBody": {
        "required": True,
        "content": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}


async def _leer_cuerpo(request: Request) -> bytes:
    """El cuerpo crudo, con un tope: si se declara o se acumula más de 5 MB, no se sigue."""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > TAMANO_MAXIMO:
        raise archivo_invalido()
    acumulado = bytearray()
    async for trozo in request.stream():
        acumulado += trozo
        if len(acumulado) > TAMANO_MAXIMO:
            raise archivo_invalido()
    return bytes(acumulado)


# Declaradas antes de las rutas de /alumnos/{id_alumno}. Son `async` para leer el cuerpo por
# partes; lo que usa la base o abre el archivo corre en el threadpool.
@router.post(
    "/alumnos/importaciones/vista-previa", response_model=VistaPreviaImportacion, openapi_extra=CUERPO_XLSX
)
async def vista_previa_importacion(request: Request, db: Db, _: SoloSupervisor, nombre_archivo: NombreArchivo):
    """Analiza el .xlsx del cuerpo sin registrar nada. `nombre_archivo` no afecta el análisis."""
    return await run_in_threadpool(importacion_service.vista_previa, db, await _leer_cuerpo(request))


@router.post(
    "/alumnos/importaciones", response_model=ResultadoImportacion, status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": ResultadoImportacion, "description": "Ya se había aplicado"}},
    openapi_extra=CUERPO_XLSX,
)
async def confirmar_importacion(
    request: Request,
    response: Response,
    db: Db,
    supervisor: SoloSupervisor,
    id_importacion: uuid.UUID,
    huella: HuellaImportacion,
    nombre_archivo: NombreArchivo,
):
    """Registra la nómina si nada cambió desde la vista previa. Con un `id_importacion` ya
    aplicado responde 200 con su resultado, sin leer el archivo."""
    previo = await run_in_threadpool(importacion_service.resultado_previo, db, id_importacion, supervisor)
    if previo is not None:
        response.status_code = status.HTTP_200_OK
        return previo
    resultado, creada = await run_in_threadpool(
        importacion_service.confirmar, db, await _leer_cuerpo(request), id_importacion, huella, nombre_archivo, supervisor
    )
    if not creada:
        response.status_code = status.HTTP_200_OK
    return resultado


@router.get("/alumnos/importaciones/{id_importacion}", response_model=ResultadoImportacion)
def consultar_importacion(id_importacion: uuid.UUID, db: Db, _: SoloSupervisor):
    """Para saber si una confirmación que se quedó sin respuesta se aplicó."""
    return importacion_service.consultar(db, id_importacion)


@router.get("/alumnos", response_model=Paginado[AlumnoItem])
def listar_alumnos(
    db: Db,
    usuario: ConAcceso,
    id_colegio: Optional[int] = None,
    id_programa: Optional[int] = None,
    ciclo: Optional[Literal["III", "IV", "V"]] = None,
    id_grado: Optional[int] = None,
    id_seccion: Optional[int] = None,
    activo: bool = True,
    q: Annotated[Optional[str], Query(description="Busca en nombres y apellidos")] = None,
    pertenencia: Annotated[
        Optional[Literal["definida", "pendiente"]], Query(description="Sin este filtro, se listan todos")
    ] = None,
    page: Pagina = 1,
):
    """Ordenado por apellidos y nombres. El Docente solo recibe los de su alcance."""
    return alumno_service.listar_alumnos(
        db, usuario, id_colegio, id_programa, ciclo, id_grado, activo, q, page, id_seccion=id_seccion,
        estado_pertenencia=pertenencia,
    )


@router.post("/alumnos", response_model=AlumnoItem, status_code=status.HTTP_201_CREATED)
def crear_alumno(data: AlumnoCrear, db: Db, usuario: ConAcceso):
    return alumno_service.crear_alumno(db, data, usuario)


@router.get("/alumnos/{id_alumno}", response_model=AlumnoDetalle)
def obtener_alumno(id_alumno: int, db: Db, usuario: ConAcceso):
    """Cabecera de la vista de detalle."""
    return alumno_service.obtener_alumno(db, usuario, id_alumno)


@router.patch("/alumnos/{id_alumno}", response_model=AlumnoItem)
def editar_alumno(id_alumno: int, data: AlumnoEditar, db: Db, usuario: ConAcceso):
    return alumno_service.editar_alumno(db, id_alumno, data, usuario)


@router.patch("/alumnos/{id_alumno}/activar", response_model=AlumnoItem)
def activar_alumno(id_alumno: int, data: CambioEstadoAlumno, db: Db, usuario: ConAcceso):
    """Vuelve a su grupo desde `aplicar_desde` (hoy o antes). Con pertenencia pendiente,
    solo cambia el estado."""
    return alumno_service.cambiar_estado(db, id_alumno, True, data, usuario)


@router.patch("/alumnos/{id_alumno}/desactivar", response_model=AlumnoItem)
def desactivar_alumno(id_alumno: int, data: CambioEstadoAlumno, db: Db, usuario: ConAcceso):
    """Deja de pertenecer a su grupo desde `aplicar_desde` (hoy o antes). Con pertenencia
    pendiente, solo cambia el estado."""
    return alumno_service.cambiar_estado(db, id_alumno, False, data, usuario)


# ── Pestañas de la vista de detalle (año escolar en curso) ──────────────────────────

@router.get("/alumnos/{id_alumno}/resumen", response_model=Resumen)
def resumen(id_alumno: int, db: Db, usuario: ConAcceso):
    return detalle.resumen(db, alumno_service.alumno_visible(db, usuario, id_alumno))


@router.get("/alumnos/{id_alumno}/registro-vuelo", response_model=list[PeriodoDeVuelo])
def registro_vuelo(id_alumno: int, db: Db, usuario: ConAcceso):
    return detalle.registro_vuelo(db, alumno_service.alumno_visible(db, usuario, id_alumno))


@router.get("/alumnos/{id_alumno}/rubrica", response_model=Rubrica)
def rubrica(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.rubrica(db, id_alumno)


@router.get("/alumnos/{id_alumno}/lectura", response_model=list[LecturaDeLaSemana])
def lectura(id_alumno: int, db: Db, usuario: ConAcceso):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.lectura(db, id_alumno)


@router.get("/alumnos/{id_alumno}/historial", response_model=Paginado[EventoHistorial])
def historial(id_alumno: int, db: Db, usuario: ConAcceso, page: Pagina = 1):
    alumno_service.alumno_visible(db, usuario, id_alumno)
    return detalle.historial(db, id_alumno, page)
