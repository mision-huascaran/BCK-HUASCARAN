"""grupo 2: catalogo del dominio de evaluacion

Revision ID: 51ee51cc9e65
Revises: 6e93b0c7b29f
Create Date: 2026-10-07 17:18:32.816774

Escrita a mano (no autogenerate). Diseño v3, §4. Los cuatro catálogos puros no llevan
auditoría (§1.2); `evaluacion_diagnostica` sí. No se cargan datos: los catálogos se
llenan con un script aparte.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51ee51cc9e65'
down_revision: str | Sequence[str] | None = '6e93b0c7b29f'
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
        'nivel_razkids',
        sa.Column('id_nivel_rk', sa.Integer(), nullable=False),
        sa.Column('letra', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id_nivel_rk'),
    )
    op.create_table(
        'nivel_rubrica',
        sa.Column('id_nivel_rubrica', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), sa.ForeignKey('programa.id_programa'), nullable=False),
        sa.Column('dimension', sa.Text(), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('nombre_nivel', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id_nivel_rubrica'),
        sa.CheckConstraint(
            "dimension IN ('Fluidez', 'Comprensión')", name='ck_nivel_rubrica_dimension'
        ),
    )
    op.create_table(
        'nivel_general',
        sa.Column('id_nivel_general', sa.Integer(), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('nombre_nivel', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id_nivel_general'),
    )
    op.create_table(
        'nivel_esperado_por_grado',
        sa.Column(
            'id_grado', sa.Integer(), sa.ForeignKey('grado.id_grado'),
            autoincrement=False, nullable=False,
        ),
        sa.Column(
            'id_nivel_rk_esperado', sa.Integer(), sa.ForeignKey('nivel_razkids.id_nivel_rk'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id_grado'),
    )
    op.create_table(
        'evaluacion_diagnostica',
        sa.Column('id_evaluacion_diagnostica', sa.Integer(), nullable=False),
        sa.Column(
            'id_periodo_academico', sa.Integer(),
            sa.ForeignKey('periodo_academico.id_periodo_academico'), nullable=False,
        ),
        sa.Column('fecha_realizacion', sa.Date(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id_evaluacion_diagnostica'),
        sa.UniqueConstraint('id_periodo_academico', name='uq_evaluacion_diagnostica_periodo'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('evaluacion_diagnostica')
    op.drop_table('nivel_esperado_por_grado')
    op.drop_table('nivel_general')
    op.drop_table('nivel_rubrica')
    op.drop_table('nivel_razkids')
