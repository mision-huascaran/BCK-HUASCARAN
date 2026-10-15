"""Nivel descriptivo del Registro de Vuelo (CU015): Inicio, En proceso, Logrado o
Avanzado, según la letra Raz-Kids y el grado (catálogo `escala_nivel_descriptivo`).

El id de `nivel_razkids` es su orden, así que una letra cae en el nivel de mayor
`id_nivel_rk_desde` que no la supere. El nivel más bajo de cada grado empieza en la
primera letra: una letra por debajo de la de Inicio también es Inicio.
"""
from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.evaluacion import EscalaNivelDescriptivo


@dataclass(frozen=True)
class EscalaDescriptiva:
    """Escala de un grado ya cargada: (desde, nivel) ordenados por `desde`."""

    tramos: tuple[tuple[int, str], ...]

    def nivel(self, id_nivel_rk: Optional[int]) -> Optional[str]:
        """Nivel de una letra (por su id). None sin letra o si el grado no tiene escala."""
        if id_nivel_rk is None or not self.tramos:
            return None
        posicion = bisect_right([desde for desde, _ in self.tramos], id_nivel_rk)
        return self.tramos[max(posicion - 1, 0)][1]


def escala_del_grado(db: Session, id_grado: int) -> EscalaDescriptiva:
    filas = db.exec(
        select(EscalaNivelDescriptivo.id_nivel_rk_desde, EscalaNivelDescriptivo.nivel)
        .where(EscalaNivelDescriptivo.id_grado == id_grado)
        .order_by(EscalaNivelDescriptivo.id_nivel_rk_desde)
    ).all()
    return EscalaDescriptiva(tuple(filas))


def nivel_descriptivo(db: Session, id_grado: int, id_nivel_rk: Optional[int]) -> Optional[str]:
    """Nivel descriptivo de una letra en un grado. Para varias letras del mismo grado,
    cargar la escala una vez con `escala_del_grado`."""
    return escala_del_grado(db, id_grado).nivel(id_nivel_rk)
