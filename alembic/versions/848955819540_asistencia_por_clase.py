"""asistencia por clase: reemplaza asistencia_semanal por asistencia

Revision ID: 848955819540
Revises: afc1aa051933
Create Date: 2026-10-14 18:00:00.000000

Escrita a mano (no autogenerate). CU022 (versión final de la semana 10): la asistencia se
toma por clase (fecha + colegio + grado + subprograma), no por semana.

- Se elimina `asistencia_semanal` (no hay datos reales) y se crea `asistencia`:
  `fecha` DATE de la clase, `id_programa` (subprograma del alumno al registrar),
  `estado` TEXT NULL con CHECK ('presente', 'tardanza', 'ausente'; null = sin marcar),
  `observacion` TEXT NULL, `id_docente`, `fecha_registro` TIMESTAMPTZ (momento real de
  captura) y los campos de auditoría.
- UNIQUE (id_alumno, fecha, id_programa).
- `ix_asistencia_fecha (fecha)`: las clases de una fecha o de un periodo (vencidos e
  incompletos de CU011, clases dictadas de CU015).
- `ix_alumno_colegio_grado (id_colegio, id_grado)`: los alumnos de un aula; con el UNIQUE
  (que empieza por id_alumno) resuelve "asistencia de un colegio y grado en una fecha".

Downgrade: elimina `asistencia` y el índice de alumno, y recrea `asistencia_semanal` vacía
tal como la dejó b7f46024bef7.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '848955819540'
down_revision: str | Sequence[str] | None = 'afc1aa051933'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _auditoria() -> list[sa.Column]:
    return [
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('modificado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=True),
        sa.Column('modificado_en', sa.DateTime(timezone=True), nullable=True),
    ]


def _alumno() -> sa.Column:
    return sa.Column('id_alumno', sa.Integer(), sa.ForeignKey('alumno.id_alumno'), nullable=False)


def _captura() -> list[sa.Column]:
    """Columnas de captura que comparten la tabla nueva y la anterior."""
    return [
        sa.Column('id_docente', sa.Integer(), sa.ForeignKey('docente.id_docente'), nullable=False),
        sa.Column('fecha_registro', sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('asistencia_semanal')
    op.create_table(
        'asistencia',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('id_programa', sa.Integer(), sa.ForeignKey('programa.id_programa'), nullable=False),
        sa.Column('estado', sa.Text(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        *_captura(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'fecha', 'id_programa', name='uq_asistencia_alumno_fecha_programa'),
        sa.CheckConstraint(
            "estado IS NULL OR estado IN ('presente', 'tardanza', 'ausente')", name='ck_asistencia_estado'
        ),
    )
    op.create_index('ix_asistencia_fecha', 'asistencia', ['fecha'])
    op.create_index('ix_alumno_colegio_grado', 'alumno', ['id_colegio', 'id_grado'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_alumno_colegio_grado', table_name='alumno')
    op.drop_index('ix_asistencia_fecha', table_name='asistencia')
    op.drop_table('asistencia')
    op.create_table(
        'asistencia_semanal',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column('id_semana', sa.Integer(), sa.ForeignKey('semana_reporte.id_semana'), nullable=False),
        sa.Column('asistio', sa.Boolean(), nullable=False),
        *_captura(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'id_semana', name='uq_asistencia_semanal_alumno_semana'),
    )
