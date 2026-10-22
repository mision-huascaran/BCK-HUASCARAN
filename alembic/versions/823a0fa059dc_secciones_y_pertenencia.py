"""secciones_y_pertenencia: secciones por grado y pertenencia histórica del alumno (CU013, CU014, T3)

Revision ID: 823a0fa059dc
Revises: 543e52b37c0f
Create Date: 2026-10-21 20:49:42.938458

Diseño v4 §4. Autogenerada y revisada a mano: se agregó el movimiento de datos (en SQL,
corre también con `--sql`) y el orden en que `alumno.id_seccion` pasa a NOT NULL.

Crea:
- `seccion`: secciones de cada grado de un colegio (FK compuesta a `colegio_grado`),
  UNIQUE (colegio, grado, nombre) y UNIQUE (id_seccion, colegio, grado) como destino de
  las FK compuestas. CHECK: nombre no vacío.
- `alumno_pertenencia`: intervalos [desde, hasta) del alumno en un grupo, con CHECK
  `hasta > desde` e índice único parcial de un solo intervalo vigente por alumno.
- `alumno.id_seccion` NOT NULL, con FK compuesta (id_seccion, colegio, grado) -> seccion.

Transforma (los datos de qa y uat son ficticios):
- Una sección por cada fila de `colegio_grado`, con el nombre que tenía `colegio.seccion`
  ("Única" en todos los colegios cargados; si estaba vacío, "Única").
- Si un alumno está en un colegio y grado que ya no figura en `colegio_grado` (un grado
  retirado cuando solo tenía alumnos inactivos), se vuelve a ofrecer ese grado, sin día
  de clase: desde T3 un grado con alumnos en su historial no se puede retirar.
- `alumno.id_seccion` = la sección de su colegio y grado.
- Un intervalo vigente por alumno activo, con su grupo actual y `desde` = inicio del año
  escolar en curso (el más reciente que ya empezó, en hora de Lima), o su fecha de
  registro si no hay ninguno. Los inactivos no pertenecen a ningún grupo: al activarlos
  se abre su intervalo.

Borra:
- `alumno_programa_historial` (lo reemplaza `alumno_pertenencia`).
- `colegio.seccion`.

Downgrade (mínimo): devuelve `colegio.seccion` con el nombre de la primera sección del
colegio, recrea `alumno_programa_historial` vacía y elimina las tablas y la columna
nuevas. La pertenencia histórica y las secciones adicionales se pierden.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '823a0fa059dc'
down_revision: str | Sequence[str] | None = '543e52b37c0f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
GRUPO = ['id_seccion', 'id_colegio', 'id_grado']
HOY_LIMA = "(now() AT TIME ZONE 'America/Lima')::date"


def _auditoria() -> list[sa.Column]:
    return [
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey(FK_USUARIO), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('modificado_por', sa.Integer(), sa.ForeignKey(FK_USUARIO), nullable=True),
        sa.Column('modificado_en', sa.DateTime(timezone=True), nullable=True),
    ]


def _crear_tablas() -> None:
    op.create_table(
        'seccion',
        sa.Column('id_seccion', sa.Integer(), primary_key=True),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.Text(), nullable=False),
        sa.Column('activo', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        *_auditoria(),
        sa.CheckConstraint('length(trim(nombre)) > 0', name='ck_seccion_nombre'),
        sa.ForeignKeyConstraint(
            ['id_colegio', 'id_grado'], ['colegio_grado.id_colegio', 'colegio_grado.id_grado'],
            name='fk_seccion_colegio_grado',
        ),
        sa.UniqueConstraint('id_colegio', 'id_grado', 'nombre', name='uq_seccion_colegio_grado_nombre'),
        sa.UniqueConstraint(*GRUPO, name='uq_seccion_grupo'),
    )
    op.create_table(
        'alumno_pertenencia',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('id_alumno', sa.Integer(), sa.ForeignKey('alumno.id_alumno'), nullable=False),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('id_seccion', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), sa.ForeignKey('programa.id_programa'), nullable=False),
        sa.Column('desde', sa.Date(), nullable=False),
        sa.Column('hasta', sa.Date(), nullable=True),
        sa.Column('id_actividad', sa.Uuid(), sa.ForeignKey('actividad.id_actividad'), nullable=True),
        *_auditoria(),
        sa.CheckConstraint('hasta IS NULL OR hasta > desde', name='ck_alumno_pertenencia_intervalo'),
        sa.ForeignKeyConstraint(
            GRUPO, [f'seccion.{c}' for c in GRUPO], name='fk_alumno_pertenencia_seccion'
        ),
    )
    op.create_index('ix_alumno_pertenencia_alumno_desde', 'alumno_pertenencia', ['id_alumno', 'desde'])
    op.create_index(
        'ix_alumno_pertenencia_grupo', 'alumno_pertenencia', ['id_colegio', 'id_grado', 'id_seccion', 'desde']
    )
    op.create_index(
        'uq_alumno_pertenencia_vigente', 'alumno_pertenencia', ['id_alumno'], unique=True,
        postgresql_where=sa.text('hasta IS NULL'), sqlite_where=sa.text('hasta IS NULL'),
    )


def _mover_datos() -> None:
    """SQL puro: corre igual con conexión y con `--sql`."""
    op.execute(
        "INSERT INTO colegio_grado (id_colegio, id_grado, creado_por, creado_en) "
        "SELECT a.id_colegio, a.id_grado, min(a.creado_por), now() FROM alumno a "
        "WHERE NOT EXISTS (SELECT 1 FROM colegio_grado cg "
        "WHERE cg.id_colegio = a.id_colegio AND cg.id_grado = a.id_grado) "
        "GROUP BY a.id_colegio, a.id_grado"
    )
    op.execute(
        "INSERT INTO seccion (id_colegio, id_grado, nombre, activo, creado_por, creado_en) "
        "SELECT cg.id_colegio, cg.id_grado, COALESCE(NULLIF(trim(c.seccion), ''), 'Única'), true, "
        "cg.creado_por, cg.creado_en "
        "FROM colegio_grado cg JOIN colegio c ON c.id_colegio = cg.id_colegio"
    )
    op.execute(
        "UPDATE alumno a SET id_seccion = s.id_seccion FROM seccion s "
        "WHERE s.id_colegio = a.id_colegio AND s.id_grado = a.id_grado"
    )
    op.execute(
        "INSERT INTO alumno_pertenencia "
        "(id_alumno, id_colegio, id_grado, id_seccion, id_programa, desde, creado_por, creado_en) "
        "SELECT a.id_alumno, a.id_colegio, a.id_grado, a.id_seccion, a.id_programa_actual, "
        f"COALESCE((SELECT max(y.fecha_inicio) FROM \"año_escolar\" y WHERE y.fecha_inicio <= {HOY_LIMA}), "
        "a.fecha_registro), a.creado_por, now() "
        "FROM alumno a WHERE a.activo"
    )


def upgrade() -> None:
    """Upgrade schema."""
    _crear_tablas()
    op.add_column('alumno', sa.Column('id_seccion', sa.Integer(), nullable=True))
    _mover_datos()
    op.alter_column('alumno', 'id_seccion', existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key('fk_alumno_seccion', 'alumno', 'seccion', GRUPO, GRUPO)
    op.drop_table('alumno_programa_historial')
    op.drop_column('colegio', 'seccion')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'colegio', sa.Column('seccion', sa.Text(), server_default=sa.text("'Única'"), nullable=False)
    )
    op.execute(
        "UPDATE colegio c SET seccion = s.nombre FROM seccion s "
        "WHERE s.id_seccion = (SELECT min(id_seccion) FROM seccion WHERE id_colegio = c.id_colegio)"
    )
    op.create_table(
        'alumno_programa_historial',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('id_alumno', sa.Integer(), sa.ForeignKey('alumno.id_alumno'), nullable=False),
        sa.Column('id_programa', sa.Integer(), sa.ForeignKey('programa.id_programa'), nullable=False),
        sa.Column(
            'id_periodo_academico', sa.Integer(),
            sa.ForeignKey('periodo_academico.id_periodo_academico'), nullable=False,
        ),
        *_auditoria(),
        sa.UniqueConstraint('id_alumno', 'id_periodo_academico', name='uq_alumno_programa_historial_periodo'),
    )
    op.drop_constraint('fk_alumno_seccion', 'alumno', type_='foreignkey')
    op.drop_column('alumno', 'id_seccion')
    op.drop_index('uq_alumno_pertenencia_vigente', table_name='alumno_pertenencia')
    op.drop_index('ix_alumno_pertenencia_grupo', table_name='alumno_pertenencia')
    op.drop_index('ix_alumno_pertenencia_alumno_desde', table_name='alumno_pertenencia')
    op.drop_table('alumno_pertenencia')
    op.drop_table('seccion')
