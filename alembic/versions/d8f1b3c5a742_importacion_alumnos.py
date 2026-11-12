"""importacion_alumnos: importación de la nómina de alumnos desde Excel (CU014, semana 14)

Revision ID: d8f1b3c5a742
Revises: c6e2a9d4f713
Create Date: 2026-11-09 18:00:00.000000

Escrita a mano. Crea `importacion_alumnos` (quién y cuándo importó una nómina, con sus
conteos; `id_importacion` lo genera el cliente para que un reintento no importe dos
veces), agrega `alumno.id_importacion` (FK e índice) y amplía `ck_auditoria_accion` con
'importar'. Sin backfill: los alumnos actuales quedan con `id_importacion` NULL.

Downgrade: falla (RuntimeError) si hay auditorías con la acción 'importar' o alumnos con
`id_importacion`, porque se perderían o violarían el CHECK anterior. Si no hay ninguno,
revierte todo.
"""
from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f1b3c5a742'
down_revision: str | Sequence[str] | None = 'c6e2a9d4f713'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'importacion_alumnos'
ALUMNO = 'alumno'
AUDITORIA = 'auditoria'
COLUMNA = 'id_importacion'
FK_ALUMNO = 'alumno_id_importacion_fkey'
IX_ALUMNO = 'ix_alumno_id_importacion'
CK_ACCION = 'ck_auditoria_accion'
ACCIONES_ANTES = "accion IN ('crear', 'editar', 'activar', 'inactivar')"
ACCIONES_DESPUES = "accion IN ('crear', 'editar', 'activar', 'inactivar', 'importar')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLA,
        sa.Column(COLUMNA, sa.Uuid(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=False),
        sa.Column('nombre_archivo', sa.String(length=255), nullable=False),
        sa.Column('huella', sa.CHAR(length=64), nullable=False),
        sa.Column('importados', sa.Integer(), nullable=False),
        sa.Column('no_importadas', sa.Integer(), nullable=False),
        sa.Column('con_advertencia', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            'importados >= 0 AND no_importadas >= 0 AND con_advertencia >= 0',
            name='ck_importacion_alumnos_conteos',
        ),
        sa.ForeignKeyConstraint(['id_usuario'], ['usuario.id_usuario'], name='importacion_alumnos_id_usuario_fkey'),
        sa.PrimaryKeyConstraint(COLUMNA, name='importacion_alumnos_pkey'),
    )
    op.add_column(ALUMNO, sa.Column(COLUMNA, sa.Uuid(), nullable=True))
    op.create_foreign_key(FK_ALUMNO, ALUMNO, TABLA, [COLUMNA], [COLUMNA])
    op.create_index(IX_ALUMNO, ALUMNO, [COLUMNA])
    op.drop_constraint(CK_ACCION, AUDITORIA, type_='check')
    op.create_check_constraint(CK_ACCION, AUDITORIA, ACCIONES_DESPUES)


def _exigir_sin_importaciones() -> None:
    """Falla si ya se importó algo: esos datos no caben en el esquema anterior."""
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    auditorias = bind.execute(sa.text("SELECT count(*) FROM auditoria WHERE accion = 'importar'")).scalar_one()
    alumnos = bind.execute(sa.text('SELECT count(*) FROM alumno WHERE id_importacion IS NOT NULL')).scalar_one()
    if auditorias or alumnos:
        raise RuntimeError(
            f"No se puede revertir: hay {alumnos} alumno(s) importados y {auditorias} auditoría(s) con la acción "
            "'importar'. Esos datos no caben en el esquema anterior."
        )


def downgrade() -> None:
    """Downgrade schema."""
    _exigir_sin_importaciones()
    op.drop_constraint(CK_ACCION, AUDITORIA, type_='check')
    op.create_check_constraint(CK_ACCION, AUDITORIA, ACCIONES_ANTES)
    op.drop_index(IX_ALUMNO, table_name=ALUMNO)
    op.drop_constraint(FK_ALUMNO, ALUMNO, type_='foreignkey')
    op.drop_column(ALUMNO, COLUMNA)
    op.drop_table(TABLA)
