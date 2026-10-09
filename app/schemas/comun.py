"""Tipos y piezas compartidas por los schemas de la API."""
from typing import Annotated, ClassVar

from pydantic import AfterValidator, BaseModel, StringConstraints, ValidationInfo, field_validator

# Texto obligatorio: sin espacios extremos y no vacío.
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _validar_dni(valor: str) -> str:
    valor = valor.strip()
    if len(valor) != 8 or not valor.isdigit():
        raise ValueError("Debe tener exactamente 8 dígitos.")
    return valor


# Texto y no número: un DNI puede empezar con 0.
Dni = Annotated[str, AfterValidator(_validar_dni)]


class ItemCatalogo(BaseModel):
    """Referencia a un elemento de catálogo: `{"id": 1, "nombre": "1.º"}`."""

    id: int
    nombre: str


class ParcheSinNulos(BaseModel):
    """Base de los schemas de PATCH.

    Omitir un campo es "no cambiarlo". Enviarlo en null se rechaza con un 422 en ese
    mismo campo, salvo los listados en `CAMPOS_NULABLES` (columnas opcionales que se
    pueden vaciar). Pydantic no valida los valores por defecto, así que los campos
    omitidos no pasan por aquí.
    """

    CAMPOS_NULABLES: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def rechazar_null(cls, valor, info: ValidationInfo):
        if valor is None and info.field_name not in cls.CAMPOS_NULABLES:
            raise ValueError("Este campo no puede ser nulo.")
        return valor
