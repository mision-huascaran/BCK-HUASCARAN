"""Colegios (CU013). Los gestiona solo el Supervisor; el Docente lee los de su alcance."""
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, Field

from app.schemas.comun import ItemCatalogo, ParcheSinNulos, Texto

NIVEL_EDUCATIVO_POR_DEFECTO = "Primaria"
SECCION_POR_DEFECTO = "Única"


class GradoOfrecido(BaseModel):
    """Un grado del colegio con su día de clase semanal (CU013)."""

    id_grado: int
    # 1 = lunes … 5 = viernes. Obligatorio.
    dia_clase: int = Field(ge=1, le=5)


def _sin_grados_repetidos(grados: list[GradoOfrecido]) -> list[GradoOfrecido]:
    ids = [g.id_grado for g in grados]
    if len(set(ids)) != len(ids):
        raise ValueError("No se puede repetir un grado.")
    return grados


# Al menos uno y sin repetir: cada grado tiene un solo día de clase.
GradosOfrecidos = Annotated[list[GradoOfrecido], Field(min_length=1), AfterValidator(_sin_grados_repetidos)]


# ── Requests ────────────────────────────────────────────────────────────────────────

class ColegioCrear(BaseModel):
    nombre: Texto
    departamento: Texto
    provincia: Texto
    distrito: Optional[Texto] = None
    nivel_educativo: Texto = NIVEL_EDUCATIVO_POR_DEFECTO
    seccion: Texto = SECCION_POR_DEFECTO
    grados: GradosOfrecidos
    # Ids de catálogo; al menos uno. Los repetidos se ignoran.
    programas: list[int] = Field(min_length=1)


class ColegioEditar(ParcheSinNulos):
    """Campos opcionales. `grados` y `programas` son la lista completa (reemplazan a la
    actual). `distrito` se puede vaciar con null; el resto, no."""

    CAMPOS_NULABLES = frozenset({"distrito"})

    nombre: Optional[Texto] = None
    departamento: Optional[Texto] = None
    provincia: Optional[Texto] = None
    distrito: Optional[Texto] = None
    nivel_educativo: Optional[Texto] = None
    seccion: Optional[Texto] = None
    grados: Optional[GradosOfrecidos] = None
    programas: Optional[list[int]] = Field(default=None, min_length=1)


# ── Responses ───────────────────────────────────────────────────────────────────────

class ColegioItem(BaseModel):
    id: int
    nombre: str
    nivel_educativo: str
    departamento: str
    provincia: str
    # Opcional (CU013): null si no se conoce.
    distrito: Optional[str] = None
    seccion: str
    activo: bool


class GradoColegio(ItemCatalogo):
    # 1 = lunes … 5 = viernes; null en los colegios cargados antes de pedirse.
    dia_clase: Optional[int] = None


class ColegioDetalle(ColegioItem):
    grados: list[GradoColegio]
    programas: list[ItemCatalogo]


class Ubicaciones(BaseModel):
    departamentos: list[str]
    distritos: list[str]
