"""Grupo 5 del diseño v3: seguridad y acceso.

Tablas técnicas: no llevan los campos de auditoría (diseño v3, §1.2).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Index, Text, Uuid, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime

TIPOS_CIERRE_SESION = (
    "Manual",
    "Automático por expiración",
    "Invalidada por restablecimiento de contraseña",
    "Invalidada por desactivación",
)


def _valores(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class ControlAccesoCorreo(SQLModel, table=True):
    """Control de abuso por correo ingresado, exista o no la cuenta (CU002, CU005)."""

    __tablename__ = "control_acceso_correo"

    # Normalizado en minúsculas.
    correo: str = Field(sa_type=Text, primary_key=True)
    intentos_fallidos: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    bloqueado_hasta: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    ultimo_intento_fallido: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    solicitudes_pin: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    ventana_pin_inicio: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class Sesion(SQLModel, table=True):
    """Sesión autenticada; su id viaja en el claim `jti` del JWT (CU003, CU007, CU008)."""

    __tablename__ = "sesion"
    __table_args__ = (
        CheckConstraint(
            f"tipo_cierre IS NULL OR tipo_cierre IN ({_valores(TIPOS_CIERRE_SESION)})",
            name="ck_sesion_tipo_cierre",
        ),
        # Las sesiones abiertas de un usuario se buscan al invalidarlas todas.
        Index(
            "ix_sesion_abiertas_por_usuario",
            "id_usuario",
            postgresql_where=text("fin IS NULL"),
            sqlite_where=text("fin IS NULL"),
        ),
        # Última conexión de cada usuario (CU020): máximo de `inicio` por usuario.
        Index("ix_sesion_usuario_inicio", "id_usuario", "inicio"),
    )

    id_sesion: uuid.UUID = Field(default_factory=uuid.uuid4, sa_type=Uuid, primary_key=True)
    id_usuario: int = Field(foreign_key="usuario.id_usuario")
    inicio: datetime = Field(sa_type=UTCDateTime)
    # inicio + 8 horas; nunca se modifica.
    expira: datetime = Field(sa_type=UTCDateTime)
    # null mientras la sesión está abierta.
    fin: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    tipo_cierre: Optional[str] = Field(default=None, sa_type=Text)


class RecoveryKey(SQLModel, table=True):
    """Llaves de recuperación de un solo uso del Supervisor original (CU001)."""

    __tablename__ = "recovery_key"

    id_recovery_key: Optional[int] = Field(default=None, primary_key=True)
    id_usuario: int = Field(foreign_key="usuario.id_usuario")
    # Hash bcrypt; la llave en texto plano solo existe en el .txt entregado.
    llave_hash: str = Field(sa_type=Text)
    creada_en: datetime = Field(sa_type=UTCDateTime)
    # No null = llave usada (invalidada).
    usada_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
