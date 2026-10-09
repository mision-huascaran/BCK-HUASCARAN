"""Grupo 6 del diseño v3: actividad y trazabilidad.

Tablas técnicas: no llevan los campos de auditoría (diseño v3, §1.2).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Text, Uuid, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime

TIPOS_CIERRE_ACTIVIDAD = (
    "Manual por finalización de actividad",
    "Forzado por cierre de sesión",
    "Automático por expiración de sesión",
)

ACCIONES_AUDITORIA = ("crear", "editar", "activar", "inactivar")


def _valores(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class Actividad(SQLModel, table=True):
    """Actividad de trabajo del Docente, dentro de una sesión (CU009, CU010)."""

    __tablename__ = "actividad"
    __table_args__ = (
        CheckConstraint(
            f"tipo_cierre IS NULL OR tipo_cierre IN ({_valores(TIPOS_CIERRE_ACTIVIDAD)})",
            name="ck_actividad_tipo_cierre",
        ),
        CheckConstraint("fin IS NULL OR fin >= inicio", name="ck_actividad_fin_posterior_inicio"),
        # Una sola actividad activa (fin IS NULL) por docente.
        Index(
            "uq_actividad_una_activa_por_docente",
            "id_docente",
            unique=True,
            postgresql_where=text("fin IS NULL"),
            sqlite_where=text("fin IS NULL"),
        ),
        # Listados de actividades de un docente, de la más reciente a la más antigua.
        Index("ix_actividad_docente_inicio", "id_docente", "inicio"),
    )

    # Lo genera el cliente, para no duplicar actividades iniciadas sin conexión: sin default.
    id_actividad: uuid.UUID = Field(sa_type=Uuid, primary_key=True)
    id_sesion: uuid.UUID = Field(sa_type=Uuid, foreign_key="sesion.id_sesion")
    id_docente: int = Field(foreign_key="docente.id_docente")
    inicio: datetime = Field(sa_type=UTCDateTime)
    # null = actividad activa.
    fin: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    tipo_cierre: Optional[str] = Field(default=None, sa_type=Text)
    # Momento en que el servidor recibió el último dato de la actividad (CU008, CU009).
    sincronizado_en: datetime = Field(sa_type=UTCDateTime)


class Auditoria(SQLModel, table=True):
    """Historial de cambios de las entidades de negocio (CU014, CU015, CU017, CU019, CU021)."""

    __tablename__ = "auditoria"
    __table_args__ = (
        CheckConstraint(f"accion IN ({_valores(ACCIONES_AUDITORIA)})", name="ck_auditoria_accion"),
        Index("ix_auditoria_registro", "tabla", "id_registro", "fecha"),
        Index("ix_auditoria_actividad", "id_actividad"),
    )

    # BIGINT en PostgreSQL. En SQLite solo INTEGER PRIMARY KEY es autoincremental.
    id_auditoria: Optional[int] = Field(
        default=None,
        sa_type=BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
    )
    tabla: str = Field(sa_type=Text)
    # Texto para admitir claves int y uuid.
    id_registro: str = Field(sa_type=Text)
    accion: str = Field(sa_type=Text)
    # Una fila por campo editado; null en 'crear'.
    campo: Optional[str] = Field(default=None, sa_type=Text)
    valor_anterior: Optional[str] = Field(default=None, sa_type=Text)
    valor_nuevo: Optional[str] = Field(default=None, sa_type=Text)
    id_usuario: int = Field(foreign_key="usuario.id_usuario")
    fecha: datetime = Field(sa_type=UTCDateTime)
    # null cuando el autor no es Docente.
    id_actividad: Optional[uuid.UUID] = Field(
        default=None, sa_type=Uuid, foreign_key="actividad.id_actividad"
    )
