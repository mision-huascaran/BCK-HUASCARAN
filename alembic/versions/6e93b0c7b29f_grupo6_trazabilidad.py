"""grupo 6: actividad y auditoria

Revision ID: 6e93b0c7b29f
Revises: 8262a4c7ae91
Create Date: 2026-10-07 17:18:32.816774

Escrita a mano (no autogenerate). Diseño v3, correcciones 38 y 39. Tablas técnicas:
sin campos de auditoría (§1.2). `actividad.id_actividad` lo genera el cliente, por eso
no tiene default en el servidor.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e93b0c7b29f'
down_revision: str | Sequence[str] | None = '8262a4c7ae91'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'actividad',
        sa.Column('id_actividad', sa.Uuid(), nullable=False),
        sa.Column('id_sesion', sa.Uuid(), sa.ForeignKey('sesion.id_sesion'), nullable=False),
        sa.Column('id_docente', sa.Integer(), sa.ForeignKey('docente.id_docente'), nullable=False),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fin', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tipo_cierre', sa.Text(), nullable=True),
        sa.Column('sincronizado_en', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id_actividad'),
        sa.CheckConstraint(
            "tipo_cierre IS NULL OR tipo_cierre IN ('Manual por finalización de actividad', "
            "'Forzado por cierre de sesión', 'Automático por expiración de sesión')",
            name='ck_actividad_tipo_cierre',
        ),
        sa.CheckConstraint('fin IS NULL OR fin >= inicio', name='ck_actividad_fin_posterior_inicio'),
    )
    op.create_index(
        'uq_actividad_una_activa_por_docente',
        'actividad',
        ['id_docente'],
        unique=True,
        postgresql_where=sa.text('fin IS NULL'),
        sqlite_where=sa.text('fin IS NULL'),
    )

    op.create_table(
        'auditoria',
        sa.Column('id_auditoria', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tabla', sa.Text(), nullable=False),
        sa.Column('id_registro', sa.Text(), nullable=False),
        sa.Column('accion', sa.Text(), nullable=False),
        sa.Column('campo', sa.Text(), nullable=True),
        sa.Column('valor_anterior', sa.Text(), nullable=True),
        sa.Column('valor_nuevo', sa.Text(), nullable=True),
        sa.Column('id_usuario', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), sa.ForeignKey('actividad.id_actividad'), nullable=True),
        sa.PrimaryKeyConstraint('id_auditoria'),
        sa.CheckConstraint(
            "accion IN ('crear', 'editar', 'activar', 'inactivar')", name='ck_auditoria_accion'
        ),
    )
    op.create_index('ix_auditoria_registro', 'auditoria', ['tabla', 'id_registro', 'fecha'])
    op.create_index('ix_auditoria_actividad', 'auditoria', ['id_actividad'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_auditoria_actividad', table_name='auditoria')
    op.drop_index('ix_auditoria_registro', table_name='auditoria')
    op.drop_table('auditoria')
    op.drop_index('uq_actividad_una_activa_por_docente', table_name='actividad')
    op.drop_table('actividad')
