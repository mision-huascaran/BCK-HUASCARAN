from typing import Any, Generator

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from app.core.config import settings

# Con la BD inaccesible, la conexión falla a los 10 s en vez de quedarse colgada.
SEGUNDOS_TIMEOUT_CONEXION = 10


def opciones_de_conexion(url: str) -> dict[str, Any]:
    """`connect_args` para el driver. `connect_timeout` es de PostgreSQL (libpq); SQLite
    (los tests) no lo admite, así que solo se pasa con PostgreSQL."""
    if make_url(url).get_backend_name() == "postgresql":
        return {"connect_timeout": SEGUNDOS_TIMEOUT_CONEXION}
    return {}


engine = create_engine(settings.DATABASE_URL, connect_args=opciones_de_conexion(settings.DATABASE_URL))


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def insert_con_conflicto(db: Session, tabla: Table):
    """INSERT con soporte de ON CONFLICT del dialecto en uso (PostgreSQL o SQLite), para
    escribir de forma atómica filas que dos peticiones simultáneas podrían crear a la vez."""
    dialecto = db.get_bind().dialect.name
    if dialecto == "postgresql":
        return postgresql.insert(tabla)
    if dialecto == "sqlite":
        return sqlite.insert(tabla)
    raise NotImplementedError(f"ON CONFLICT no está soportado para el dialecto {dialecto}")
