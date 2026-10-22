"""habilitaciones: asignaciones retroactivas y permisos de corrección (CU016, CU028, T4)

Revision ID: cfc5a93906b1
Revises: 823a0fa059dc
Create Date: 2026-10-21 21:40:00.000000

Diseño v4 §5. Autogenerada y revisada a mano (tipos de fecha sin depender de la app y
nombre explícito de la FK nueva de `auditoria`, que Alembic no sabe borrar sin nombre).

Crea:
- `docente_colegio_grado.retroactiva` (false en las filas existentes).
- `permiso_correccion`: un permiso por docente, año, colegio, grado (FK compuesta a
  `colegio_grado`), PA y módulo, con vigencia [inicio, fin) y revocación opcional.
  CHECK: módulo válido, fin > inicio y motivo no vacío. Índice por alcance.
- `auditoria.id_permiso` (nullable -> permiso_correccion).

No transforma ni borra datos. Downgrade: elimina lo creado (se pierden los permisos y la
marca de las asignaciones retroactivas, que quedan como asignaciones comunes).
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfc5a93906b1'
down_revision: str | Sequence[str] | None = '823a0fa059dc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
FK_AUDITORIA_PERMISO = 'auditoria_id_permiso_fkey'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'docente_colegio_grado',
        sa.Column('retroactiva', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.create_table(
        'permiso_correccion',
        sa.Column('id_permiso', sa.Integer(), primary_key=True),
        sa.Column('id_docente', sa.Integer(), sa.ForeignKey('docente.id_docente'), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), sa.ForeignKey('año_escolar.id_anio_escolar'), nullable=False),
        sa.Column(
            'id_periodo_academico', sa.Integer(),
            sa.ForeignKey('periodo_academico.id_periodo_academico'), nullable=False,
        ),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('modulo', sa.Text(), nullable=False),
        sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fin', sa.DateTime(timezone=True), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('otorgado_por', sa.Integer(), sa.ForeignKey(FK_USUARIO), nullable=False),
        sa.Column('otorgado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revocado_por', sa.Integer(), sa.ForeignKey(FK_USUARIO), nullable=True),
        sa.Column('revocado_en', sa.DateTime(timezone=True), nullable=True),
        sa.Column('motivo_revocacion', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "modulo IN ('asistencias', 'rubrica', 'registro_vuelo', 'lectura')", name='ck_permiso_correccion_modulo'
        ),
        sa.CheckConstraint('fin > inicio', name='ck_permiso_correccion_vigencia'),
        sa.CheckConstraint('length(trim(motivo)) > 0', name='ck_permiso_correccion_motivo'),
        sa.ForeignKeyConstraint(
            ['id_colegio', 'id_grado'], ['colegio_grado.id_colegio', 'colegio_grado.id_grado'],
            name='fk_permiso_correccion_colegio_grado',
        ),
    )
    op.create_index(
        'ix_permiso_correccion_alcance', 'permiso_correccion',
        ['id_docente', 'id_colegio', 'id_grado', 'id_periodo_academico', 'modulo'],
    )
    op.add_column('auditoria', sa.Column('id_permiso', sa.Integer(), nullable=True))
    op.create_foreign_key(FK_AUDITORIA_PERMISO, 'auditoria', 'permiso_correccion', ['id_permiso'], ['id_permiso'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_AUDITORIA_PERMISO, 'auditoria', type_='foreignkey')
    op.drop_column('auditoria', 'id_permiso')
    op.drop_index('ix_permiso_correccion_alcance', table_name='permiso_correccion')
    op.drop_table('permiso_correccion')
    op.drop_column('docente_colegio_grado', 'retroactiva')
