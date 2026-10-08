"""una fila de alumno_programa_historial por alumno y periodo

Revision ID: c4f18e2a9d37
Revises: 5a73b20b7cb4
Create Date: 2026-10-08 20:00:00.000000

Escrita a mano (no autogenerate). CU014.

UNIQUE `uq_alumno_programa_historial_periodo` sobre
`alumno_programa_historial (id_alumno, id_periodo_academico)`: el subprograma de un alumno
en un periodo es uno solo; un cambio de subprograma actualiza la fila de ese periodo. Si
ya hay duplicados, la migración aborta y los lista; no borra nada, porque decidir cuál
de las filas es la correcta es del Supervisor.

Downgrade: quita el UNIQUE.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f18e2a9d37'
down_revision: str | Sequence[str] | None = '5a73b20b7cb4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DUPLICADOS = sa.text(
    """
    SELECT id_alumno, id_periodo_academico, string_agg(CAST(id AS TEXT), ', ') AS ids
    FROM alumno_programa_historial
    GROUP BY id_alumno, id_periodo_academico
    HAVING count(*) > 1
    ORDER BY id_alumno, id_periodo_academico
    """
)


class MigracionAbortada(RuntimeError):
    """Hay datos que impiden aplicar la migración; se listan para corregirlos a mano."""


def upgrade() -> None:
    """Upgrade schema."""
    filas = op.get_bind().execute(DUPLICADOS).fetchall()
    if filas:
        detalle = "\n".join(f"  - {tuple(fila)}" for fila in filas)
        raise MigracionAbortada(
            "Hay filas duplicadas en alumno_programa_historial por "
            "(id_alumno, id_periodo_academico, ids). Corríjalas antes de migrar:\n" + detalle
        )
    with op.batch_alter_table('alumno_programa_historial') as batch:
        batch.create_unique_constraint(
            'uq_alumno_programa_historial_periodo', ['id_alumno', 'id_periodo_academico']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('alumno_programa_historial') as batch:
        batch.drop_constraint('uq_alumno_programa_historial_periodo', type_='unique')
