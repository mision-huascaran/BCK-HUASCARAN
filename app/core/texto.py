"""Comparación de textos escritos por personas (nombres de colegios y de secciones)."""
import unicodedata


def nombre_comparable(nombre: str) -> str:
    """Sin tildes, sin distinguir mayúsculas y sin espacios sobrantes: 'Amashca' = 'AMASHCÁ '.

    Es la identidad de un colegio o de una sección por nombre, la misma para la API (409
    por duplicado) y para los scripts de carga.
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", nombre) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.casefold().split())
