"""salud_por_registro: clave canónica del registro en avisos y operaciones (CU012)

Revision ID: a7d3e5f9b102
Revises: f5c1d8a3e926
Create Date: 2026-10-25 14:00:00.000000

Escrita a mano. Agrega `clave_registro` (texto, nullable; ver
app/services/claves_registro.py) a `operacion_pendiente` (índice por clave) y a
`operacion_sincronizacion` (índice por módulo, clave y llegada, para la última operación
de cada registro). Sin backfill: las filas anteriores quedan en NULL y cuentan como
antes (los avisos, uno por `id_operacion`; las operaciones viejas no participan del
rechazo sin resolver).

Downgrade: quita los índices y las columnas.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d3e5f9b102'
down_revision: str | Sequence[str] | None = 'f5c1d8a3e926'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDIENTE = 'operacion_pendiente'
OPERACION = 'operacion_sincronizacion'
COLUMNA = 'clave_registro'
IX_PENDIENTE = 'ix_operacion_pendiente_clave'
IX_OPERACION = 'ix_operacion_sincronizacion_clave'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(PENDIENTE, sa.Column(COLUMNA, sa.Text(), nullable=True))
    op.add_column(OPERACION, sa.Column(COLUMNA, sa.Text(), nullable=True))
    op.create_index(IX_PENDIENTE, PENDIENTE, [COLUMNA])
    op.create_index(IX_OPERACION, OPERACION, ['modulo', COLUMNA, 'recibida_en'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(IX_OPERACION, table_name=OPERACION)
    op.drop_index(IX_PENDIENTE, table_name=PENDIENTE)
    op.drop_column(OPERACION, COLUMNA)
    op.drop_column(PENDIENTE, COLUMNA)
