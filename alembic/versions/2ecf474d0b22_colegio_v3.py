"""colegio v3: provincia, nivel educativo, departamento, distrito, seccion y activo

Revision ID: 2ecf474d0b22
Revises: 35e61c646994
Create Date: 2026-10-07 16:53:48.558036

Escrita a mano (no autogenerate). Diseño v3, corrección 24: `zona` se renombra a
`provincia` conservando los datos y se agregan los campos de CU013.

`departamento` es obligatorio pero no tiene default: se agrega nullable, se rellena
con 'Áncash' (todos los colegios actuales están ahí) y recién entonces pasa a NOT NULL.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ecf474d0b22'
down_revision: str | Sequence[str] | None = '35e61c646994'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('colegio', 'zona', new_column_name='provincia')

    op.add_column(
        'colegio',
        sa.Column('nivel_educativo', sa.Text(), server_default='Primaria', nullable=False),
    )
    op.add_column(
        'colegio',
        sa.Column('seccion', sa.Text(), server_default='Única', nullable=False),
    )
    op.add_column(
        'colegio',
        sa.Column('activo', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )

    op.add_column('colegio', sa.Column('departamento', sa.Text(), nullable=True))
    op.execute("UPDATE colegio SET departamento = 'Áncash'")
    op.alter_column('colegio', 'departamento', existing_type=sa.Text(), nullable=False)

    op.add_column('colegio', sa.Column('distrito', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('colegio', 'distrito')
    op.drop_column('colegio', 'departamento')
    op.drop_column('colegio', 'activo')
    op.drop_column('colegio', 'seccion')
    op.drop_column('colegio', 'nivel_educativo')
    op.alter_column('colegio', 'provincia', new_column_name='zona')
