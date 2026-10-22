"""sincronizacion: operaciones offline, avisos de pendientes y cierre no verificado (T5)

Revision ID: ffcee55fdf96
Revises: cfc5a93906b1
Create Date: 2026-10-21 21:56:22.846652

Autogenerada y revisada a mano (tipos de fecha sin depender de la app, nombres
explícitos de las FK y el CHECK de módulo en una constante).

Crea:
- `operacion_sincronizacion`: resultado de cada operación recibida (PK = id que genera
  el cliente, para la idempotencia), con su estado (aceptada, rechazada, descartada),
  motivo y la respuesta devuelta (JSONB). Índices por actividad y por (usuario, estado).
- `operacion_pendiente`: aviso de una operación offline todavía no enviada. Sin FK a
  `actividad`: la actividad pudo crearse sin conexión y llegar después.
- `sesion.ultima_peticion_en` (nullable) y `sesion.fin_verificado` (true en las filas
  existentes: sus cierres se registraron con hora verificada).

No transforma ni borra datos. Downgrade: elimina lo creado (se pierden los resultados de
sincronización y los avisos de pendientes).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ffcee55fdf96'
down_revision: str | Sequence[str] | None = 'cfc5a93906b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
CHECK_MODULO = "modulo IN ('asistencias', 'lectura', 'rubrica', 'registro_vuelo', 'alumnos', 'actividad')"
PENDIENTE = 'operacion_pendiente'
OPERACION = 'operacion_sincronizacion'


def _instante(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        PENDIENTE,
        sa.Column('id_operacion', sa.Uuid(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), nullable=False),
        sa.Column('id_dispositivo', sa.Text(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('modulo', sa.Text(), nullable=False),
        _instante('fecha_realizacion'),
        _instante('informado_en'),
        sa.CheckConstraint(CHECK_MODULO, name='ck_operacion_pendiente_modulo'),
        sa.ForeignKeyConstraint(['id_usuario'], [FK_USUARIO], name='operacion_pendiente_id_usuario_fkey'),
        sa.PrimaryKeyConstraint('id_operacion'),
    )
    op.create_index('ix_operacion_pendiente_actividad', PENDIENTE, ['id_actividad'], unique=False)
    op.create_index('ix_operacion_pendiente_usuario', PENDIENTE, ['id_usuario'], unique=False)
    op.create_table(
        OPERACION,
        sa.Column('id_operacion', sa.Uuid(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('modulo', sa.Text(), nullable=False),
        sa.Column('entidad', sa.Text(), nullable=False),
        sa.Column('id_entidad', sa.Text(), nullable=False),
        sa.Column('version_origen', sa.Integer(), nullable=True),
        sa.Column('estado', sa.Text(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=True),
        sa.Column('respuesta', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
        _instante('fecha_realizacion'),
        _instante('recibida_en'),
        sa.CheckConstraint(
            "estado IN ('aceptada', 'rechazada', 'descartada')", name='ck_operacion_sincronizacion_estado'
        ),
        sa.CheckConstraint(CHECK_MODULO, name='ck_operacion_sincronizacion_modulo'),
        sa.ForeignKeyConstraint(
            ['id_actividad'], ['actividad.id_actividad'], name='operacion_sincronizacion_id_actividad_fkey'
        ),
        sa.ForeignKeyConstraint(['id_usuario'], [FK_USUARIO], name='operacion_sincronizacion_id_usuario_fkey'),
        sa.PrimaryKeyConstraint('id_operacion'),
    )
    op.create_index('ix_operacion_sincronizacion_actividad', OPERACION, ['id_actividad'], unique=False)
    op.create_index('ix_operacion_sincronizacion_usuario_estado', OPERACION, ['id_usuario', 'estado'], unique=False)
    op.add_column('sesion', _instante('ultima_peticion_en', nullable=True))
    op.add_column('sesion', sa.Column('fin_verificado', sa.Boolean(), server_default=sa.text('true'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sesion', 'fin_verificado')
    op.drop_column('sesion', 'ultima_peticion_en')
    op.drop_index('ix_operacion_sincronizacion_usuario_estado', table_name=OPERACION)
    op.drop_index('ix_operacion_sincronizacion_actividad', table_name=OPERACION)
    op.drop_table(OPERACION)
    op.drop_index('ix_operacion_pendiente_usuario', table_name=PENDIENTE)
    op.drop_index('ix_operacion_pendiente_actividad', table_name=PENDIENTE)
    op.drop_table(PENDIENTE)
