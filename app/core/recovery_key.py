"""Formato de las Recovery Keys del Supervisor original (CU001).

Una llave son 7 grupos de 4 símbolos separados por guiones (`ABCD-EFGH-...`), tomados de
un alfabeto de 32 símbolos sin caracteres ambiguos. Lo usan el generador (CLI) y la
verificación (endpoint), así que el formato vive en un solo lugar.
"""
import secrets
import unicodedata

from app.core.security import CODIGO_ALFABETO

CANTIDAD_LLAVES = 10
# 28 símbolos de un alfabeto de 32 (5 bits cada uno) = 140 bits de entropía por llave.
GRUPOS_POR_LLAVE = 7
SIMBOLOS_POR_GRUPO = 4
SEPARADOR = "-"


def generar_llave() -> str:
    grupos = (
        "".join(secrets.choice(CODIGO_ALFABETO) for _ in range(SIMBOLOS_POR_GRUPO))
        for _ in range(GRUPOS_POR_LLAVE)
    )
    return SEPARADOR.join(grupos)


def normalizar_llave(llave: str) -> str:
    """Lleva lo que tipeó el usuario al formato con que se generó (y se hasheó) la llave.

    Descarta espacios y guiones de cualquier tipo (incluidos – y —, que aparecen al copiar
    desde un procesador de texto), pasa a mayúsculas y vuelve a agrupar de 4 en 4. Si no
    quedan exactamente 28 símbolos, devuelve lo limpiado tal cual: no coincidirá con
    ninguna llave y contará como intento fallido.
    """
    simbolos = "".join(
        c for c in llave if not c.isspace() and unicodedata.category(c) != "Pd"
    ).upper()
    if len(simbolos) != GRUPOS_POR_LLAVE * SIMBOLOS_POR_GRUPO:
        return simbolos
    return SEPARADOR.join(
        simbolos[i : i + SIMBOLOS_POR_GRUPO] for i in range(0, len(simbolos), SIMBOLOS_POR_GRUPO)
    )
