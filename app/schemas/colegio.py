"""Colegios (CU013). Los gestiona solo el Supervisor; el Docente lee los de su alcance."""
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.comun import ItemCatalogo, ParcheSinNulos, Texto

NIVEL_EDUCATIVO_POR_DEFECTO = "Primaria"
SECCION_POR_DEFECTO = "Única"


# ── Requests ────────────────────────────────────────────────────────────────────────

class ColegioCrear(BaseModel):
    nombre: Texto
    departamento: Texto
    distrito: Texto
    provincia: Optional[Texto] = None
    nivel_educativo: Texto = NIVEL_EDUCATIVO_POR_DEFECTO
    seccion: Texto = SECCION_POR_DEFECTO
    # Ids de catálogo; al menos uno. Los repetidos se ignoran.
    grados: list[int] = Field(min_length=1)
    programas: list[int] = Field(min_length=1)


class ColegioEditar(ParcheSinNulos):
    """Campos opcionales. `grados` y `programas` son la lista completa (reemplazan a la
    actual). `provincia` se puede vaciar con null; el resto, no."""

    CAMPOS_NULABLES = frozenset({"provincia"})

    nombre: Optional[Texto] = None
    departamento: Optional[Texto] = None
    distrito: Optional[Texto] = None
    provincia: Optional[Texto] = None
    nivel_educativo: Optional[Texto] = None
    seccion: Optional[Texto] = None
    grados: Optional[list[int]] = Field(default=None, min_length=1)
    programas: Optional[list[int]] = Field(default=None, min_length=1)


# ── Responses ───────────────────────────────────────────────────────────────────────

class ColegioItem(BaseModel):
    id: int
    nombre: str
    nivel_educativo: str
    departamento: str
    provincia: Optional[str] = None
    # Nullable en BD por los colegios anteriores a v3; obligatorio para los nuevos.
    distrito: Optional[str] = None
    seccion: str
    activo: bool


class ColegioDetalle(ColegioItem):
    grados: list[ItemCatalogo]
    programas: list[ItemCatalogo]


class Ubicaciones(BaseModel):
    departamentos: list[str]
    distritos: list[str]
