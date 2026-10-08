"""Paginación común de los listados: página de 10 elementos, `page` desde 1.

Respuesta: {"items": [...], "total": 37, "page": 1, "page_size": 10, "total_pages": 4}.
Una página posterior a la última devuelve `items` vacío, no un error.
"""
from math import ceil
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select, SelectOfScalar

TAMANO_PAGINA = 10

T = TypeVar("T")

# Parámetro de query reutilizable: `page: Pagina = 1`.
Pagina = Annotated[int, Query(ge=1, description="Número de página, desde 1")]


class Paginado(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


def paginar(db: Session, consulta: Select | SelectOfScalar, page: int) -> tuple[list[Any], int]:
    """Filas de la página pedida y el total de la consulta (sin paginar).

    La consulta debe traer su ORDER BY: sin orden estable, las páginas pueden repetir
    u omitir filas.
    """
    total = db.exec(select(func.count()).select_from(consulta.order_by(None).subquery())).one()
    filas = db.exec(consulta.offset((page - 1) * TAMANO_PAGINA).limit(TAMANO_PAGINA)).all()
    return list(filas), total


def pagina(items: list[T], total: int, page: int) -> Paginado[T]:
    return Paginado[T](
        items=items,
        total=total,
        page=page,
        page_size=TAMANO_PAGINA,
        total_pages=ceil(total / TAMANO_PAGINA),
    )
