"""Rúbrica mensual (CU029, CU030).

Una evaluación por alumno y mes con dos dimensiones en códigos 0-4 (0 Pre-inicio, 1
Inicio, 2 En proceso, 3 Logrado, 4 Avanzado); null = "Sin evaluar" (≠ 0). Las
escrituras son operaciones (`id_operacion`, `version`, `realizado_en`): ver
app/schemas/sincronizacion.py.
"""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from app.core.paginacion import Paginado
from app.schemas.asistencia import Escritura
from app.schemas.comun import ParcheSinNulos
from app.schemas.sincronizacion import ItemOperacion

EstadoRubrica = Literal["pendiente", "incompleta", "completada"]
Mes = Annotated[int, Field(ge=1, le=12)]
MAX_RUBRICAS_POR_LOTE = 200


class RubricaAlumno(BaseModel):
    id_alumno: int
    nombre: str
    # Hoy inactivo, pero le corresponde la evaluación de ese mes.
    inactivo: bool
    # null si todavía no tiene evaluación guardada.
    id_rubrica: Optional[int] = None
    fluidez: Optional[int] = None
    comprension: Optional[int] = None
    estado: EstadoRubrica
    # 4·F + 6·C (F × 0,40 + C × 0,60 en décimas); null si falta una dimensión.
    resultado_decimas: Optional[int] = None
    # null si todavía no tiene evaluación guardada.
    version: Optional[int] = None


class RubricasDelMes(Paginado[RubricaAlumno]):
    ciclo: str
    # PA del mes (REGLA PROVISIONAL R1: el de su último día lectivo).
    id_periodo_academico: int
    escritura: Escritura
    # "No hay alumnos que corresponda evaluar en este grupo y mes." si no hay ninguno.
    detail: Optional[str] = None


class RubricaGuardar(ItemOperacion, ParcheSinNulos):
    """Una evaluación. Una dimensión omitida no cambia; en null se borra ("Sin evaluar")."""

    CAMPOS_NULABLES = frozenset({"version", "realizado_en", "id_actividad", "fluidez", "comprension"})

    id_alumno: int
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int
    id_anio_escolar: int
    mes: Mes
    fluidez: Optional[int] = None
    comprension: Optional[int] = None


class Criterio(BaseModel):
    dimension: Literal["Fluidez", "Comprensión"]
    valor: int
    nombre: str
    descriptor: str
