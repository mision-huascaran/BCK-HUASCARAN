"""Cálculos numéricos comunes del Dashboard."""
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

DOS_DECIMALES = Decimal("0.01")


def porcentaje(parte: int, base: int) -> float:
    """parte / base × 100, con 1 decimal. `base` debe ser mayor que 0."""
    return round(100 * parte / base, 1)


def dos_decimales(valor: Fraction) -> float:
    """Valor exacto para mostrar con 2 decimales, redondeando la mitad hacia arriba
    (1,625 -> 1,63). El orden se decide con el valor exacto, no con este."""
    exacto = Decimal(valor.numerator) / Decimal(valor.denominator)
    return float(exacto.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP))
