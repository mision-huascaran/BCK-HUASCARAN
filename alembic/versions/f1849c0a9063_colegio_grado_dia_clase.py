"""colegio_grado: día de clase por grado

Revision ID: f1849c0a9063
Revises: 848955819540
Create Date: 2026-10-14 18:10:00.000000

Escrita a mano (no autogenerate). CU013 (versión final de la semana 10): cada grado que
ofrece un colegio tiene un día de clase semanal, que CU011 usa para saber qué clases
vencieron.

- `colegio_grado.dia_clase` SMALLINT NULL con CHECK entre 1 y 5 (1 = lunes … 5 =
  viernes). La API lo exige al crear o editar; los colegios existentes quedan en null
  (sus grados no generan clases esperadas hasta que se configure).

Downgrade: quita la columna (y con ella el CHECK).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1849c0a9063'
down_revision: str | Sequence[str] | None = '848955819540'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('colegio_grado', sa.Column('dia_clase', sa.SmallInteger(), nullable=True))
    op.create_check_constraint('ck_colegio_grado_dia_clase', 'colegio_grado', 'dia_clase BETWEEN 1 AND 5')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_colegio_grado_dia_clase', 'colegio_grado', type_='check')
    op.drop_column('colegio_grado', 'dia_clase')
