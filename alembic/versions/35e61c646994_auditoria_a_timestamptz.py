"""auditoria a timestamptz

Revision ID: 35e61c646994
Revises: 7fa524e653cc
Create Date: 2026-10-07 16:53:48.558036

Escrita a mano (no autogenerate). Diseño v3, corrección 21: `creado_en` y
`modificado_en` pasan de `date` a `timestamptz` en las 8 tablas con auditoría.

Los valores actuales son días de calendario de Lima. Al subir se convierten a la
medianoche de Lima (05:00 UTC); al bajar se recupera el día de Lima de cada instante.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35e61c646994'
down_revision: str | Sequence[str] | None = '7fa524e653cc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLAS = (
    'año_escolar',
    'periodo_academico',
    'colegio',
    'docente',
    'usuario',
    'alumno',
    'docente_colegio_grado',
    'alumno_programa_historial',
)

# (columna, nullable)
COLUMNAS = (('creado_en', False), ('modificado_en', True))


def upgrade() -> None:
    """Upgrade schema."""
    for tabla in TABLAS:
        for columna, nullable in COLUMNAS:
            op.alter_column(
                tabla,
                columna,
                type_=sa.DateTime(timezone=True),
                existing_type=sa.Date(),
                existing_nullable=nullable,
                postgresql_using=f"{columna}::timestamp AT TIME ZONE 'America/Lima'",
            )


def downgrade() -> None:
    """Downgrade schema."""
    for tabla in TABLAS:
        for columna, nullable in COLUMNAS:
            op.alter_column(
                tabla,
                columna,
                type_=sa.Date(),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=nullable,
                postgresql_using=f"({columna} AT TIME ZONE 'America/Lima')::date",
            )
