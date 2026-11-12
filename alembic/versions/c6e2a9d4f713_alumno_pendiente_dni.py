"""alumno_pendiente_dni: alumno con pertenencia pendiente y DNI opcional (CU014, semana 14)

Revision ID: c6e2a9d4f713
Revises: a7d3e5f9b102
Create Date: 2026-11-09 10:00:00.000000

Escrita a mano. La importación de una nómina crea alumnos incompletos: `id_grado`,
`id_seccion` e `id_programa_actual` pasan a nullable, y `ck_alumno_grupo_coherente` exige
que la sección y el subprograma vayan juntos y que, si hay sección, haya grado. La FK
compuesta `fk_alumno_seccion` se mantiene: con alguna columna NULL no se valida (MATCH
SIMPLE). Agrega `dni` (8 dígitos, `ck_alumno_dni_formato`), único entre alumnos con el
índice parcial `uq_alumno_dni`. Sin backfill: los alumnos actuales conservan su grupo y
quedan con `dni` NULL.

Downgrade: falla (RuntimeError) si hay alumnos con el grupo incompleto, porque las
columnas no pueden volver a NOT NULL. Si no hay ninguno, revierte todo; los DNI se pierden.
"""
from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6e2a9d4f713'
down_revision: str | Sequence[str] | None = 'a7d3e5f9b102'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'alumno'
COLUMNAS_GRUPO = ('id_grado', 'id_seccion', 'id_programa_actual')
COLUMNA_DNI = 'dni'
CK_GRUPO = 'ck_alumno_grupo_coherente'
CK_DNI = 'ck_alumno_dni_formato'
UQ_DNI = 'uq_alumno_dni'


def upgrade() -> None:
    """Upgrade schema."""
    for columna in COLUMNAS_GRUPO:
        op.alter_column(TABLA, columna, existing_type=sa.Integer(), nullable=True)
    op.add_column(TABLA, sa.Column(COLUMNA_DNI, sa.String(length=8), nullable=True))
    op.create_check_constraint(
        CK_GRUPO,
        TABLA,
        "(id_seccion IS NULL) = (id_programa_actual IS NULL) AND (id_seccion IS NULL OR id_grado IS NOT NULL)",
    )
    op.create_check_constraint(CK_DNI, TABLA, "dni ~ '^[0-9]{8}$'")
    op.create_index(UQ_DNI, TABLA, [COLUMNA_DNI], unique=True, postgresql_where=sa.text('dni IS NOT NULL'))


def _exigir_grupos_completos() -> None:
    """Falla si algún alumno tiene el grado, la sección o el subprograma en NULL."""
    if context.is_offline_mode():
        return
    incompletos = op.get_bind().execute(
        sa.text('SELECT count(*) FROM alumno WHERE id_grado IS NULL OR id_seccion IS NULL OR id_programa_actual IS NULL')
    ).scalar_one()
    if incompletos:
        raise RuntimeError(
            f'No se puede revertir: hay {incompletos} alumno(s) con pertenencia pendiente (grado, sección o '
            'subprograma en NULL). Completa su grupo antes de hacer el downgrade.'
        )


def downgrade() -> None:
    """Downgrade schema."""
    _exigir_grupos_completos()
    op.drop_index(UQ_DNI, table_name=TABLA, postgresql_where=sa.text('dni IS NOT NULL'))
    op.drop_constraint(CK_DNI, TABLA, type_='check')
    op.drop_constraint(CK_GRUPO, TABLA, type_='check')
    op.drop_column(TABLA, COLUMNA_DNI)
    for columna in COLUMNAS_GRUPO:
        op.alter_column(TABLA, columna, existing_type=sa.Integer(), nullable=False)
