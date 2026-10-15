"""usuario: cambio obligatorio de la contraseña temporal

Revision ID: afc1aa051933
Revises: 8bea174c43a3
Create Date: 2026-10-14 12:00:00.000000

Escrita a mano (no autogenerate). CU001 y CU016 (versión de la semana 10): una cuenta
nueva recibe una contraseña temporal que vence a las 48 horas y que debe cambiar en su
primer ingreso.

- `debe_cambiar_contrasena` BOOLEAN NOT NULL DEFAULT false: todas las cuentas existentes
  quedan sin cambio obligatorio.
- `contrasena_temporal_expira` TIMESTAMPTZ NULL: vencimiento de la temporal; null si no
  hay marca.

Downgrade: quita ambas columnas.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'afc1aa051933'
down_revision: str | Sequence[str] | None = '8bea174c43a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'usuario',
        sa.Column('debe_cambiar_contrasena', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column('usuario', sa.Column('contrasena_temporal_expira', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('usuario', 'contrasena_temporal_expira')
    op.drop_column('usuario', 'debe_cambiar_contrasena')
