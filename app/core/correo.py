"""Normalización de correos: un solo criterio en todos los puntos de entrada.

Los correos se guardan y se comparan en minúsculas y sin espacios alrededor (diseño v3,
`usuario.correo` y `control_acceso_correo.correo`). Así `Rosa@MH.org ` y `rosa@mh.org`
son la misma cuenta, el mismo contador de intentos y el mismo UNIQUE.
"""
from typing import Annotated

from pydantic import AfterValidator


def normalizar_correo(correo: str) -> str:
    return correo.strip().lower()


# Tipo para los schemas de Pydantic: el valor llega ya normalizado al servicio.
CorreoNormalizado = Annotated[str, AfterValidator(normalizar_correo)]
