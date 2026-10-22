"""lectura_semanal: Seguimiento de Lectura por grupo, subprograma y semana (CU023-CU026, T7)

Revision ID: c4f20778f2b5
Revises: 258390935eac
Create Date: 2026-10-22 06:18:05.329305

Autogenerada y revisada a mano: tipos de fecha sin depender de la app, nombres
explícitos de las FK y el traslado de datos de `semana_reporte`.

- Elimina `reporte_semanal_alumno`, `libro_subida_nivel` y `rubrica_registro_semanal`
  (datos ficticios; la rúbrica semanal la reemplaza `rubrica_mensual` en T8) y sus filas
  de `auditoria`.
- `semana_reporte` deja de depender de los PA: se quitan `id_periodo_academico` y la
  UNIQUE compuesta que usaban las FK de esas tablas.
- Crea `lectura_semanal` (UNIQUE por grupo, subprograma, año, mes y semana; UNIQUE
  (clase de referencia, subprograma), REGLA PROVISIONAL L1) y `lectura_semanal_alumno`
  (LSN todo o nada, máximo 5 o 10, nota entre 0 y el máximo, LSL ≥ 0).

Downgrade: elimina las tablas nuevas, devuelve a cada semana el PA del calendario
general que contiene su lunes (borra las que caen fuera de todo PA, como las generaba el
comando antes) y recrea vacías las tablas eliminadas.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f20778f2b5'
down_revision: str | Sequence[str] | None = '258390935eac'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
FK_ALUMNO = 'alumno.id_alumno'
FK_DOCENTE = 'docente.id_docente'
FK_ACTIVIDAD = 'actividad.id_actividad'
FK_PERMISO = 'permiso_correccion.id_permiso'
FK_NIVEL_RUBRICA = 'nivel_rubrica.id_nivel_rubrica'
LECTURA = 'lectura_semanal'
LECTURA_ALUMNO = 'lectura_semanal_alumno'
SEMANA = 'semana_reporte'
REPORTE = 'reporte_semanal_alumno'
LIBRO = 'libro_subida_nivel'
RUBRICA = 'rubrica_registro_semanal'
UQ_SEMANA = 'uq_semana_reporte_semana_periodo_anio'
FK_SEMANA_PERIODO = 'semana_reporte_id_periodo_academico_fkey'
SEMANA_PERIODO_ANIO = ['id_semana', 'id_periodo_academico', 'id_anio_escolar']
REF_SEMANA_PERIODO_ANIO = [f'{SEMANA}.{c}' for c in SEMANA_PERIODO_ANIO]


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


def _captura(tabla: str) -> list:
    """Quién, con qué actividad y permiso, y cuándo capturó el registro; su versión."""
    return [
        sa.Column('id_docente', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('id_permiso', sa.Integer(), nullable=True),
        _instante('fecha_registro'),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.ForeignKeyConstraint(['id_docente'], [FK_DOCENTE], name=f'{tabla}_id_docente_fkey'),
        sa.ForeignKeyConstraint(['id_actividad'], [FK_ACTIVIDAD], name=f'{tabla}_id_actividad_fkey'),
        sa.ForeignKeyConstraint(['id_permiso'], [FK_PERMISO], name=f'{tabla}_id_permiso_fkey'),
    ]


def _crear_lectura() -> None:
    op.create_table(
        LECTURA,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('id_seccion', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column('id_semana', sa.Integer(), nullable=False),
        sa.Column('id_clase_referencia', sa.Integer(), nullable=False),
        sa.Column('estado', sa.Text(), server_default='normal', nullable=False),
        sa.Column('motivo_no_realizada', sa.Text(), nullable=True),
        *_captura(LECTURA),
        *_auditoria(LECTURA),
        sa.CheckConstraint(
            "estado <> 'no_realizada' OR length(trim(coalesce(motivo_no_realizada, ''))) > 0",
            name='ck_lectura_semanal_motivo',
        ),
        sa.CheckConstraint("estado IN ('normal', 'no_realizada')", name='ck_lectura_semanal_estado'),
        sa.CheckConstraint('mes BETWEEN 1 AND 12', name='ck_lectura_semanal_mes'),
        sa.ForeignKeyConstraint(
            ['id_seccion', 'id_colegio', 'id_grado'], ['seccion.id_seccion', 'seccion.id_colegio', 'seccion.id_grado'],
            name='fk_lectura_semanal_seccion',
        ),
        sa.ForeignKeyConstraint(['id_programa'], ['programa.id_programa'], name='lectura_semanal_id_programa_fkey'),
        sa.ForeignKeyConstraint(
            ['id_anio_escolar'], ['año_escolar.id_anio_escolar'], name='lectura_semanal_id_anio_escolar_fkey'
        ),
        sa.ForeignKeyConstraint(['id_semana'], [f'{SEMANA}.id_semana'], name='lectura_semanal_id_semana_fkey'),
        sa.ForeignKeyConstraint(
            ['id_clase_referencia'], ['clase.id_clase'], name='lectura_semanal_id_clase_referencia_fkey'
        ),
        sa.PrimaryKeyConstraint('id', name='lectura_semanal_pkey'),
        sa.UniqueConstraint('id_clase_referencia', 'id_programa', name='uq_lectura_semanal_clase_programa'),
        sa.UniqueConstraint(
            'id_colegio', 'id_grado', 'id_seccion', 'id_programa', 'id_anio_escolar', 'mes', 'id_semana',
            name='uq_lectura_semanal_grupo_semana',
        ),
    )
    op.create_table(
        LECTURA_ALUMNO,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_lectura_semanal', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('lsn_titulo', sa.Text(), nullable=True),
        sa.Column('lsn_nota', sa.Integer(), nullable=True),
        sa.Column('lsn_maximo', sa.Integer(), nullable=True),
        sa.Column('cantidad_lsl', sa.Integer(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        *_captura(LECTURA_ALUMNO),
        *_auditoria(LECTURA_ALUMNO),
        sa.CheckConstraint(
            '(lsn_titulo IS NULL AND lsn_nota IS NULL AND lsn_maximo IS NULL) OR '
            '(length(trim(lsn_titulo)) > 0 AND lsn_nota IS NOT NULL AND lsn_maximo IS NOT NULL)',
            name='ck_lectura_semanal_alumno_lsn_completo',
        ),
        sa.CheckConstraint('cantidad_lsl IS NULL OR cantidad_lsl >= 0', name='ck_lectura_semanal_alumno_cantidad_lsl'),
        sa.CheckConstraint('lsn_maximo IS NULL OR lsn_maximo IN (5, 10)', name='ck_lectura_semanal_alumno_lsn_maximo'),
        sa.CheckConstraint(
            'lsn_nota IS NULL OR (lsn_nota >= 0 AND lsn_nota <= lsn_maximo)', name='ck_lectura_semanal_alumno_lsn_nota'
        ),
        sa.ForeignKeyConstraint(['id_alumno'], [FK_ALUMNO], name='lectura_semanal_alumno_id_alumno_fkey'),
        sa.ForeignKeyConstraint(
            ['id_lectura_semanal'], [f'{LECTURA}.id'], name='lectura_semanal_alumno_id_lectura_semanal_fkey'
        ),
        sa.PrimaryKeyConstraint('id', name='lectura_semanal_alumno_pkey'),
        sa.UniqueConstraint('id_lectura_semanal', 'id_alumno', name='uq_lectura_semanal_alumno_tabla_alumno'),
    )


def upgrade() -> None:
    op.execute(sa.text(f"DELETE FROM auditoria WHERE tabla IN ('{REPORTE}', '{LIBRO}', '{RUBRICA}')"))
    op.drop_table(LIBRO)
    op.drop_table(REPORTE)
    op.drop_table(RUBRICA)
    op.drop_constraint(UQ_SEMANA, SEMANA, type_='unique')
    op.drop_constraint(FK_SEMANA_PERIODO, SEMANA, type_='foreignkey')
    op.drop_column(SEMANA, 'id_periodo_academico')
    _crear_lectura()


# ── Downgrade ───────────────────────────────────────────────────────────────────────

def _semanas_con_su_pa() -> None:
    """Cada semana vuelve a tener el PA del calendario general que contiene su lunes; las
    que caen fuera de todo PA se eliminan (el comando de antes solo las generaba dentro)."""
    op.add_column(SEMANA, sa.Column('id_periodo_academico', sa.Integer(), nullable=True))
    op.execute(sa.text(f"""
        UPDATE {SEMANA} s SET id_periodo_academico = pa.id_periodo_academico
        FROM calendario c
        JOIN calendario_periodo cp ON cp.id_calendario = c.id_calendario AND cp.tipo = 'PA'
        JOIN periodo_academico pa ON pa.id_anio_escolar = c.id_anio_escolar AND pa.numero = cp.numero
        WHERE c.id_colegio IS NULL AND c.id_anio_escolar = s.id_anio_escolar
          AND s.fecha_inicio BETWEEN cp.fecha_inicio AND cp.fecha_fin
    """))
    op.execute(sa.text(f"DELETE FROM {SEMANA} WHERE id_periodo_academico IS NULL"))
    op.alter_column(SEMANA, 'id_periodo_academico', nullable=False)
    op.create_foreign_key(
        FK_SEMANA_PERIODO, SEMANA, 'periodo_academico', ['id_periodo_academico'], ['id_periodo_academico']
    )
    op.create_unique_constraint(UQ_SEMANA, SEMANA, SEMANA_PERIODO_ANIO)


def _semanal(tabla: str) -> list:
    """Columnas comunes de las tablas semanales anteriores (alumno, semana y docente)."""
    return [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_semana', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        _instante('fecha_registro'),
        *_auditoria(tabla),
        sa.ForeignKeyConstraint(['id_alumno'], [FK_ALUMNO], name=f'{tabla}_id_alumno_fkey'),
        sa.ForeignKeyConstraint(['id_docente'], [FK_DOCENTE], name=f'{tabla}_id_docente_fkey'),
        sa.ForeignKeyConstraint(
            SEMANA_PERIODO_ANIO, REF_SEMANA_PERIODO_ANIO, name=f'fk_{tabla}_semana_periodo_anio'
        ),
        sa.PrimaryKeyConstraint('id', name=f'{tabla}_pkey'),
        sa.UniqueConstraint('id_alumno', 'id_semana', name=f'uq_{tabla}_alumno_semana'),
    ]


def _recrear_anteriores() -> None:
    op.create_table(
        RUBRICA,
        *_semanal(RUBRICA),
        sa.Column('id_nivel_fluidez', sa.Integer(), nullable=True),
        sa.Column('id_nivel_comprension', sa.Integer(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['id_nivel_fluidez'], [FK_NIVEL_RUBRICA], name=f'{RUBRICA}_id_nivel_fluidez_fkey'),
        sa.ForeignKeyConstraint(
            ['id_nivel_comprension'], [FK_NIVEL_RUBRICA], name=f'{RUBRICA}_id_nivel_comprension_fkey'
        ),
    )
    op.create_table(
        REPORTE,
        *_semanal(REPORTE),
        sa.Column('cantidad_lsl', sa.Integer(), nullable=True),
        sa.Column('observaciones', sa.Text(), nullable=True),
        sa.CheckConstraint('cantidad_lsl IS NULL OR cantidad_lsl >= 0', name=f'ck_{REPORTE}_cantidad_lsl'),
    )
    op.create_table(
        LIBRO,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_reporte_semanal', sa.Integer(), nullable=False),
        sa.Column('titulo_libro', sa.Text(), nullable=False),
        sa.Column('aciertos', sa.Integer(), nullable=False),
        sa.Column('total_preguntas', sa.Integer(), nullable=False),
        *_auditoria(LIBRO),
        sa.CheckConstraint('aciertos <= total_preguntas', name=f'ck_{LIBRO}_aciertos'),
        sa.ForeignKeyConstraint(['id_reporte_semanal'], [f'{REPORTE}.id'], name=f'{LIBRO}_id_reporte_semanal_fkey'),
        sa.PrimaryKeyConstraint('id', name=f'{LIBRO}_pkey'),
    )


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM auditoria WHERE tabla IN ('{LECTURA}', '{LECTURA_ALUMNO}')"))
    op.drop_table(LECTURA_ALUMNO)
    op.drop_table(LECTURA)
    _semanas_con_su_pa()
    _recrear_anteriores()
