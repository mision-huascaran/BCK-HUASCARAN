"""asistencia: cada registro guarda su aula (colegio y grado)

Revision ID: 0f7bffeb9082
Revises: 617ebd8543df
Create Date: 2026-10-14 20:00:00.000000

Escrita a mano (no autogenerate). CU022 define la clase como fecha + colegio + grado +
subprograma: el registro guarda su aula para que una rotación del alumno (CU014) no mude
sus asistencias pasadas a otra aula.

- `asistencia.id_colegio` (FK colegio) e `id_grado` (FK grado), NOT NULL. Las filas que
  ya existan se rellenan con el colegio y grado actuales de su alumno (es lo que la API
  daba por hecho hasta ahora).
- `ix_asistencia_aula_fecha (id_colegio, id_grado, fecha)`: la clase de un aula y sus
  clases dictadas.
- La unicidad (id_alumno, fecha, id_programa) no cambia.

Downgrade: quita el índice y las dos columnas (con sus FK).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f7bffeb9082'
down_revision: str | Sequence[str] | None = '617ebd8543df'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('asistencia', sa.Column('id_colegio', sa.Integer(), nullable=True))
    op.add_column('asistencia', sa.Column('id_grado', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE asistencia SET id_colegio = alumno.id_colegio, id_grado = alumno.id_grado "
        "FROM alumno WHERE alumno.id_alumno = asistencia.id_alumno"
    )
    op.alter_column('asistencia', 'id_colegio', nullable=False)
    op.alter_column('asistencia', 'id_grado', nullable=False)
    op.create_foreign_key('asistencia_id_colegio_fkey', 'asistencia', 'colegio', ['id_colegio'], ['id_colegio'])
    op.create_foreign_key('asistencia_id_grado_fkey', 'asistencia', 'grado', ['id_grado'], ['id_grado'])
    op.create_index('ix_asistencia_aula_fecha', 'asistencia', ['id_colegio', 'id_grado', 'fecha'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_asistencia_aula_fecha', table_name='asistencia')
    op.drop_constraint('asistencia_id_grado_fkey', 'asistencia', type_='foreignkey')
    op.drop_constraint('asistencia_id_colegio_fkey', 'asistencia', type_='foreignkey')
    op.drop_column('asistencia', 'id_grado')
    op.drop_column('asistencia', 'id_colegio')
