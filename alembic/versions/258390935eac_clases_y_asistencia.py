"""clases y asistencia: la clase es colegio × grado × sección (CU013, CU022, T6)

Revision ID: 258390935eac
Revises: ffcee55fdf96
Create Date: 2026-10-21 23:10:00.000000

Autogenerada y revisada a mano: `asistencia` se recrea en vez de alterarse (sus filas se
borran: datos ficticios, decisión del equipo), tipos de fecha sin depender de la app y
nombres explícitos de las FK.

Crea:
- `clase`: grupo (FK compuesta a `seccion`), PA, tipo (habitual | adicional),
  `fecha_original`, `fecha` vigente, `lista_congelada_en` y `version`. UNIQUE (grupo,
  fecha) y único parcial (grupo, fecha_original) WHERE tipo = 'habitual'.
- `clase_alumno`: lista congelada de cada clase, con el subprograma del alumno.
- `clase_reprogramacion`: historial de traslados (motivo no vacío).
- `asistencia` nueva: (id_clase, id_alumno) → `clase_alumno`, estado, observación,
  docente, actividad, permiso (CU028), `fecha_registro`, `version` y auditoría. UNIQUE
  (id_clase, id_alumno). Sin `fecha`, `id_colegio`, `id_grado` ni `id_programa`.

Borra las asistencias existentes y sus filas de `auditoria` (apuntarían, por id, a las
filas nuevas). Downgrade: elimina lo creado y recrea la `asistencia` anterior, vacía.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '258390935eac'
down_revision: str | Sequence[str] | None = 'ffcee55fdf96'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
FK_ALUMNO = 'alumno.id_alumno'
FK_PROGRAMA = 'programa.id_programa'
FK_DOCENTE = 'docente.id_docente'
CLASE = 'clase'
ASISTENCIA = 'asistencia'
REPROGRAMACION = 'clase_reprogramacion'
CHECK_ESTADO = "estado IS NULL OR estado IN ('presente', 'tardanza', 'ausente')"
GRUPO = ['id_colegio', 'id_grado', 'id_seccion']


def _instante(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def _auditoria(tabla: str) -> list:
    """Columnas y FK de auditoría de una tabla de negocio."""
    return [
        sa.Column('creado_por', sa.Integer(), nullable=False),
        _instante('creado_en'),
        sa.Column('modificado_por', sa.Integer(), nullable=True),
        _instante('modificado_en', nullable=True),
        sa.ForeignKeyConstraint(['creado_por'], [FK_USUARIO], name=f'{tabla}_creado_por_fkey'),
        sa.ForeignKeyConstraint(['modificado_por'], [FK_USUARIO], name=f'{tabla}_modificado_por_fkey'),
    ]


def _crear_clases() -> None:
    op.create_table(
        CLASE,
        sa.Column('id_clase', sa.Integer(), nullable=False),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('id_seccion', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.Text(), nullable=False),
        sa.Column('fecha_original', sa.Date(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        _instante('lista_congelada_en', nullable=True),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        *_auditoria(CLASE),
        sa.CheckConstraint("tipo IN ('habitual', 'adicional')", name='ck_clase_tipo'),
        sa.ForeignKeyConstraint(
            ['id_periodo_academico'], ['periodo_academico.id_periodo_academico'],
            name='clase_id_periodo_academico_fkey',
        ),
        sa.ForeignKeyConstraint(
            ['id_seccion', 'id_colegio', 'id_grado'], ['seccion.id_seccion', 'seccion.id_colegio', 'seccion.id_grado'],
            name='fk_clase_seccion',
        ),
        sa.PrimaryKeyConstraint('id_clase'),
        sa.UniqueConstraint(*GRUPO, 'fecha', name='uq_clase_grupo_fecha'),
    )
    op.create_index('ix_clase_colegio_fecha', CLASE, ['id_colegio', 'fecha'], unique=False)
    op.create_index(
        'uq_clase_habitual_fecha_original', CLASE, [*GRUPO, 'fecha_original'], unique=True,
        postgresql_where=sa.text("tipo = 'habitual'"), sqlite_where=sa.text("tipo = 'habitual'"),
    )
    op.create_table(
        'clase_alumno',
        sa.Column('id_clase', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['id_alumno'], [FK_ALUMNO], name='clase_alumno_id_alumno_fkey'),
        sa.ForeignKeyConstraint(['id_clase'], ['clase.id_clase'], name='clase_alumno_id_clase_fkey'),
        sa.ForeignKeyConstraint(['id_programa'], [FK_PROGRAMA], name='clase_alumno_id_programa_fkey'),
        sa.PrimaryKeyConstraint('id_clase', 'id_alumno'),
    )
    op.create_table(
        REPROGRAMACION,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_clase', sa.Integer(), nullable=False),
        sa.Column('fecha_anterior', sa.Date(), nullable=False),
        sa.Column('fecha_nueva', sa.Date(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('id_usuario', sa.Integer(), nullable=False),
        _instante('fecha'),
        sa.CheckConstraint('length(trim(motivo)) > 0', name='ck_clase_reprogramacion_motivo'),
        sa.ForeignKeyConstraint(['id_clase'], ['clase.id_clase'], name='clase_reprogramacion_id_clase_fkey'),
        sa.ForeignKeyConstraint(['id_usuario'], [FK_USUARIO], name='clase_reprogramacion_id_usuario_fkey'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_clase_reprogramacion_clase', REPROGRAMACION, ['id_clase', 'fecha'], unique=False)


def _crear_asistencia_nueva() -> None:
    op.create_table(
        ASISTENCIA,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_clase', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('estado', sa.Text(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('id_permiso', sa.Integer(), nullable=True),
        _instante('fecha_registro'),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        *_auditoria(ASISTENCIA),
        sa.CheckConstraint(CHECK_ESTADO, name='ck_asistencia_estado'),
        sa.ForeignKeyConstraint(
            ['id_clase', 'id_alumno'], ['clase_alumno.id_clase', 'clase_alumno.id_alumno'],
            name='fk_asistencia_clase_alumno',
        ),
        sa.ForeignKeyConstraint(['id_docente'], [FK_DOCENTE], name='asistencia_id_docente_fkey'),
        sa.ForeignKeyConstraint(['id_actividad'], ['actividad.id_actividad'], name='asistencia_id_actividad_fkey'),
        sa.ForeignKeyConstraint(['id_permiso'], ['permiso_correccion.id_permiso'], name='asistencia_id_permiso_fkey'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_clase', 'id_alumno', name='uq_asistencia_clase_alumno'),
    )


def _crear_asistencia_anterior() -> None:
    op.create_table(
        ASISTENCIA,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('id_programa', sa.Integer(), nullable=False),
        sa.Column('estado', sa.Text(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        _instante('fecha_registro'),
        *_auditoria(ASISTENCIA),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.CheckConstraint(CHECK_ESTADO, name='ck_asistencia_estado'),
        sa.ForeignKeyConstraint(['id_alumno'], [FK_ALUMNO], name='asistencia_id_alumno_fkey'),
        sa.ForeignKeyConstraint(['id_programa'], [FK_PROGRAMA], name='asistencia_id_programa_fkey'),
        sa.ForeignKeyConstraint(['id_docente'], [FK_DOCENTE], name='asistencia_id_docente_fkey'),
        sa.ForeignKeyConstraint(['id_colegio'], ['colegio.id_colegio'], name='asistencia_id_colegio_fkey'),
        sa.ForeignKeyConstraint(['id_grado'], ['grado.id_grado'], name='asistencia_id_grado_fkey'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'fecha', 'id_programa', name='uq_asistencia_alumno_fecha_programa'),
    )
    op.create_index('ix_asistencia_fecha', ASISTENCIA, ['fecha'], unique=False)
    op.create_index('ix_asistencia_aula_fecha', ASISTENCIA, ['id_colegio', 'id_grado', 'fecha'], unique=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.text("DELETE FROM auditoria WHERE tabla = 'asistencia'"))
    op.drop_table(ASISTENCIA)
    _crear_clases()
    _crear_asistencia_nueva()


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("DELETE FROM auditoria WHERE tabla IN ('asistencia', 'clase')"))
    op.drop_table(ASISTENCIA)
    op.drop_index('ix_clase_reprogramacion_clase', table_name=REPROGRAMACION)
    op.drop_table(REPROGRAMACION)
    op.drop_table('clase_alumno')
    op.drop_index('uq_clase_habitual_fecha_original', table_name=CLASE)
    op.drop_index('ix_clase_colegio_fecha', table_name=CLASE)
    op.drop_table(CLASE)
    _crear_asistencia_anterior()
