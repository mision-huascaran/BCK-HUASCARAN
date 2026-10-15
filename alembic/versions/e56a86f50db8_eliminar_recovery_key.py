"""elimina la tabla recovery_key

Revision ID: e56a86f50db8
Revises: d81a5c3f2b64
Create Date: 2026-10-14 10:00:00.000000

Escrita a mano (no autogenerate). CU001 (versión de la semana 10): el Supervisor
original recupera su cuenta solo con PIN, como el resto de usuarios. Las Recovery Keys
desaparecen y con ellas su tabla y los hashes que hubiera guardados.

Downgrade: recrea la tabla vacía, idéntica a la de 8262a4c7ae91 (grupo 5). Los hashes
borrados no se recuperan: habría que volver a generarlos.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e56a86f50db8'
down_revision: str | Sequence[str] | None = 'd81a5c3f2b64'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('recovery_key')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'recovery_key',
        sa.Column('id_recovery_key', sa.Integer(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('llave_hash', sa.Text(), nullable=False),
        sa.Column('creada_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('usada_en', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id_recovery_key'),
    )
