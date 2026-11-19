"""Nombres de personas: qué se acepta. Es la regla de referencia del sistema.

El frontend aplica la misma regla en el formulario de Alumnos
(FE-HUASCARAN `src/lib/nombrePersona.js`, `motivoNombreInvalido`). Si cambias una,
cambia la otra: los dos repos prueban la misma tabla de casos
(`tests/test_nombre_persona.py` y `src/lib/__tests__/nombrePersona.test.js`).

Nombres y apellidos, cada uno por separado:

- como máximo 60 caracteres;
- solo letras de cualquier alfabeto (con tildes, ñ, ü…), números, espacios, apóstrofo
  (' o ’) y guion: ningún símbolo como < > = ; { } $ @ + , ( );
- no empiezan ni terminan con apóstrofo o guion.

Además, una celda de Excel que empieza con =, +, -, @, tabulador, retorno de carro o
salto de línea se rechaza antes de mirar lo demás: Excel o LibreOffice la tomarían como
fórmula (OWASP, CSV/Formula Injection).
"""
import unicodedata
from typing import Optional

LARGO_MAXIMO_NOMBRE = 60
SIGNOS_PERMITIDOS = frozenset(" '’-")
SIGNOS_EXTREMOS = frozenset("'’-")
# El retorno de carro de una celda llega como salto de línea al leer el XML del .xlsx.
INICIO_DE_FORMULA = frozenset("=+-@\t\r\n")

MOTIVO_LARGO = "largo"
MOTIVO_CARACTERES = "caracteres"
MOTIVO_EXTREMOS = "extremos"
MOTIVO_ESPACIOS = "espacios"


def _caracter_permitido(caracter: str) -> bool:
    # Las mismas clases que `[\p{L}\p{M}\p{Nd}' ’-]` del frontend.
    categoria = unicodedata.category(caracter)
    return caracter in SIGNOS_PERMITIDOS or categoria[0] in "LM" or categoria == "Nd"


def parece_formula(texto: str) -> bool:
    """Si la celda empieza (tal cual, o tras los espacios) con un signo de fórmula."""
    return texto[:1] in INICIO_DE_FORMULA or texto.lstrip(" ")[:1] in INICIO_DE_FORMULA


def motivo_nombre_invalido(valor: Optional[str]) -> Optional[str]:
    """Por qué `valor` no sirve como nombre o apellido, o None si sirve. Vacío devuelve
    None: lo obligatorio lo decide quien llama. Es `motivoNombreInvalido` del frontend,
    con un código en vez del texto."""
    texto = valor or ""
    limpio = texto.strip()
    if texto and not limpio:
        return MOTIVO_ESPACIOS
    if not limpio:
        return None
    if len(limpio) > LARGO_MAXIMO_NOMBRE:
        return MOTIVO_LARGO
    if not all(_caracter_permitido(c) for c in limpio):
        return MOTIVO_CARACTERES
    if limpio[0] in SIGNOS_EXTREMOS or limpio[-1] in SIGNOS_EXTREMOS:
        return MOTIVO_EXTREMOS
    return None
