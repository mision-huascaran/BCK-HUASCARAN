"""Nivel Académico del alumno que muestran Lectura y Rúbrica.

Es el último nivel colocado confirmado del Registro de Vuelo de un PA anterior al de la
clase; sin él, null ("Sin evaluar"). El Registro de Vuelo se rehace en T9: hasta
entonces no hay niveles confirmados y la función devuelve siempre None. T9 la completa
sin cambiar su firma.
"""
from datetime import date
from typing import Optional

from sqlmodel import Session


def nivel_academico(db: Session, id_alumno: int, fecha_clase: date) -> Optional[str]:
    """Letra Raz-Kids del último nivel colocado confirmado antes del PA de `fecha_clase`,
    o None si no hay ("Sin evaluar")."""
    del db, id_alumno, fecha_clase
    return None
