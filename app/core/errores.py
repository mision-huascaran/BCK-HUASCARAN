"""Formato común de los errores de negocio.

Todo error que el cliente deba poder distinguir sin leer el texto se responde así:

    {"detail": "Mensaje legible", "motivo": "codigo_maquina", ...extra}

`motivo` y los campos extra (p. ej. `requisitos_incumplidos`) solo aparecen cuando se
pasan. `detail` mantiene el nombre que ya usan los HTTPException de FastAPI, así el
frontend lee el mensaje siempre del mismo campo.
"""
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.validacion import documentar_formato_422, manejar_error_validacion

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
