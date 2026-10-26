"""lectura_no_realizada_historial: historial de declaraciones "No realizada" y su reversión (CU026)

Revision ID: f5c1d8a3e926
Revises: e4a9b7c2d815
Create Date: 2026-10-25 10:00:00.000000

Escrita a mano:

1. Crea `lectura_no_realizada`: una fila por declaración, con su justificación, quién,
   con qué actividad y autorización y cuándo; los campos de la reversión ("Habilitar
   registro de lectura") van todos NULL o todos con valor. FK a `lectura_semanal` con
   ON DELETE CASCADE e índice único parcial: una sola declaración vigente por tabla.
2. Backfill: cada tabla con `estado = 'no_realizada'` recibe su declaración vigente con
   `motivo_no_realizada`, `id_docente`, `id_actividad`, `id_permiso`, `fecha_registro` y
   quien la modificó por última vez (o la creó).

Downgrade: borra la tabla (el estado actual sigue en `lectura_semanal`).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5c1d8a3e926'
down_revision: str | Sequence[str] | None = 'e4a9b7c2d815'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'lectura_no_realizada'
FK_USUARIO = 'usuario.id_usuario'
FK_ACTIVIDAD = 'actividad.id_actividad'
FK_PERMISO = 'permiso_correccion.id_permiso'
FECHA = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        TABLA,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_lectura_semanal', sa.Integer(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('declarada_por', sa.Integer(), nullable=False),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('id_permiso', sa.Integer(), nullable=True),
        sa.Column('fecha_registro', FECHA, nullable=False),
        sa.Column('creado_en', FECHA, nullable=False),
        sa.Column('revertida_por', sa.Integer(), nullable=True),
        sa.Column('motivo_correccion', sa.Text(), nullable=True),
        sa.Column('id_actividad_reversion', sa.Uuid(), nullable=True),
        sa.Column('id_permiso_reversion', sa.Integer(), nullable=True),
        sa.Column('fecha_registro_reversion', FECHA, nullable=True),
        sa.Column('revertida_en', FECHA, nullable=True),
        sa.CheckConstraint('length(trim(motivo)) > 0', name='ck_lectura_no_realizada_motivo'),
        sa.CheckConstraint(
            "(revertida_por IS NULL AND motivo_correccion IS NULL AND fecha_registro_reversion IS NULL "
            "AND revertida_en IS NULL AND id_actividad_reversion IS NULL AND id_permiso_reversion IS NULL) OR "
            "(revertida_por IS NOT NULL AND motivo_correccion IS NOT NULL AND fecha_registro_reversion IS NOT NULL "
            "AND revertida_en IS NOT NULL)",
            name='ck_lectura_no_realizada_reversion',
        ),
        sa.CheckConstraint(
            'motivo_correccion IS NULL OR length(trim(motivo_correccion)) > 0',
            name='ck_lectura_no_realizada_motivo_correccion',
        ),
        sa.ForeignKeyConstraint(['id_lectura_semanal'], ['lectura_semanal.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['declarada_por'], [FK_USUARIO]),
        sa.ForeignKeyConstraint(['id_docente'], ['docente.id_docente']),
        sa.ForeignKeyConstraint(['id_actividad'], [FK_ACTIVIDAD]),
        sa.ForeignKeyConstraint(['id_permiso'], [FK_PERMISO]),
        sa.ForeignKeyConstraint(['revertida_por'], [FK_USUARIO]),
        sa.ForeignKeyConstraint(['id_actividad_reversion'], [FK_ACTIVIDAD]),
        sa.ForeignKeyConstraint(['id_permiso_reversion'], [FK_PERMISO]),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_lectura_no_realizada_vigente', TABLA, ['id_lectura_semanal'], unique=True,
        postgresql_where=sa.text('revertida_en IS NULL'), sqlite_where=sa.text('revertida_en IS NULL'),
    )
    op.execute(
        f"INSERT INTO {TABLA} (id_lectura_semanal, motivo, declarada_por, id_docente, id_actividad, id_permiso, "
        "fecha_registro, creado_en) "
        "SELECT id, motivo_no_realizada, COALESCE(modificado_por, creado_por), id_docente, id_actividad, id_permiso, "
        "fecha_registro, COALESCE(modificado_en, creado_en) "
        "FROM lectura_semanal WHERE estado = 'no_realizada'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_lectura_no_realizada_vigente', table_name=TABLA)
    op.drop_table(TABLA)
