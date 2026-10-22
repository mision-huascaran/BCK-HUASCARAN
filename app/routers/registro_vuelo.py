"""Registro de Vuelo (CU031, CU032).

Docente: consulta en su alcance de lectura y escribe con actividad y habilitación del
módulo `registro_vuelo` de cada PA. Supervisor: consulta todo, en solo lectura (403 al
escribir). Directivo: 403 en todo el módulo.
"""
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.registro_vuelo import MAX_REGISTROS_POR_LOTE, RegistrosVueloDelGrupo, RegistroVueloGuardar
from app.schemas.sincronizacion import ResultadoOperacion
from app.services import registro_vuelo
from app.services.registro_vuelo import Contexto
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(prefix="/registro-vuelo", tags=["registro-vuelo"])

Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=RegistrosVueloDelGrupo)
def listar_registros(
    db: Db,
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_colegio: int,
    id_programa: int,
    id_grado: int,
    id_seccion: int,
    id_anio_escolar: int,
    page: Pagina = 1,
):
    """Una fila por alumno que perteneció al grupo en el año (incluidos los hoy
    inactivos), por apellidos, en páginas de 40, con los 4 PA resueltos: entrada,
    sugerencia (con su rúbrica de referencia), nivel colocado, nivel del programa,
    proyección, observación y avisos; más `periodos` con las fechas del calendario del
    colegio, su estado y `escritura` por PA. Sin alumnos: `items` vacío y `aviso`.

    Errores: 403 `aula_fuera_de_alcance`; 404 `colegio_no_encontrado`,
    `anio_no_encontrado`; 422 `seccion_inexistente`, `programa_inexistente`.
    """
    contexto = Contexto(id_colegio, id_grado, id_seccion, id_programa)
    return registro_vuelo.listar(db, usuario, contexto, id_anio_escolar, page)


@router.put("", response_model=list[ResultadoOperacion])
def guardar_registros(
    db: Db,
    docente: Annotated[Usuario, Depends(require_role(DOCENTE))],
    items: Annotated[list[RegistroVueloGuardar], Body(min_length=1, max_length=MAX_REGISTROS_POR_LOTE)],
):
    """Registra o corrige el nivel colocado, la proyección y la observación. Cada ítem es
    una operación con su resultado: `aceptada` (con el PA resuelto en `registro`) o
    `rechazada` (`modificacion_concurrente` con el vigente, `nivel_invalido`,
    `nivel_colocado_obligatorio`, `periodo_futuro`, `periodo_inexistente`,
    `alumno_fuera_del_grupo`, `sin_habilitacion`, `aula_fuera_de_alcance`,
    `actividad_requerida`, `fuera_de_actividad`). Enviar entrada, sugerencia o nivel del
    programa es 422 `validacion`."""
    return registro_vuelo.guardar(db, docente, items)
