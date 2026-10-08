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


def _validar_formato(correo: str) -> str:
    """Forma mínima de un correo: `usuario@dominio.tld`, sin espacios y con una sola @.

    La validación definitiva es que el correo de bienvenida llegue; esto solo descarta
    errores de tipeo evidentes. Sin expresión regular, para que una entrada armada a
    propósito no dispare búsquedas con retroceso costosas.
    """
    usuario, _, dominio = correo.partition("@")
    partes = dominio.split(".")
    valido = (
        bool(usuario)
        and "@" not in dominio
        and not any(c.isspace() for c in correo)
        and len(partes) >= 2
        and all(partes)
    )
    if not valido:
        raise ValueError("El correo no tiene un formato válido.")
    return correo


# Para altas y ediciones de cuentas. El login y la recuperación usan CorreoNormalizado
# a secas: un correo mal escrito ahí es simplemente un intento fallido más.
CorreoValido = Annotated[str, AfterValidator(normalizar_correo), AfterValidator(_validar_formato)]
