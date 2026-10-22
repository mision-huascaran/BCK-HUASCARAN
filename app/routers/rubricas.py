"""Rúbrica mensual (CU029, CU030).

Docente: consulta en su alcance de lectura y escribe con actividad y habilitación del
módulo `rubrica` del PA del mes. Supervisor: consulta todo, en solo lectura (403 al
escribir). Directivo: 403 en todo el módulo.
"""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from sqlmodel import Session

from app.core.database import get_db
from app.core.paginacion import Pagina
from app.dependencies import require_role
from app.models.organizacion import Usuario
from app.schemas.rubrica import MAX_RUBRICAS_POR_LOTE, Criterio, RubricaGuardar, RubricasDelMes
from app.schemas.sincronizacion import ResultadoOperacion
from app.services import rubrica_service
from app.services.rubrica_service import GrupoMes
from app.services.usuario_service import DOCENTE, SUPERVISOR

router = APIRouter(prefix="/rubricas", tags=["rubricas"])

Db = Annotated[Session, Depends(get_db)]
Lector = Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))]
Docente = Annotated[Usuario, Depends(require_role(DOCENTE))]


@router.get("", response_model=RubricasDelMes)
def listar_rubricas(
    db: Db,
    usuario: Lector,
    id_colegio: int,
    id_programa: int,
    id_grado: int,
    id_seccion: int,
    id_anio_escolar: int,
    mes: Annotated[int, Query(ge=1, le=12)],
    page: Pagina = 1,
):
    """Los alumnos que corresponde evaluar en el grupo y mes (REGLA PROVISIONAL R2),
    incluidos los hoy inactivos, con su evaluación (`fluidez`, `comprension` en códigos
    0-4, `estado`, `resultado_decimas`), en páginas de 40; además el `ciclo`, el PA del
    mes (R1) y si el usuario puede escribir. Sin alumnos: `items` vacío y `detail`.

    Errores: 422 `mes_futuro`, `mes_sin_actividad`, `seccion_inexistente`,
    `programa_inexistente`; 403 `aula_fuera_de_alcance`; 404 `colegio_no_encontrado`,
    `anio_no_encontrado`.
    """
    grupo = GrupoMes(id_colegio, id_grado, id_seccion, id_programa, id_anio_escolar, mes)
    return rubrica_service.listar(db, usuario, grupo, page)


@router.put("", response_model=list[ResultadoOperacion])
def guardar_rubricas(
    db: Db,
    docente: Docente,
    items: Annotated[list[RubricaGuardar], Body(min_length=1, max_length=MAX_RUBRICAS_POR_LOTE)],
):
    """Registra o corrige evaluaciones (se pueden guardar incompletas). Cada ítem es una
    operación con su resultado: `aceptada` (con la evaluación en `registro`) o
    `rechazada` (`valor_no_permitido`, `sin_habilitacion`, `alumno_fuera_del_grupo`,
    `modificacion_concurrente` con la vigente, `mes_futuro`, `mes_sin_actividad`,
    `aula_fuera_de_alcance`, `actividad_requerida`, `fuera_de_actividad`). Un ítem sin
    cambios no escribe."""
    return rubrica_service.guardar(db, docente, items)


@router.get("/criterios", response_model=list[Criterio])
def listar_criterios(db: Db, _: Lector, id_programa: int, id_ciclo: int):
    """Niveles de la rúbrica del subprograma con su descriptor para el ciclo. Errores: 422
    `programa_inexistente`, `ciclo_inexistente`."""
    return rubrica_service.criterios(db, id_programa, id_ciclo)
