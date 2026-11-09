"""Formato común de los errores de negocio.

Todo error que el cliente deba poder distinguir sin leer el texto se responde así:

    {"detail": "Mensaje legible", "motivo": "codigo_maquina", ...extra}

`motivo` y los campos extra (p. ej. `requisitos_incumplidos`) solo aparecen cuando se
pasan. `detail` mantiene el nombre que ya usan los HTTPException de FastAPI, así el
frontend lee el mensaje siempre del mismo campo.
"""
from collections.abc import Iterable
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.validacion import (
    MENSAJE_GENERAL,
    MENSAJE_OBLIGATORIO,
    MOTIVO_VALIDACION,
    documentar_formato_422,
    manejar_error_validacion,
)

# Textos por defecto de Starlette para rutas inexistentes o métodos no admitidos.
TRADUCCIONES_HTTP = {
    "Not Found": "Recurso no encontrado.",
    "Method Not Allowed": "Método no permitido.",
}


class ErrorNegocio(Exception):
    def __init__(
        self,
        status_code: int,
        detail: str,
        motivo: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.motivo = motivo
        self.extra = extra or {}
        # Cabeceras HTTP de la respuesta, p. ej. WWW-Authenticate en los 401.
        self.headers = headers

    def cuerpo(self) -> dict[str, Any]:
        cuerpo: dict[str, Any] = {"detail": self.detail}
        if self.motivo is not None:
            cuerpo["motivo"] = self.motivo
        cuerpo.update(self.extra)
        return cuerpo


def errores_de_campos(campos: Iterable[str], mensaje: str) -> ErrorNegocio:
    """422 de una regla de negocio sobre uno o más campos, con el formato único de
    validación (`motivo: validacion`, `errores: [{campo, mensaje}]`, un error por campo) y
    el mensaje también en `detail`."""
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        mensaje,
        motivo=MOTIVO_VALIDACION,
        extra={"errores": [{"campo": campo, "mensaje": mensaje} for campo in campos]},
    )


def error_de_campo(campo: str, mensaje: str) -> ErrorNegocio:
    return errores_de_campos([campo], mensaje)


def campo_obligatorio(campo: str) -> ErrorNegocio:
    """El mismo 422 que da la validación de la petición cuando falta un campo obligatorio,
    para los campos cuya obligatoriedad depende de una regla de negocio."""
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        MENSAJE_GENERAL,
        motivo=MOTIVO_VALIDACION,
        extra={"errores": [{"campo": campo, "mensaje": MENSAJE_OBLIGATORIO}]},
    )


def manejar_error_negocio(_request: Request, error: ErrorNegocio) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.cuerpo(), headers=error.headers)


def manejar_error_http(_request: Request, error: StarletteHTTPException) -> JSONResponse:
    """`{"detail": "..."}` para cualquier HTTPException, con los textos por defecto de
    Starlette (404 y 405 de rutas inexistentes) en español. Conserva las cabeceras
    (p. ej. `Allow` en el 405)."""
    detail = TRADUCCIONES_HTTP.get(error.detail, error.detail)
    return JSONResponse(
        status_code=error.status_code, content={"detail": detail}, headers=getattr(error, "headers", None)
    )


def registrar_manejadores(app: FastAPI) -> None:
    app.add_exception_handler(ErrorNegocio, manejar_error_negocio)
    app.add_exception_handler(RequestValidationError, manejar_error_validacion)
    app.add_exception_handler(StarletteHTTPException, manejar_error_http)
    documentar_formato_422(app)
