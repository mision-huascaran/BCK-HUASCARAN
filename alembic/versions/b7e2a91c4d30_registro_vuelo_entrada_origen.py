"""registro_vuelo_entrada_origen: referencia a la decisión que dio el nivel de entrada (CU031, CU032)

Revision ID: b7e2a91c4d30
Revises: d06ce68c5f14
Create Date: 2026-10-24 16:00:00.000000

Escrita a mano. Agrega `registro_vuelo.id_registro_entrada` (nullable): la decisión del PA
anterior del mismo alumno y año que dio la entrada al decidir, aunque sea de otro colegio
(traslado). FK a `registro_vuelo.id` con ON DELETE SET NULL. Las filas existentes quedan
en NULL.

Downgrade: quita la FK y la columna.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2a91c4d30'
down_revision: str | Sequence[str] | None = 'd06ce68c5f14'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'registro_vuelo'
COLUMNA = 'id_registro_entrada'
FK = 'registro_vuelo_id_registro_entrada_fkey'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(TABLA, sa.Column(COLUMNA, sa.Integer(), nullable=True))
    op.create_foreign_key(FK, TABLA, TABLA, [COLUMNA], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK, TABLA, type_='foreignkey')
    op.drop_column(TABLA, COLUMNA)
