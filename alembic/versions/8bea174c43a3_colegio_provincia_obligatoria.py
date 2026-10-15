"""colegio.provincia obligatoria

Revision ID: 8bea174c43a3
Revises: e56a86f50db8
Create Date: 2026-10-14 10:30:00.000000

Escrita a mano (no autogenerate). CU013 (versión de la semana 10): la provincia es
obligatoria y el distrito, opcional (`colegio.distrito` ya admitía null).

Los colegios sin provincia (null o en blanco) quedan con 'Por definir', el mismo valor
que usa `colegios_iniciales.json` para los datos aún por confirmar, y después la columna
pasa a NOT NULL.

Downgrade: la columna vuelve a admitir null. Los 'Por definir' puestos aquí se quedan:
no se distinguen de los que ya venían así.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '8bea174c43a3'
down_revision: str | Sequence[str] | None = 'e56a86f50db8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text("UPDATE colegio SET provincia = 'Por definir' WHERE provincia IS NULL OR btrim(provincia) = ''")
    )
    op.alter_column(
        'colegio', 'provincia', existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'colegio', 'provincia', existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=True
    )
