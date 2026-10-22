"""Secciones de los grados de un colegio (CU013, CU014).

- Cada grado nace con la sección "Única" y siempre tiene al menos una activa.
- Solo se agregan: no hay renombrar ni eliminar (decisión de Carlos). Dos nombres que
  difieren solo en mayúsculas, tildes o espacios son la misma sección.
- El alumno está en una sección de su grado: si el grado tiene una sola activa, se le
  asigna sola; si tiene varias, hay que elegirla.
Nada aquí hace commit.
"""
from collections import defaultdict
from typing import Iterable, Optional

from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.core.texto import nombre_comparable
from app.models.organizacion import SECCION_UNICA, Seccion

CAMPO_SECCION = "id_seccion"
MENSAJE_ELEGIR_SECCION = "Selecciona la sección del alumno."
MENSAJE_SECCION_AJENA = "La sección no pertenece a ese colegio y grado."


def secciones_por_grado(db: Session, id_colegio: int, solo_activas: bool = True) -> dict[int, list[Seccion]]:
    """{id_grado: secciones}, en el orden en que se crearon ("Única" primero)."""
    consulta = select(Seccion).where(Seccion.id_colegio == id_colegio)
    if solo_activas:
        consulta = consulta.where(col(Seccion.activo).is_(True))
    por_grado: dict[int, list[Seccion]] = defaultdict(list)
    for seccion in db.exec(consulta.order_by(Seccion.id_seccion)).all():
        por_grado[seccion.id_grado].append(seccion)
    return por_grado


def _de_grado(db: Session, id_colegio: int, id_grado: int) -> list[Seccion]:
    return list(
        db.exec(
            select(Seccion)
            .where(Seccion.id_colegio == id_colegio, Seccion.id_grado == id_grado)
            .order_by(Seccion.id_seccion)
        ).all()
    )


def agregar_secciones(
    db: Session, id_colegio: int, id_grado: int, nombres: Iterable[str], auditoria: dict
) -> list[str]:
    """Agrega al grado las secciones que todavía no tiene; las existentes no se tocan. Si
    el grado queda sin ninguna, le crea "Única". Devuelve los nombres agregados."""
    existentes = {nombre_comparable(s.nombre) for s in _de_grado(db, id_colegio, id_grado)}
    pedidos = list(nombres) or ([] if existentes else [SECCION_UNICA])
    agregados = []
    for nombre in pedidos:
        clave = nombre_comparable(nombre)
        if clave in existentes:
            continue
        existentes.add(clave)
        agregados.append(nombre.strip())
        db.add(Seccion(id_colegio=id_colegio, id_grado=id_grado, nombre=nombre.strip(), **auditoria))
    return agregados


def seccion_para(db: Session, id_colegio: int, id_grado: int, id_seccion: Optional[int]) -> int:
    """La sección del alumno en ese colegio y grado. Sin `id_seccion`, la única activa del
    grado (422 si tiene varias); con `id_seccion`, 422 si no es una sección activa de ese
    colegio y grado."""
    activas = [s for s in _de_grado(db, id_colegio, id_grado) if s.activo]
    if id_seccion is None:
        if len(activas) != 1:
            raise error_de_campo(CAMPO_SECCION, MENSAJE_ELEGIR_SECCION)
        return activas[0].id_seccion
    if id_seccion not in {s.id_seccion for s in activas}:
        raise error_de_campo(CAMPO_SECCION, MENSAJE_SECCION_AJENA)
    return id_seccion
