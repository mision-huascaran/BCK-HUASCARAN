"""Grupo 6 del diseño v3: actividad y trazabilidad.

Tablas técnicas: no llevan los campos de auditoría (diseño v3, §1.2).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, CheckConstraint, Index, Integer, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime
from app.models.organizacion import FK_DOCENTE, FK_USUARIO

TIPOS_CIERRE_ACTIVIDAD = (
    "Manual por finalización de actividad",
    "Forzado por cierre de sesión",
    "Automático por expiración de sesión",
)

ACCIONES_AUDITORIA = ("crear", "editar", "activar", "inactivar")

# Módulos que generan operaciones offline (CU017-CU021).
MODULOS_SINCRONIZACION = ("asistencias", "lectura", "rubrica", "registro_vuelo", "alumnos", "actividad")
ACEPTADA = "aceptada"
RECHAZADA = "rechazada"
DESCARTADA = "descartada"
ESTADOS_OPERACION = (ACEPTADA, RECHAZADA, DESCARTADA)


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
    id_docente: int = Field(foreign_key=FK_DOCENTE)
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
    id_usuario: int = Field(foreign_key=FK_USUARIO)
    fecha: datetime = Field(sa_type=UTCDateTime)
    # null cuando el autor no es Docente.
    id_actividad: Optional[uuid.UUID] = Field(
        default=None, sa_type=Uuid, foreign_key="actividad.id_actividad"
    )
    # El permiso de corrección (CU028) con que se hizo el cambio, o con que se otorgó o
    # revocó ese permiso; null en lo demás.
    id_permiso: Optional[int] = Field(default=None, foreign_key="permiso_correccion.id_permiso")


def _check_modulo(tabla: str) -> CheckConstraint:
    return CheckConstraint(f"modulo IN ({_valores(MODULOS_SINCRONIZACION)})", name=f"ck_{tabla}_modulo")


class OperacionSincronizacion(SQLModel, table=True):
    """Resultado de cada operación recibida (CU017-CU021). Su id lo genera el cliente: un
    reintento con el mismo id devuelve la `respuesta` guardada sin volver a aplicar nada."""

    __tablename__ = "operacion_sincronizacion"
    __table_args__ = (
        _check_modulo("operacion_sincronizacion"),
        CheckConstraint(f"estado IN ({_valores(ESTADOS_OPERACION)})", name="ck_operacion_sincronizacion_estado"),
        Index("ix_operacion_sincronizacion_actividad", "id_actividad"),
        Index("ix_operacion_sincronizacion_usuario_estado", "id_usuario", "estado"),
    )

    id_operacion: uuid.UUID = Field(sa_type=Uuid, primary_key=True)
    id_usuario: int = Field(foreign_key=FK_USUARIO)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key="actividad.id_actividad")
    modulo: str = Field(sa_type=Text)
    entidad: str = Field(sa_type=Text)
    id_entidad: str = Field(sa_type=Text)
    # La versión del registro que veía el usuario; null si era nuevo.
    version_origen: Optional[int] = None
    estado: str = Field(sa_type=Text)
    motivo: Optional[str] = Field(default=None, sa_type=Text)
    # El resultado devuelto al cliente, tal cual (idempotencia).
    respuesta: Optional[dict] = Field(default=None, sa_type=JSON().with_variant(JSONB(), "postgresql"))
    fecha_realizacion: datetime = Field(sa_type=UTCDateTime)
    recibida_en: datetime = Field(sa_type=UTCDateTime)


class OperacionPendiente(SQLModel, table=True):
    """Aviso de que un dispositivo tiene una operación offline todavía sin enviar. Se borra
    al procesarla (aceptada o rechazada)."""

    __tablename__ = "operacion_pendiente"
    __table_args__ = (
        _check_modulo("operacion_pendiente"),
        Index("ix_operacion_pendiente_usuario", "id_usuario"),
        Index("ix_operacion_pendiente_actividad", "id_actividad"),
    )

    id_operacion: uuid.UUID = Field(sa_type=Uuid, primary_key=True)
    id_usuario: int = Field(foreign_key=FK_USUARIO)
    id_dispositivo: str = Field(sa_type=Text)
    # Sin FK: la actividad pudo crearse sin conexión y llegar al servidor después.
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid)
    modulo: str = Field(sa_type=Text)
    fecha_realizacion: datetime = Field(sa_type=UTCDateTime)
    informado_en: datetime = Field(sa_type=UTCDateTime)
