"""rubrica_mensual: rúbrica mensual con niveles 0-4 y descriptores por ciclo (CU029, CU030, T8)

Revision ID: 81db4eefa1c4
Revises: c4f20778f2b5
Create Date: 2026-10-22 07:10:00.000000

Autogenerada y revisada a mano: tipos de fecha sin depender de la app, nombres
explícitos de las FK, el traslado de datos de `nivel_rubrica` y la carga de los
descriptores.

- `nivel_rubrica`: + `valor` (código 0-4, CHECK y UNIQUE por programa y dimensión), con
  los mismos ids: Fluidez de Alfabetización `valor = orden`; las demás `valor = orden +
  1`. Nombres: "Pre Inicio" → "Pre-inicio", "Proceso" → "En proceso", "Destacado" →
  "Avanzado".
- `descriptor_rubrica` (catálogo puro, PK nivel + ciclo) con sus 33 textos de CU029
  (los mismos de app/datos/descriptores_rubrica.json). Se insertan por nombre de
  programa, dimensión, valor y ciclo: en una BD sin catálogos no inserta nada y los
  carga después `inicializar`.
- `rubrica_mensual`: una evaluación por alumno, año, mes y grupo (FK compuesta a
  `seccion`), con el PA del mes (R1), los dos niveles (nullable), captura y auditoría.

Downgrade: elimina lo creado y devuelve los nombres anteriores.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81db4eefa1c4'
down_revision: str | Sequence[str] | None = 'c4f20778f2b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIVEL = 'nivel_rubrica'
DESCRIPTOR = 'descriptor_rubrica'
RUBRICA = 'rubrica_mensual'
FK_USUARIO = 'usuario.id_usuario'
FK_NIVEL = 'nivel_rubrica.id_nivel_rubrica'
UQ_VALOR = 'uq_nivel_rubrica_programa_dimension_valor'
CK_VALOR = 'ck_nivel_rubrica_valor'
NOMBRES = (('Pre Inicio', 'Pre-inicio'), ('Proceso', 'En proceso'), ('Destacado', 'Avanzado'))

# (programa, ciclo, dimensión, valor, texto), copiados textualmente de CU029.
DESCRIPTORES = [
    ('Alfabetización', 'III', 'Fluidez', 0, 'No puede leer porque le cuesta reconocer consonantes y vocales.'),
    ('Alfabetización', 'III', 'Fluidez', 1, 'Lee palabra por palabra con esfuerzo. No respeta entonación ni signos de puntuación. Se detiene frecuentemente.'),
    ('Alfabetización', 'III', 'Fluidez', 2, 'Lee frases cortas con cierta dificultad. Empieza a respetar algunos signos de puntuación como puntos y comas. Ritmo irregular.'),
    ('Alfabetización', 'III', 'Fluidez', 3, 'Lee frases completas con cierta fluidez. Comienza a respetar puntos y entonar preguntas simples.'),
    ('Alfabetización', 'III', 'Fluidez', 4, 'Lee textos cortos de forma fluida, con entonación y ritmo adecuado. Identifica signos de puntuación con apoyo.'),
    ('Alfabetización', 'III', 'Comprensión', 1, 'No comprende lo leído. Solo realiza una lectura simple apoyándose en imágenes. No responde preguntas literales.'),
    ('Alfabetización', 'III', 'Comprensión', 2, 'Identifica personajes, lugares o hechos, a través de palabras sencillas del texto apoyándose en las imágenes.'),
    ('Alfabetización', 'III', 'Comprensión', 3, 'Identifica la idea principal del texto. Responde preguntas literales con apoyo.'),
    ('Alfabetización', 'III', 'Comprensión', 4, 'Identifica la idea principal del texto. Responde preguntas literales con autonomía.'),
    ('Comprensión Lectora', 'III', 'Fluidez', 1, 'Lee palabra por palabra con esfuerzo. No respeta entonación ni signos de puntuación. Se detiene frecuentemente.'),
    ('Comprensión Lectora', 'III', 'Fluidez', 2, 'Lee frases completas con cierta dificultad. Empieza a respetar algunos signos de puntuación como puntos y comas haciendo pausas cortas. Ritmo irregular.'),
    ('Comprensión Lectora', 'III', 'Fluidez', 3, 'Lee frases completas con cierta fluidez. Comienza a respetar puntos y entonar preguntas simples.'),
    ('Comprensión Lectora', 'III', 'Fluidez', 4, 'Lee textos cortos de forma fluida, con entonación y ritmo adecuado. Identifica signos de puntuación sin apoyo.'),
    ('Comprensión Lectora', 'III', 'Comprensión', 1, 'Identifica la idea principal del texto. Responde preguntas literales con apoyo.'),
    ('Comprensión Lectora', 'III', 'Comprensión', 2, 'Reconoce ideas básicas. Responde preguntas literales y hace conexiones simples con imágenes o vivencias.'),
    ('Comprensión Lectora', 'III', 'Comprensión', 3, 'Identifica la idea principal del texto, personajes y hechos. Realiza predicciones sobre lo que podría suceder en la historia.'),
    ('Comprensión Lectora', 'III', 'Comprensión', 4, 'Hace inferencias simples. Interpreta el mensaje del texto con cierta autonomía.'),
    ('Comprensión Lectora', 'IV', 'Fluidez', 1, 'Lee de forma entrecortada con pausas constantes. Reconoce palabras sencillas pero se detiene con frecuencia.'),
    ('Comprensión Lectora', 'IV', 'Fluidez', 2, 'Lee frases completas con ritmo irregular. Comienza a usar entonación básica, haciendo pausas correctamente.'),
    ('Comprensión Lectora', 'IV', 'Fluidez', 3, 'Lee con ritmo y entonación adecuada la mayor parte del texto.'),
    ('Comprensión Lectora', 'IV', 'Fluidez', 4, 'Lee en forma fluida y expresiva. Entona con naturalidad según el tipo de texto.'),
    ('Comprensión Lectora', 'IV', 'Comprensión', 1, 'Reconoce ideas básicas. Responde preguntas literales y hace conexiones simples con imágenes o vivencias.'),
    ('Comprensión Lectora', 'IV', 'Comprensión', 2, 'Identifica la idea principal del texto, personajes y hechos. Realiza predicciones sobre lo que podría suceder en la historia.'),
    ('Comprensión Lectora', 'IV', 'Comprensión', 3, 'Identifica ideas principales y secundarias. Hace inferencias simples y justifica sus respuestas.'),
    ('Comprensión Lectora', 'IV', 'Comprensión', 4, 'Crea un análisis crítico personal del texto, a partir de sus experiencias personales.'),
    ('Comprensión Lectora', 'V', 'Fluidez', 1, 'Lee con ritmo aceptable, reconociendo el tipo de texto y comienza a variar la entonación.'),
    ('Comprensión Lectora', 'V', 'Fluidez', 2, 'Lee con ritmo y entonación adecuada la mayor parte del texto.'),
    ('Comprensión Lectora', 'V', 'Fluidez', 3, 'Lee en forma fluida y expresiva. Entona con naturalidad según el tipo de texto.'),
    ('Comprensión Lectora', 'V', 'Fluidez', 4, 'Lee con fluidez y naturalidad textos complejos. Adapta entonación según propósito comunicativo.'),
    ('Comprensión Lectora', 'V', 'Comprensión', 1, 'Identifica la idea principal del texto, personajes y hechos. Realiza predicciones sobre lo que podría suceder en la historia.'),
    ('Comprensión Lectora', 'V', 'Comprensión', 2, 'Identifica ideas principales y secundarias. Hace inferencias simples con apoyo.'),
    ('Comprensión Lectora', 'V', 'Comprensión', 3, 'Comprende textos con estructura compleja. Interpreta y reflexiona con apoyo mínimo.'),
    ('Comprensión Lectora', 'V', 'Comprensión', 4, 'Analiza textos críticamente. Identifica intención, tono y hace inferencias profundas con argumentos claros.'),
]


def _literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def _instante(nombre: str, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def _renombrar(pares) -> None:
    for antes, despues in pares:
        op.execute(sa.text(f"UPDATE {NIVEL} SET nombre_nivel = {_literal(despues)} WHERE nombre_nivel = {_literal(antes)}"))


def _valores_de_nivel() -> None:
    op.add_column(NIVEL, sa.Column('valor', sa.Integer(), nullable=True))
    op.execute(sa.text(f"""
        UPDATE {NIVEL} n
        SET valor = CASE WHEN p.nombre = 'Alfabetización' AND n.dimension = 'Fluidez' THEN n.orden ELSE n.orden + 1 END
        FROM programa p
        WHERE p.id_programa = n.id_programa
    """))
    op.alter_column(NIVEL, 'valor', nullable=False)
    op.create_check_constraint(CK_VALOR, NIVEL, 'valor BETWEEN 0 AND 4')
    op.create_unique_constraint(UQ_VALOR, NIVEL, ['id_programa', 'dimension', 'valor'])
    _renombrar(NOMBRES)


def _descriptores() -> None:
    op.create_table(
        DESCRIPTOR,
        sa.Column('id_nivel_rubrica', sa.Integer(), nullable=False),
        sa.Column('id_ciclo', sa.Integer(), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['id_ciclo'], ['ciclo_ebr.id_ciclo'], name='descriptor_rubrica_id_ciclo_fkey'),
        sa.ForeignKeyConstraint(['id_nivel_rubrica'], [FK_NIVEL], name='descriptor_rubrica_id_nivel_rubrica_fkey'),
        sa.PrimaryKeyConstraint('id_nivel_rubrica', 'id_ciclo', name='descriptor_rubrica_pkey'),
    )
    valores = ',\n'.join(
        f"({_literal(p)}, {_literal(c)}, {_literal(d)}, {v}, {_literal(t)})" for p, c, d, v, t in DESCRIPTORES
    )
    op.execute(sa.text(f"""
        INSERT INTO {DESCRIPTOR} (id_nivel_rubrica, id_ciclo, texto)
        SELECT n.id_nivel_rubrica, c.id_ciclo, d.texto
        FROM (VALUES {valores}) AS d (programa, ciclo, dimension, valor, texto)
        JOIN programa p ON p.nombre = d.programa
        JOIN {NIVEL} n ON n.id_programa = p.id_programa AND n.dimension = d.dimension AND n.valor = d.valor
        JOIN ciclo_ebr c ON c.nombre = d.ciclo
        ON CONFLICT DO NOTHING
    """))


def _rubrica_mensual() -> None:
    op.create_table(
        RUBRICA,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_alumno', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column('id_colegio', sa.Integer(), nullable=False),
        sa.Column('id_grado', sa.Integer(), nullable=False),
        sa.Column('id_seccion', sa.Integer(), nullable=False),
        sa.Column('id_programa', sa.Integer(), nullable=False),
        sa.Column('id_periodo_academico', sa.Integer(), nullable=False),
        sa.Column('id_nivel_fluidez', sa.Integer(), nullable=True),
        sa.Column('id_nivel_comprension', sa.Integer(), nullable=True),
        sa.Column('id_docente', sa.Integer(), nullable=False),
        sa.Column('id_actividad', sa.Uuid(), nullable=True),
        sa.Column('id_permiso', sa.Integer(), nullable=True),
        _instante('fecha_registro'),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('creado_por', sa.Integer(), nullable=False),
        _instante('creado_en'),
        sa.Column('modificado_por', sa.Integer(), nullable=True),
        _instante('modificado_en', nullable=True),
        sa.CheckConstraint('mes BETWEEN 1 AND 12', name='ck_rubrica_mensual_mes'),
        sa.ForeignKeyConstraint(['id_alumno'], ['alumno.id_alumno'], name='rubrica_mensual_id_alumno_fkey'),
        sa.ForeignKeyConstraint(
            ['id_anio_escolar'], ['año_escolar.id_anio_escolar'], name='rubrica_mensual_id_anio_escolar_fkey'
        ),
        sa.ForeignKeyConstraint(['id_programa'], ['programa.id_programa'], name='rubrica_mensual_id_programa_fkey'),
        sa.ForeignKeyConstraint(
            ['id_periodo_academico'], ['periodo_academico.id_periodo_academico'],
            name='rubrica_mensual_id_periodo_academico_fkey',
        ),
        sa.ForeignKeyConstraint(['id_nivel_fluidez'], [FK_NIVEL], name='rubrica_mensual_id_nivel_fluidez_fkey'),
        sa.ForeignKeyConstraint(['id_nivel_comprension'], [FK_NIVEL], name='rubrica_mensual_id_nivel_comprension_fkey'),
        sa.ForeignKeyConstraint(['id_docente'], ['docente.id_docente'], name='rubrica_mensual_id_docente_fkey'),
        sa.ForeignKeyConstraint(['id_actividad'], ['actividad.id_actividad'], name='rubrica_mensual_id_actividad_fkey'),
        sa.ForeignKeyConstraint(
            ['id_permiso'], ['permiso_correccion.id_permiso'], name='rubrica_mensual_id_permiso_fkey'
        ),
        sa.ForeignKeyConstraint(
            ['id_seccion', 'id_colegio', 'id_grado'], ['seccion.id_seccion', 'seccion.id_colegio', 'seccion.id_grado'],
            name='fk_rubrica_mensual_seccion',
        ),
        sa.ForeignKeyConstraint(['creado_por'], [FK_USUARIO], name='rubrica_mensual_creado_por_fkey'),
        sa.ForeignKeyConstraint(['modificado_por'], [FK_USUARIO], name='rubrica_mensual_modificado_por_fkey'),
        sa.PrimaryKeyConstraint('id', name='rubrica_mensual_pkey'),
        sa.UniqueConstraint(
            'id_alumno', 'id_anio_escolar', 'mes', 'id_colegio', 'id_grado', 'id_seccion', 'id_programa',
            name='uq_rubrica_mensual_alumno_mes_grupo',
        ),
    )
    op.create_index('ix_rubrica_mensual_colegio_periodo', RUBRICA, ['id_colegio', 'id_periodo_academico'])


def upgrade() -> None:
    _valores_de_nivel()
    _descriptores()
    _rubrica_mensual()


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM auditoria WHERE tabla = '{RUBRICA}'"))
    op.drop_index('ix_rubrica_mensual_colegio_periodo', table_name=RUBRICA)
    op.drop_table(RUBRICA)
    op.drop_table(DESCRIPTOR)
    _renombrar((despues, antes) for antes, despues in NOMBRES)
    op.drop_constraint(UQ_VALOR, NIVEL, type_='unique')
    op.drop_constraint(CK_VALOR, NIVEL, type_='check')
    op.drop_column(NIVEL, 'valor')
