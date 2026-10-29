"""Cálculos numéricos comunes del Dashboard."""


def porcentaje(parte: int, base: int) -> float:
    """parte / base × 100, con 1 decimal. `base` debe ser mayor que 0."""
    return round(100 * parte / base, 1)
