"""grupos 3 y 4: registro semanal, diagnostico del alumno, rubrica y nivel final

Revision ID: b7f46024bef7
Revises: 51ee51cc9e65
Create Date: 2026-10-07 17:18:32.816774

Escrita a mano (no autogenerate). Diseño v3, §5 y §6 (correcciones 28 a 34). Todas
llevan auditoría. Las tablas de captura guardan en `fecha_registro` el momento real de
captura (§1.3).

`reporte_semanal_alumno` y `rubrica_registro_semanal` referencian a `semana_reporte`
con una FK compuesta (semana, periodo, año), que impide guardar una semana con un
periodo o año que no le corresponde.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f46024bef7'
down_revision: str | Sequence[str] | None = '51ee51cc9e65'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEMANA_PERIODO_ANIO = ['id_semana', 'id_periodo_academico', 'id_anio_escolar']
REF_SEMANA_PERIODO_ANIO = [f'semana_reporte.{c}' for c in SEMANA_PERIODO_ANIO]


def _auditoria() -> list[sa.Column]:
    return [
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('modificado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=True),
        sa.Column('modificado_en', sa.DateTime(timezone=True), nullable=True),
    ]


def _alumno() -> sa.Column:
    return sa.Column('id_alumno', sa.Integer(), sa.ForeignKey('alumno.id_alumno'), nullable=False)


def _docente() -> sa.Column:
    return sa.Column('id_docente', sa.Integer(), sa.ForeignKey('docente.id_docente'), nullable=False)


def _fecha_registro() -> sa.Column:
    return sa.Column('fecha_registro', sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'semana_reporte',
        sa.Column('id_semana', sa.Integer(), nullable=False),
        sa.Column(
            'id_anio_escolar', sa.Integer(), sa.ForeignKey('año_escolar.id_anio_escolar'),
            nullable=False,
        ),
        sa.Column(
            'id_periodo_academico', sa.Integer(),
            sa.ForeignKey('periodo_academico.id_periodo_academico'), nullable=False,
        ),
        sa.Column('numero_semana', sa.Integer(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id_semana'),
        sa.UniqueConstraint(*SEMANA_PERIODO_ANIO, name='uq_semana_reporte_semana_periodo_anio'),
    )

    op.create_table(
        'asistencia_semanal',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column('id_semana', sa.Integer(), sa.ForeignKey('semana_reporte.id_semana'), nullable=False),
        sa.Column('asistio', sa.Boolean(), nullable=False),
        _docente(),
        _fecha_registro(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'id_semana', name='uq_asistencia_semanal_alumno_semana'),
    )

    op.create_table(
        'evaluacion_diagnostica_alumno',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column(
            'id_evaluacion_diagnostica', sa.Integer(),
            sa.ForeignKey('evaluacion_diagnostica.id_evaluacion_diagnostica'), nullable=False,
        ),
        sa.Column('id_ciclo_evaluado', sa.Integer(), sa.ForeignKey('ciclo_ebr.id_ciclo'), nullable=False),
        sa.Column(
            'id_nivel_rk_entrada', sa.Integer(), sa.ForeignKey('nivel_razkids.id_nivel_rk'),
            nullable=False,
        ),
        sa.Column('aciertos_prueba', sa.Integer(), nullable=True),
        sa.Column('total_prueba', sa.Integer(), nullable=True),
        sa.Column('nivel_colocado', sa.Integer(), sa.ForeignKey('nivel_razkids.id_nivel_rk'), nullable=True),
        sa.Column(
            'id_nivel_general', sa.Integer(), sa.ForeignKey('nivel_general.id_nivel_general'),
            nullable=True,
        ),
        sa.Column('observacion', sa.Text(), nullable=True),
        _docente(),
        _fecha_registro(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'id_alumno', 'id_evaluacion_diagnostica',
            name='uq_evaluacion_diagnostica_alumno_alumno_evaluacion',
        ),
        sa.CheckConstraint(
            'aciertos_prueba IS NULL OR total_prueba IS NULL OR aciertos_prueba <= total_prueba',
            name='ck_evaluacion_diagnostica_alumno_aciertos',
        ),
    )

    op.create_table(
        'reporte_semanal_alumno',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column('id_semana', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('cantidad_lsl', sa.Integer(), nullable=True),
        sa.Column('observaciones', sa.Text(), nullable=True),
        _docente(),
        _fecha_registro(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'id_semana', name='uq_reporte_semanal_alumno_alumno_semana'),
        sa.ForeignKeyConstraint(
            SEMANA_PERIODO_ANIO, REF_SEMANA_PERIODO_ANIO,
            name='fk_reporte_semanal_alumno_semana_periodo_anio',
        ),
        sa.CheckConstraint(
            'cantidad_lsl IS NULL OR cantidad_lsl >= 0', name='ck_reporte_semanal_alumno_cantidad_lsl'
        ),
    )

    op.create_table(
        'libro_subida_nivel',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'id_reporte_semanal', sa.Integer(), sa.ForeignKey('reporte_semanal_alumno.id'),
            nullable=False,
        ),
        sa.Column('titulo_libro', sa.Text(), nullable=False),
        sa.Column('aciertos', sa.Integer(), nullable=False),
        sa.Column('total_preguntas', sa.Integer(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('aciertos <= total_preguntas', name='ck_libro_subida_nivel_aciertos'),
    )

    op.create_table(
        'rubrica_registro_semanal',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column('id_semana', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column(
            'id_nivel_fluidez', sa.Integer(), sa.ForeignKey('nivel_rubrica.id_nivel_rubrica'),
            nullable=True,
        ),
        sa.Column(
            'id_nivel_comprension', sa.Integer(), sa.ForeignKey('nivel_rubrica.id_nivel_rubrica'),
            nullable=True,
        ),
        sa.Column('observacion', sa.Text(), nullable=True),
        _docente(),
        _fecha_registro(),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_alumno', 'id_semana', name='uq_rubrica_registro_semanal_alumno_semana'),
        sa.ForeignKeyConstraint(
            SEMANA_PERIODO_ANIO, REF_SEMANA_PERIODO_ANIO,
            name='fk_rubrica_registro_semanal_semana_periodo_anio',
        ),
    )

    op.create_table(
        'nivel_final_mensual',
        sa.Column('id', sa.Integer(), nullable=False),
        _alumno(),
        sa.Column(
            'id_anio_escolar', sa.Integer(), sa.ForeignKey('año_escolar.id_anio_escolar'),
            nullable=False,
        ),
        sa.Column(
            'id_periodo_academico', sa.Integer(),
            sa.ForeignKey('periodo_academico.id_periodo_academico'), nullable=False,
        ),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column(
            'id_nivel_final', sa.Integer(), sa.ForeignKey('nivel_general.id_nivel_general'),
            nullable=False,
        ),
        sa.Column('ajustado_por_docente', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('id_docente_ajuste', sa.Integer(), sa.ForeignKey('docente.id_docente'), nullable=True),
        sa.Column('justificacion', sa.Text(), nullable=True),
        sa.Column('fecha_calculo', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_ajuste', sa.DateTime(timezone=True), nullable=True),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'id_alumno', 'id_anio_escolar', 'mes', name='uq_nivel_final_mensual_alumno_anio_mes'
        ),
        sa.CheckConstraint('mes BETWEEN 1 AND 12', name='ck_nivel_final_mensual_mes'),
        sa.CheckConstraint(
            'ajustado_por_docente = false OR justificacion IS NOT NULL',
            name='ck_nivel_final_mensual_justificacion',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('nivel_final_mensual')
    op.drop_table('rubrica_registro_semanal')
    op.drop_table('libro_subida_nivel')
    op.drop_table('reporte_semanal_alumno')
    op.drop_table('evaluacion_diagnostica_alumno')
    op.drop_table('asistencia_semanal')
    op.drop_table('semana_reporte')
