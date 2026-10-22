"""registro_vuelo: Registro de Vuelo por alumno, PA y grupo (CU031, CU032, T9)

Revision ID: a4e56dc37694
Revises: 81db4eefa1c4
Create Date: 2026-10-22 08:00:00.000000

Autogenerada y revisada a mano: tipos de fecha sin depender de la app, nombres
explícitos de las FK y la limpieza de datos.

- Elimina `evaluacion_diagnostica`, `evaluacion_diagnostica_alumno`,
  `nivel_final_mensual` y `nivel_general` (datos ficticios) y sus filas de `auditoria`.
- Borra Z1 y Z2 de `nivel_razkids` (ids 28 y 29): la escala queda aa–Z. Antes quita las
  filas de catálogo que pudieran apuntarles.
- Crea `registro_vuelo`: UNIQUE (alumno, año, PA, grupo), FK compuesta a `seccion`,
  `resultado_decimas` entre 0 y 40 e índice (alumno, PA) para la herencia de la entrada.

Downgrade: elimina `registro_vuelo`, reinserta Z1/Z2 con sus ids y recrea vacías las
tablas eliminadas.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4e56dc37694'
down_revision: str | Sequence[str] | None = '81db4eefa1c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_USUARIO = 'usuario.id_usuario'
FK_ALUMNO = 'alumno.id_alumno'
FK_DOCENTE = 'docente.id_docente'
FK_PERIODO = 'periodo_academico.id_periodo_academico'
FK_NIVEL_RK = 'nivel_razkids.id_nivel_rk'
VUELO = 'registro_vuelo'
DIAGNOSTICA = 'evaluacion_diagnostica'
DIAGNOSTICA_ALUMNO = 'evaluacion_diagnostica_alumno'
FINAL_MENSUAL = 'nivel_final_mensual'
GENERAL = 'nivel_general'
ELIMINADAS = (DIAGNOSTICA_ALUMNO, FINAL_MENSUAL, DIAGNOSTICA, GENERAL)
LETRAS_EXTRA = ((28, 'Z1'), (29, 'Z2'))
IDS_EXTRA = '28, 29'


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


def _fk(tabla: str, columna: str, destino: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([columna], [destino], name=f'{tabla}_{columna}_fkey')


def _crear_registro_vuelo() -> None:
    op.create_table(
        VUELO,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('id_seccion', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), nullable=False),
        sa.Column('id_nivel_colocado', sa.Integer(), nullable=True),
        sa.Column('id_nivel_proyeccion', sa.Integer(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        sa.Column('id_rubrica_referencia', sa.Integer(), nullable=True),
        sa.Column('rubrica_version', sa.Integer(), nullable=True),
        sa.Column('resultado_decimas', sa.Integer(), nullable=True),
        sa.Column('id_nivel_entrada_al_decidir', sa.Integer(), nullable=True),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('id_permiso', sa.Integer(), nullable=True),
        _instante('fecha_registro'),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        *_auditoria(VUELO),
        sa.CheckConstraint('resultado_decimas BETWEEN 0 AND 40', name='ck_registro_vuelo_resultado'),
        _fk(VUELO, 'id_alumno', FK_ALUMNO),
        _fk(VUELO, 'id_anio_escolar', 'año_escolar.id_anio_escolar'),
        _fk(VUELO, 'id_periodo_academico', FK_PERIODO),
        _fk(VUELO, 'id_programa', 'programa.id_programa'),
        _fk(VUELO, 'id_nivel_colocado', FK_NIVEL_RK),
        _fk(VUELO, 'id_nivel_proyeccion', FK_NIVEL_RK),
        _fk(VUELO, 'id_nivel_entrada_al_decidir', FK_NIVEL_RK),
        _fk(VUELO, 'id_rubrica_referencia', 'rubrica_mensual.id'),
        _fk(VUELO, 'id_docente', FK_DOCENTE),
        _fk(VUELO, 'id_actividad', 'actividad.id_actividad'),
        _fk(VUELO, 'id_permiso', 'permiso_correccion.id_permiso'),
        sa.ForeignKeyConstraint(
            ['id_seccion', 'id_colegio', 'id_grado'], ['seccion.id_seccion', 'seccion.id_colegio', 'seccion.id_grado'],
            name='fk_registro_vuelo_seccion',
        ),
        sa.PrimaryKeyConstraint('id', name='registro_vuelo_pkey'),
        sa.UniqueConstraint(
            'id_alumno', 'id_anio_escolar', 'id_periodo_academico', 'id_colegio', 'id_grado', 'id_seccion',
            'id_programa', name='uq_registro_vuelo_alumno_periodo_grupo',
        ),
    )
    op.create_index('ix_registro_vuelo_alumno_periodo', VUELO, ['id_alumno', 'id_periodo_academico'])


def upgrade() -> None:
    nombres = ", ".join(f"'{t}'" for t in ELIMINADAS)
    op.execute(sa.text(f"DELETE FROM auditoria WHERE tabla IN ({nombres})"))
    for tabla in ELIMINADAS:
        op.drop_table(tabla)
    op.execute(sa.text(f"DELETE FROM escala_nivel_descriptivo WHERE id_nivel_rk_desde IN ({IDS_EXTRA})"))
    op.execute(sa.text(f"DELETE FROM nivel_esperado_por_grado WHERE id_nivel_rk_esperado IN ({IDS_EXTRA})"))
    op.execute(sa.text(f"DELETE FROM nivel_razkids WHERE id_nivel_rk IN ({IDS_EXTRA})"))
    _crear_registro_vuelo()


# ── Downgrade ───────────────────────────────────────────────────────────────────────

def _recrear_catalogos() -> None:
    valores = ", ".join(f"({id_nivel}, '{letra}')" for id_nivel, letra in LETRAS_EXTRA)
    op.execute(sa.text(f"INSERT INTO nivel_razkids (id_nivel_rk, letra) VALUES {valores} ON CONFLICT DO NOTHING"))
    op.create_table(
        GENERAL,
        sa.Column('id_nivel_general', sa.Integer(), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('nombre_nivel', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id_nivel_general', name='nivel_general_pkey'),
    )
    op.create_table(
        DIAGNOSTICA,
        sa.Column('id_evaluacion_diagnostica', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('fecha_realizacion', sa.Date(), nullable=False),
        *_auditoria(DIAGNOSTICA),
        _fk(DIAGNOSTICA, 'id_periodo_academico', FK_PERIODO),
        sa.PrimaryKeyConstraint('id_evaluacion_diagnostica', name='evaluacion_diagnostica_pkey'),
        sa.UniqueConstraint('id_periodo_academico', name='uq_evaluacion_diagnostica_periodo'),
    )


def _recrear_capturas() -> None:
    op.create_table(
        DIAGNOSTICA_ALUMNO,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_evaluacion_diagnostica', sa.Integer(), nullable=False),
        sa.Column('id_ciclo_evaluado', sa.Integer(), nullable=False),
        sa.Column('id_nivel_rk_entrada', sa.Integer(), nullable=False),
        sa.Column('aciertos_prueba', sa.Integer(), nullable=True),
        sa.Column('total_prueba', sa.Integer(), nullable=True),
        sa.Column('nivel_colocado', sa.Integer(), nullable=True),
        sa.Column('id_nivel_general', sa.Integer(), nullable=True),
        sa.Column('observacion', sa.Text(), nullable=True),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        _instante('fecha_registro'),
        *_auditoria(DIAGNOSTICA_ALUMNO),
        sa.CheckConstraint(
            'aciertos_prueba IS NULL OR total_prueba IS NULL OR aciertos_prueba <= total_prueba',
            name='ck_evaluacion_diagnostica_alumno_aciertos',
        ),
        _fk(DIAGNOSTICA_ALUMNO, 'id_alumno', FK_ALUMNO),
        _fk(DIAGNOSTICA_ALUMNO, 'id_evaluacion_diagnostica', f'{DIAGNOSTICA}.id_evaluacion_diagnostica'),
        _fk(DIAGNOSTICA_ALUMNO, 'id_ciclo_evaluado', 'ciclo_ebr.id_ciclo'),
        _fk(DIAGNOSTICA_ALUMNO, 'id_nivel_rk_entrada', FK_NIVEL_RK),
        _fk(DIAGNOSTICA_ALUMNO, 'nivel_colocado', FK_NIVEL_RK),
        _fk(DIAGNOSTICA_ALUMNO, 'id_nivel_general', f'{GENERAL}.id_nivel_general'),
        _fk(DIAGNOSTICA_ALUMNO, 'id_docente', FK_DOCENTE),
        sa.PrimaryKeyConstraint('id', name='evaluacion_diagnostica_alumno_pkey'),
        sa.UniqueConstraint(
            'id_alumno', 'id_evaluacion_diagnostica', name='uq_evaluacion_diagnostica_alumno_alumno_evaluacion'
        ),
    )
    op.create_table(
        FINAL_MENSUAL,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column('id_nivel_final', sa.Integer(), nullable=False),
        sa.Column('ajustado_por_docente', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('id_docente_ajuste', sa.Integer(), nullable=True),
        sa.Column('justificacion', sa.Text(), nullable=True),
        _instante('fecha_calculo'),
        _instante('fecha_ajuste', nullable=True),
        *_auditoria(FINAL_MENSUAL),
        sa.CheckConstraint(
            'ajustado_por_docente = false OR justificacion IS NOT NULL', name='ck_nivel_final_mensual_justificacion'
        ),
        sa.CheckConstraint('mes BETWEEN 1 AND 12', name='ck_nivel_final_mensual_mes'),
        _fk(FINAL_MENSUAL, 'id_alumno', FK_ALUMNO),
        _fk(FINAL_MENSUAL, 'id_anio_escolar', 'año_escolar.id_anio_escolar'),
        _fk(FINAL_MENSUAL, 'id_periodo_academico', FK_PERIODO),
        _fk(FINAL_MENSUAL, 'id_nivel_final', f'{GENERAL}.id_nivel_general'),
        _fk(FINAL_MENSUAL, 'id_docente_ajuste', FK_DOCENTE),
        sa.PrimaryKeyConstraint('id', name='nivel_final_mensual_pkey'),
        sa.UniqueConstraint('id_alumno', 'id_anio_escolar', 'mes', name='uq_nivel_final_mensual_alumno_anio_mes'),
    )


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM auditoria WHERE tabla = '{VUELO}'"))
    op.drop_index('ix_registro_vuelo_alumno_periodo', table_name=VUELO)
    op.drop_table(VUELO)
    _recrear_catalogos()
    _recrear_capturas()
