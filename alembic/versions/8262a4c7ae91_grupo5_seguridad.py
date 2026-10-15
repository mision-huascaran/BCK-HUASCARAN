"""grupo 5: control_acceso_correo, sesion y recovery_key

Revision ID: 8262a4c7ae91
Revises: 91d2a1b6cd42
Create Date: 2026-10-07 17:18:32.816774

Escrita a mano (no autogenerate). Diseño v3, correcciones 35, 36 y 37. Tablas
técnicas: sin campos de auditoría (§1.2).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8262a4c7ae91'
down_revision: str | Sequence[str] | None = '91d2a1b6cd42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'control_acceso_correo',
        sa.Column('correo', sa.Text(), nullable=False),
        sa.Column('intentos_fallidos', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('bloqueado_hasta', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ultimo_intento_fallido', sa.DateTime(timezone=True), nullable=True),
        sa.Column('solicitudes_pin', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('ventana_pin_inicio', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('correo'),
    )

    op.create_table(
        'sesion',
        sa.Column('id_sesion', sa.Uuid(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expira', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fin', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tipo_cierre', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id_sesion'),
        sa.CheckConstraint(
            "tipo_cierre IS NULL OR tipo_cierre IN ('Manual', 'Automático por expiración', "
            "'Invalidada por restablecimiento de contraseña', 'Invalidada por desactivación')",
            name='ck_sesion_tipo_cierre',
        ),
    )
    op.create_index(
        'ix_sesion_abiertas_por_usuario',
        'sesion',
        ['id_usuario'],
        postgresql_where=sa.text('fin IS NULL'),
        sqlite_where=sa.text('fin IS NULL'),
    )

    op.create_table(
        'recovery_key',
        sa.Column('id_recovery_key', sa.Integer(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('llave_hash', sa.Text(), nullable=False),
        sa.Column('creada_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('usada_en', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id_recovery_key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('recovery_key')
    op.drop_index('ix_sesion_abiertas_por_usuario', table_name='sesion')
    op.drop_table('sesion')
    op.drop_table('control_acceso_correo')
