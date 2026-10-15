"""escala_nivel_descriptivo: nivel descriptivo del Registro de Vuelo por grado

Revision ID: 617ebd8543df
Revises: f1849c0a9063
Create Date: 2026-10-14 18:20:00.000000

Escrita a mano (no autogenerate). CU015 (versión final de la semana 10): la letra Raz-Kids
colocada se muestra también como nivel descriptivo (Inicio, En proceso, Logrado,
Avanzado), según una escala que depende del grado.

- `escala_nivel_descriptivo (id_grado, nivel, id_nivel_rk_desde)`: desde qué letra empieza
  cada nivel en cada grado. PK (id_grado, nivel); UNIQUE (id_grado, id_nivel_rk_desde);
  CHECK del nombre del nivel. Catálogo sin auditoría, como los demás del Grupo 2.

Las filas las carga `cargar-catalogos` desde app/datos/catalogos.json (idempotente).

Downgrade: elimina la tabla.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '617ebd8543df'
down_revision: str | Sequence[str] | None = 'f1849c0a9063'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'escala_nivel_descriptivo',
        sa.Column('id_grado', sa.Integer(), sa.ForeignKey('grado.id_grado'), nullable=False, autoincrement=False),
        sa.Column('nivel', sa.Text(), nullable=False),
        sa.Column(
            'id_nivel_rk_desde', sa.Integer(), sa.ForeignKey('nivel_razkids.id_nivel_rk'), nullable=False
        ),
        sa.PrimaryKeyConstraint('id_grado', 'nivel'),
        sa.UniqueConstraint('id_grado', 'id_nivel_rk_desde', name='uq_escala_nivel_descriptivo_grado_desde'),
        sa.CheckConstraint(
            "nivel IN ('Inicio', 'En proceso', 'Logrado', 'Avanzado')", name='ck_escala_nivel_descriptivo_nivel'
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('escala_nivel_descriptivo')
