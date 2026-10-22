"""Motor de sugerencias de la rúbrica mensual (S10-04). Función pura, sin BD.

Con la evaluación mensual de un alumno (Fluidez y Comprensión, en códigos 0-4) y su
letra de entrada (escala `aa, A, …, Z`) sugiere bajar, mantener o subir de nivel:

    resultado = F × 0,40 + C × 0,60, en décimas enteras: d = 4·F + 6·C

- d < 20 → bajar; 20 ≤ d < 30 → mantener; d ≥ 30 → subir.
- Excepciones por la letra de entrada: en `aa` no se puede bajar (mantener, nivel
  mínimo); en `Z` no se puede subir (mantener, nivel máximo). Solo bloquean el movimiento
  imposible: en `Z` un puntaje bajo sí sugiere bajar, y en `aa` uno alto sí sugiere subir.
- Si falta una dimensión o la combinación no es válida para el subprograma: "Pendiente
  de evaluar", sin acción.
"""
from dataclasses import dataclass
from string import ascii_uppercase
from typing import Optional

ALFABETIZACION = "Alfabetización"
COMPRENSION_LECTORA = "Comprensión Lectora"
FLUIDEZ = "Fluidez"
COMPRENSION = "Comprensión"
DIMENSIONES = (FLUIDEZ, COMPRENSION)

# Códigos válidos de cada dimensión por subprograma (0 Pre-inicio solo en la Fluidez de
# Alfabetización).
VALORES_PERMITIDOS: dict[str, dict[str, range]] = {
    ALFABETIZACION: {FLUIDEZ: range(0, 5), COMPRENSION: range(1, 5)},
    COMPRENSION_LECTORA: {FLUIDEZ: range(1, 5), COMPRENSION: range(1, 5)},
}

# Escala alfabética de niveles: aa, A, B, …, Z (27 letras).
LETRAS = ("aa", *ascii_uppercase)
LETRA_MINIMA, LETRA_MAXIMA = LETRAS[0], LETRAS[-1]

BAJAR, MANTENER, SUBIR = "bajar", "mantener", "subir"
UMBRAL_MANTENER = 20
UMBRAL_SUBIR = 30
TEXTOS = {BAJAR: "Bajar nivel", MANTENER: "Mantener nivel", SUBIR: "Subir nivel"}
TEXTO_PENDIENTE = "Pendiente de evaluar"
TEXTO_NIVEL_MINIMO = "Mantener, nivel mínimo"
TEXTO_NIVEL_MAXIMO = "Mantener, nivel máximo"


@dataclass(frozen=True)
class Sugerencia:
    accion: Optional[str]
    texto: str
    resultado_decimas: Optional[int]
    excepcion: Optional[str] = None


PENDIENTE = Sugerencia(accion=None, texto=TEXTO_PENDIENTE, resultado_decimas=None)


def valor_permitido(programa: str, dimension: str, valor: Optional[int]) -> bool:
    """Si el código es válido para esa dimensión del subprograma (null no lo es)."""
    permitidos = VALORES_PERMITIDOS.get(programa, {}).get(dimension, range(0))
    return valor is not None and valor in permitidos


def resultado_decimas(fluidez: int, comprension: int) -> int:
    """F × 0,40 + C × 0,60 en décimas, sin floats ni redondeo."""
    return 4 * fluidez + 6 * comprension


def _accion(decimas: int) -> str:
    if decimas < UMBRAL_MANTENER:
        return BAJAR
    return MANTENER if decimas < UMBRAL_SUBIR else SUBIR


def sugerencia(
    fluidez: Optional[int], comprension: Optional[int], programa: str, letra_entrada: Optional[str]
) -> Sugerencia:
    """La sugerencia para el nivel del alumno. `programa` es el nombre del subprograma."""
    if not (valor_permitido(programa, FLUIDEZ, fluidez) and valor_permitido(programa, COMPRENSION, comprension)):
        return PENDIENTE
    decimas = resultado_decimas(fluidez, comprension)
    accion = _accion(decimas)
    if accion == BAJAR and letra_entrada == LETRA_MINIMA:
        return Sugerencia(MANTENER, TEXTO_NIVEL_MINIMO, decimas, LETRA_MINIMA)
    if accion == SUBIR and letra_entrada == LETRA_MAXIMA:
        return Sugerencia(MANTENER, TEXTO_NIVEL_MAXIMO, decimas, LETRA_MAXIMA)
    return Sugerencia(accion, TEXTOS[accion], decimas)
