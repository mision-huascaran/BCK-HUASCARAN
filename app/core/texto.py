"""Comparación de textos escritos por personas (nombres de colegios, secciones, alumnos
y usuarios)."""
import unicodedata

from sqlalchemy import func


def nombre_comparable(nombre: str) -> str:
    """Sin tildes, sin distinguir mayúsculas y sin espacios sobrantes: 'Amashca' = 'AMASHCÁ '.

    Es la identidad de un colegio o de una sección por nombre, la misma para la API (409
    por duplicado) y para los scripts de carga.
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", nombre) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.casefold().split())


# Quien busca escribe "perez", no "Pérez": en las consultas se comparan ambos lados sin
# tildes ni mayúsculas. Se usa translate() en vez de la extensión unaccent porque no
# requiere instalar nada en la BD.
CON_TILDES = "áéíóúüñÁÉÍÓÚÜÑ"
SIN_TILDES = "aeiouunAEIOUUN"
_A_SIN_TILDES = str.maketrans(CON_TILDES, SIN_TILDES)


def comparable_sql(expresion):
    """La expresión SQL sin tildes y en minúsculas, para buscar y ordenar. Se quitan las
    tildes antes de bajar a minúsculas porque lower() de SQLite solo cambia ASCII."""
    return func.lower(func.translate(expresion, CON_TILDES, SIN_TILDES))


def palabras_buscadas(q: str) -> list[str]:
    """Las palabras de una búsqueda, sin tildes y en minúsculas (como `comparable_sql`)."""
    return q.translate(_A_SIN_TILDES).lower().split()
