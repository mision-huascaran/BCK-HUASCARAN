"""codigo_verificacion_expira a timestamptz

Revision ID: 7fa524e653cc
Revises: 94f7ac6aced4
Create Date: 2026-10-07 08:34:17.894032

Escrita a mano (no autogenerate). La columna guardaba instantes en UTC sin zona
(`datetime.utcnow()`); pasa a `timestamptz` siguiendo la convención de app/core/tiempo.py.
`AT TIME ZONE 'UTC'` interpreta los valores existentes como UTC al subir y los
devuelve a UTC sin zona al bajar, así que ningún vencimiento se corre de hora.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7fa524e653cc'
down_revision: str | Sequence[str] | None = '94f7ac6aced4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'usuario',
        'codigo_verificacion_expira',
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
        postgresql_using="codigo_verificacion_expira AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'usuario',
        'codigo_verificacion_expira',
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="codigo_verificacion_expira AT TIME ZONE 'UTC'",
    )
