"""grupo 1: colegio_grado, colegio_programa y dia_no_laborable

Revision ID: 91d2a1b6cd42
Revises: d6dd78e7e4ba
Create Date: 2026-10-07 17:18:32.816774

Escrita a mano (no autogenerate). Diseño v3, correcciones 25 y 27. Las tres tablas
llevan los campos de auditoría.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91d2a1b6cd42'
down_revision: str | Sequence[str] | None = 'd6dd78e7e4ba'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _auditoria() -> list[sa.Column]:
    return [
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('modificado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=True),
        sa.Column('modificado_en', sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'colegio_grado',
        sa.Column('id_colegio', sa.Integer(), sa.ForeignKey('colegio.id_colegio'), nullable=False),
        sa.Column('id_grado', sa.Integer(), sa.ForeignKey('grado.id_grado'), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id_colegio', 'id_grado'),
    )
    op.create_table(
        'colegio_programa',
        sa.Column('id_colegio', sa.Integer(), sa.ForeignKey('colegio.id_colegio'), nullable=False),
        sa.Column('id_programa', sa.Integer(), sa.ForeignKey('programa.id_programa'), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id_colegio', 'id_programa'),
    )
    op.create_table(
        'dia_no_laborable',
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('fecha'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('dia_no_laborable')
    op.drop_table('colegio_programa')
    op.drop_table('colegio_grado')
