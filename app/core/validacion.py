"""Errores de validación de la petición con el mismo formato que los de negocio.

Pydantic y FastAPI responden por defecto con `detail` como lista de objetos en inglés.
Aquí se convierten en:

    {
      "detail": "Los datos enviados no son válidos.",
      "motivo": "validacion",
      "errores": [{"campo": "dni", "mensaje": "Debe tener exactamente 8 dígitos."}]
    }

`campo` es la ruta del campo sin el origen (`body`, `query`, `path`), unida con puntos.
`mensaje` va en español; los mensajes de los validadores propios se conservan.
"""
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

MENSAJE_GENERAL = "Los datos enviados no son válidos."
MOTIVO_VALIDACION = "validacion"
MENSAJE_GENERICO = "Valor inválido."

ORIGENES = {"body", "query", "path", "header", "cookie"}

# Mensaje fijo -> tipos de error de Pydantic que lo usan.
_MENSAJES_POR_TIPO = {
    "Este campo es obligatorio.": ("missing",),
    "Debe ser un texto.": ("string_type",),
    "Debe ser un número entero.": ("int_type", "int_parsing", "int_from_float"),
    "Debe ser un número.": ("float_type", "float_parsing"),
    "Debe ser verdadero o falso.": ("bool_type", "bool_parsing"),
    "Debe ser una lista.": ("list_type",),
    "Debe ser un objeto.": ("dict_type", "model_type", "model_attributes_type"),
    "Debe ser una fecha (AAAA-MM-DD).": ("date_type", "date_parsing", "date_from_datetime_parsing"),
    "Debe ser una fecha y hora.": ("datetime_type", "datetime_parsing"),
    "Debe ser un identificador válido.": ("uuid_type", "uuid_parsing"),
    "No tiene el formato esperado.": ("string_pattern_mismatch",),
    "Campo no permitido.": ("extra_forbidden",),
    "El cuerpo de la petición no es un JSON válido.": ("json_invalid", "json_type"),
}
# Tipo de error de Pydantic -> mensaje fijo.
MENSAJES = {tipo: mensaje for mensaje, tipos in _MENSAJES_POR_TIPO.items() for tipo in tipos}

# Tipos cuyo mensaje depende de un límite que viene en `ctx`.
LIMITES = {
    "greater_than": ("gt", "Debe ser mayor que {}."),
    "greater_than_equal": ("ge", "Debe ser mayor o igual que {}."),
    "less_than": ("lt", "Debe ser menor que {}."),
    "less_than_equal": ("le", "Debe ser menor o igual que {}."),
    "string_too_long": ("max_length", "Debe tener como máximo {} caracteres."),
    "too_long": ("max_length", "Debe tener como máximo {} elementos."),
}


def _campo(loc: tuple) -> str:
    partes = list(loc)
    if partes and partes[0] in ORIGENES:
        partes = partes[1:]
    return ".".join(str(p) for p in partes)


def _mensaje(error: dict[str, Any]) -> str:
    tipo = error.get("type", "")
    ctx = error.get("ctx") or {}

    if tipo == "value_error":
        # Validador propio: el texto es nuestro. Pydantic le antepone "Value error, ".
        return str(ctx.get("error", error.get("msg", MENSAJE_GENERICO)))
    if error.get("input", ...) is None and tipo.endswith("_type"):
        return "Este campo no puede ser nulo."
    if tipo == "string_too_short":
        minimo = ctx.get("min_length", 1)
        return "No puede estar vacío." if minimo <= 1 else f"Debe tener al menos {minimo} caracteres."
    if tipo == "too_short":
        minimo = ctx.get("min_length", 1)
        return "Debe tener al menos un elemento." if minimo <= 1 else f"Debe tener al menos {minimo} elementos."
    if tipo in ("literal_error", "enum"):
        return f"Valor no permitido. Valores aceptados: {ctx.get('expected', '')}."
    if tipo in LIMITES:
        clave, plantilla = LIMITES[tipo]
        return plantilla.format(ctx.get(clave, ""))
    return MENSAJES.get(tipo, MENSAJE_GENERICO)


def errores_legibles(errores: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"campo": _campo(tuple(e.get("loc", ()))), "mensaje": _mensaje(e)} for e in errores]


def manejar_error_validacion(_request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder(
            {
                "detail": MENSAJE_GENERAL,
                "motivo": MOTIVO_VALIDACION,
                "errores": errores_legibles(list(error.errors())),
            }
        ),
    )


# ── Documentación (OpenAPI) ─────────────────────────────────────────────────────────

class ErrorCampo(BaseModel):
    campo: str
    mensaje: str


class ErrorValidacion(BaseModel):
    """Cuerpo de todo 422 por datos inválidos."""

    detail: str
    motivo: str
    errores: list[ErrorCampo]


def documentar_formato_422(app: FastAPI) -> None:
    """Hace que /docs muestre el formato real de los 422 en vez del de FastAPI.

    FastAPI documenta todos los 422 con su esquema `HTTPValidationError`; se reemplaza
    ese esquema (al que apuntan todas las rutas) por `ErrorValidacion`.
    """
    generar = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            esquema = generar()
            componentes = esquema.setdefault("components", {}).setdefault("schemas", {})
            propio = ErrorValidacion.model_json_schema(ref_template="#/components/schemas/{model}")
            componentes.update(propio.pop("$defs", {}))
            componentes["HTTPValidationError"] = propio
            componentes.pop("ValidationError", None)
        return app.openapi_schema

    app.openapi = openapi
