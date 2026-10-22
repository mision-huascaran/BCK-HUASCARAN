"""Colegios (CU013). Los gestiona solo el Supervisor; el Docente lee los de su alcance."""
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field

from app.schemas.comun import ItemCatalogo, ParcheSinNulos, Texto

NIVEL_EDUCATIVO_POR_DEFECTO = "Primaria"
# Esta versión solo admite Primaria (CU013): cualquier otro valor es un 422.
NivelEducativo = Literal["Primaria"]


class GradoOfrecido(BaseModel):
    """Un grado del colegio con su día de clase semanal y sus secciones (CU013)."""

    id_grado: int
    # 1 = lunes … 5 = viernes. Obligatorio.
    dia_clase: int = Field(ge=1, le=5)
    # Nombres de secciones a agregar. Sin secciones, un grado nuevo nace con "Única". Al
    # editar, las que no existen se agregan y las existentes se conservan aunque no
    # vengan (no hay renombrar ni eliminar).
    secciones: list[Texto] = []


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
    nivel_educativo: NivelEducativo = NIVEL_EDUCATIVO_POR_DEFECTO
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
    nivel_educativo: Optional[NivelEducativo] = None
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
    activo: bool


class GradoColegio(ItemCatalogo):
    # 1 = lunes … 5 = viernes; null en los colegios cargados antes de pedirse.
    dia_clase: Optional[int] = None
    # Secciones activas del grado, en el orden en que se crearon.
    secciones: list[ItemCatalogo]


class ColegioDetalle(ColegioItem):
    grados: list[GradoColegio]
    programas: list[ItemCatalogo]


class EstadoCalendario(BaseModel):
    """En qué periodo está hoy el colegio según su calendario (CU016, CU027)."""

    tipo: Literal["PA", "PR"]
    numero: int
    # En el PR4 no existe "Próximo": el front lo deshabilita.
    es_pr4: bool
    fecha_fin: date


class ColegioEstadoCalendario(ItemCatalogo):
    """Colegio activo con su estado de calendario, para el modal de asignación: si está
    en un PR, el front muestra el bloque Retroactivo / Próximo. Null si hoy no cae en el
    calendario del año (p. ej. el año todavía no empezó) o el año no tiene calendario."""

    estado_calendario: Optional[EstadoCalendario] = None


class Ubicaciones(BaseModel):
    departamentos: list[str]
    distritos: list[str]
