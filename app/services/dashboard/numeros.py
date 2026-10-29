"""Cálculos numéricos comunes del Dashboard."""
from collections.abc import Callable, Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import TypeVar

T = TypeVar("T")

DOS_DECIMALES = Decimal("0.01")


def porcentaje(parte: int, base: int) -> float:
    """parte / base × 100, con 1 decimal. `base` debe ser mayor que 0."""
    return round(100 * parte / base, 1)


def dos_decimales(valor: Fraction) -> float:
    """Valor exacto para mostrar con 2 decimales, redondeando la mitad hacia arriba
    (1,625 -> 1,63). El orden se decide con el valor exacto, no con este."""
    exacto = Decimal(valor.numerator) / Decimal(valor.denominator)
    return float(exacto.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP))


def ranking(elementos: Iterable[T], valor: Callable[[T], Fraction], desempate: Callable[[T], tuple]) -> list[tuple[int, T]]:
    """(posición, elemento) de mayor a menor `valor` exacto. Con el mismo valor exacto
    comparten posición, en el orden de `desempate`, y la siguiente salta (1, 1, 3)."""
    ordenados = sorted(elementos, key=lambda e: (-valor(e), desempate(e)))
    posiciones, posicion, anterior = [], 0, None
    for lugar, elemento in enumerate(ordenados, start=1):
        if valor(elemento) != anterior:
            posicion, anterior = lugar, valor(elemento)
        posiciones.append((posicion, elemento))
    return posiciones


def meses_entre(desde: date, hasta: date) -> list[tuple[int, int]]:
    """(año, mes) de cada mes que toca [desde, hasta], en orden."""
    meses, anio, mes = [], desde.year, desde.month
    while (anio, mes) <= (hasta.year, hasta.month):
        meses.append((anio, mes))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return meses
