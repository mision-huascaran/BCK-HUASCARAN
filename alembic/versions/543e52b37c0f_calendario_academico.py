"""calendario_academico: calendario general y particular por colegio (CU027, T2)

Revision ID: 543e52b37c0f
Revises: 0f7bffeb9082
Create Date: 2026-10-21 20:00:00.000000

Diseño v4 §3. Escrita a mano a partir de los modelos (sin BD local disponible no se pudo
autogenerar) y revisada con `alembic upgrade/downgrade --sql`: Alembic tampoco habría
emitido los índices únicos parciales, los CHECK ni el movimiento de datos.

Crea:
- `calendario`: general (id_colegio NULL) o particular por colegio y año, con `activo` y
  `version`. Índices únicos parciales: un general por año y un particular activo por
  colegio y año. CHECK: el general siempre está activo.
- `calendario_periodo`: PA1, PR1, ..., PA4, PR4 de cada calendario (fechas inclusivas).
- `dia_no_lectivo`: rangos de días no lectivos de un calendario, con motivo y origen.
- UNIQUE (id_anio_escolar, numero) en `periodo_academico`.

Transforma (los datos de qa y uat son ficticios):
- Cada año escolar con sus PA 1 a 4 recibe un calendario general con las fechas que
  tenían en `periodo_academico`. Cada PR n va del día siguiente al fin de PA n al día
  anterior al inicio de PA n+1; el PR4, hasta `año_escolar.fecha_fin`. Si un hueco queda
  vacío o negativo, el PR dura un día y se corre el inicio del PA siguiente (y su fin si
  hiciera falta); si el PA4 termina el último día del año, el PR4 dura un día y cae
  fuera de él. Un año que no tiene exactamente los PA 1 a 4 queda sin calendario (se
  configura después desde la API o con `cargar-calendario`).
- Cada `dia_no_laborable` que cae dentro de un PA de esos calendarios pasa a
  `dia_no_lectivo` (origen 'automatico', inicio = fin = fecha).

Borra:
- `dia_no_laborable`, con los días que no caían dentro de un PA (fines de semana,
  recesos o fuera de todo periodo): ya no tienen dónde vivir.
- `periodo_academico.fecha_inicio` y `fecha_fin`.

Downgrade (mínimo): devuelve a `periodo_academico` las fechas de los PA del calendario
general (o las del año si no tiene), recrea `dia_no_laborable` con un día por cada fecha
de los días no lectivos de los generales y elimina las tablas nuevas. Los calendarios
particulares se pierden.
"""
from datetime import date, timedelta
from typing import Optional, Sequence

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '543e52b37c0f'
down_revision: str | Sequence[str] | None = '0f7bffeb9082'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UN_DIA = timedelta(days=1)


def _auditoria() -> list[sa.Column]:
    return [
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=False),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
        sa.Column('modificado_por', sa.Integer(), sa.ForeignKey('usuario.id_usuario'), nullable=True),
        sa.Column('modificado_en', sa.DateTime(timezone=True), nullable=True),
    ]


# Tablas ligeras para el movimiento de datos (no dependen de los modelos de la app).
_calendario = sa.table(
    'calendario',
    sa.column('id_calendario', sa.Integer), sa.column('id_anio_escolar', sa.Integer),
    sa.column('id_colegio', sa.Integer), sa.column('activo', sa.Boolean), sa.column('version', sa.Integer),
    sa.column('creado_por', sa.Integer), sa.column('creado_en', sa.DateTime(timezone=True)),
)
_calendario_periodo = sa.table(
    'calendario_periodo',
    sa.column('id_calendario', sa.Integer), sa.column('tipo', sa.Text), sa.column('numero', sa.SmallInteger),
    sa.column('fecha_inicio', sa.Date), sa.column('fecha_fin', sa.Date),
)
_dia_no_lectivo = sa.table(
    'dia_no_lectivo',
    sa.column('id_calendario', sa.Integer), sa.column('motivo', sa.Text), sa.column('fecha_inicio', sa.Date),
    sa.column('fecha_fin', sa.Date), sa.column('origen', sa.Text), sa.column('creado_por', sa.Integer),
    sa.column('creado_en', sa.DateTime(timezone=True)), sa.column('modificado_por', sa.Integer),
    sa.column('modificado_en', sa.DateTime(timezone=True)),
)


def _crear_tablas() -> None:
    op.create_table(
        'calendario',
        sa.Column('id_calendario', sa.Integer(), nullable=False),
        sa.Column('id_anio_escolar', sa.Integer(), sa.ForeignKey('año_escolar.id_anio_escolar'), nullable=False),
        sa.Column('id_colegio', sa.Integer(), sa.ForeignKey('colegio.id_colegio'), nullable=True),
        sa.Column('activo', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id_calendario'),
        sa.CheckConstraint('id_colegio IS NOT NULL OR activo', name='ck_calendario_general_activo'),
    )
    op.create_index(
        'uq_calendario_general_por_anio', 'calendario', ['id_anio_escolar'], unique=True,
        postgresql_where=sa.text('id_colegio IS NULL'),
    )
    op.create_index(
        'uq_calendario_particular_activo', 'calendario', ['id_anio_escolar', 'id_colegio'], unique=True,
        postgresql_where=sa.text('id_colegio IS NOT NULL AND activo'),
    )
    op.create_table(
        'calendario_periodo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_calendario', sa.Integer(), sa.ForeignKey('calendario.id_calendario'), nullable=False),
        sa.Column('tipo', sa.Text(), nullable=False),
        sa.Column('numero', sa.SmallInteger(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_calendario', 'tipo', 'numero', name='uq_calendario_periodo_tipo_numero'),
        sa.CheckConstraint("tipo IN ('PA', 'PR')", name='ck_calendario_periodo_tipo'),
        sa.CheckConstraint('numero BETWEEN 1 AND 4', name='ck_calendario_periodo_numero'),
        sa.CheckConstraint('fecha_fin >= fecha_inicio', name='ck_calendario_periodo_fechas'),
    )
    op.create_table(
        'dia_no_lectivo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('id_calendario', sa.Integer(), sa.ForeignKey('calendario.id_calendario'), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=False),
        sa.Column('origen', sa.Text(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("origen IN ('automatico', 'manual')", name='ck_dia_no_lectivo_origen'),
        sa.CheckConstraint('fecha_fin >= fecha_inicio', name='ck_dia_no_lectivo_fechas'),
        sa.CheckConstraint('length(trim(motivo)) > 0', name='ck_dia_no_lectivo_motivo'),
    )
    op.create_index('ix_dia_no_lectivo_calendario_inicio', 'dia_no_lectivo', ['id_calendario', 'fecha_inicio'])


def _periodos_derivados(pas: list[tuple[date, date]], fin_del_anio: date) -> list[tuple[str, int, date, date]]:
    """Los 8 periodos (tipo, numero, inicio, fin) a partir de los 4 PA en orden. Misma
    regla que `calendario_gestion.derivar_recesos` (copiada: una migración no importa
    código de la app, que puede cambiar después)."""
    periodos = []
    inicio_minimo: Optional[date] = None
    for numero, (inicio, fin) in enumerate(pas, start=1):
        if inicio_minimo is not None and inicio < inicio_minimo:
            inicio, fin = inicio_minimo, max(fin, inicio_minimo)
        siguiente = pas[numero][0] if numero < len(pas) else fin_del_anio + UN_DIA
        fin_receso = max(siguiente - UN_DIA, fin + UN_DIA)
        periodos.append(('PA', numero, inicio, fin))
        periodos.append(('PR', numero, fin + UN_DIA, fin_receso))
        inicio_minimo = fin_receso + UN_DIA
    return periodos


def _crear_calendarios_generales(conexion) -> dict[int, list[tuple[date, date]]]:
    """Un calendario general por año con sus PA 1 a 4. Devuelve {id_calendario: PA}."""
    anios = conexion.execute(
        sa.text('SELECT id_anio_escolar, fecha_fin FROM "año_escolar"').columns(
            id_anio_escolar=sa.Integer, fecha_fin=sa.Date
        )
    ).all()
    pas_por_calendario = {}
    for id_anio, fin_del_anio in anios:
        filas = conexion.execute(
            sa.text(
                'SELECT numero, fecha_inicio, fecha_fin, creado_por FROM periodo_academico '
                'WHERE id_anio_escolar = :anio ORDER BY numero'
            ).columns(numero=sa.Integer, fecha_inicio=sa.Date, fecha_fin=sa.Date, creado_por=sa.Integer),
            {'anio': id_anio},
        ).all()
        if [f.numero for f in filas] != [1, 2, 3, 4]:
            continue
        periodos = _periodos_derivados([(f.fecha_inicio, f.fecha_fin) for f in filas], fin_del_anio)
        id_calendario = conexion.execute(
            _calendario.insert()
            .values(id_anio_escolar=id_anio, id_colegio=None, activo=True, version=1,
                    creado_por=filas[0].creado_por, creado_en=sa.func.now())
            .returning(_calendario.c.id_calendario)
        ).scalar_one()
        conexion.execute(
            _calendario_periodo.insert(),
            [{'id_calendario': id_calendario, 'tipo': tipo, 'numero': numero, 'fecha_inicio': inicio,
              'fecha_fin': fin} for tipo, numero, inicio, fin in periodos],
        )
        pas_por_calendario[id_calendario] = [(inicio, fin) for tipo, _, inicio, fin in periodos if tipo == 'PA']
    return pas_por_calendario


def _copiar_dias_no_laborables(conexion, pas_por_calendario: dict[int, list[tuple[date, date]]]) -> None:
    """dia_no_laborable -> dia_no_lectivo del general cuyo PA contiene la fecha."""
    consulta = sa.text(
        'SELECT fecha, motivo, creado_por, creado_en, modificado_por, modificado_en FROM dia_no_laborable ORDER BY fecha'
    ).columns(
        fecha=sa.Date, motivo=sa.Text, creado_por=sa.Integer, creado_en=sa.DateTime(timezone=True),
        modificado_por=sa.Integer, modificado_en=sa.DateTime(timezone=True),
    )
    for dia in conexion.execute(consulta).mappings():
        destino = next(
            (cal for cal, pas in pas_por_calendario.items() if any(i <= dia['fecha'] <= f for i, f in pas)),
            None,
        )
        if destino is None:
            continue
        conexion.execute(_dia_no_lectivo.insert().values(
            id_calendario=destino, motivo=dia['motivo'], fecha_inicio=dia['fecha'], fecha_fin=dia['fecha'],
            origen='automatico', creado_por=dia['creado_por'], creado_en=dia['creado_en'],
            modificado_por=dia['modificado_por'], modificado_en=dia['modificado_en'],
        ))


def upgrade() -> None:
    """Upgrade schema."""
    _crear_tablas()
    if context.is_offline_mode():
        # `--sql` no puede leer los datos: el movimiento solo corre con conexión.
        op.execute("-- calendarios generales y dia_no_laborable -> dia_no_lectivo: solo en modo online")
    else:
        conexion = op.get_bind()
        _copiar_dias_no_laborables(conexion, _crear_calendarios_generales(conexion))
    op.create_unique_constraint(
        'uq_periodo_academico_anio_numero', 'periodo_academico', ['id_anio_escolar', 'numero']
    )
    op.drop_table('dia_no_laborable')
    op.drop_column('periodo_academico', 'fecha_fin')
    op.drop_column('periodo_academico', 'fecha_inicio')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('periodo_academico', sa.Column('fecha_inicio', sa.Date(), nullable=True))
    op.add_column('periodo_academico', sa.Column('fecha_fin', sa.Date(), nullable=True))
    op.execute(
        "UPDATE periodo_academico p SET fecha_inicio = cp.fecha_inicio, fecha_fin = cp.fecha_fin "
        "FROM calendario c JOIN calendario_periodo cp ON cp.id_calendario = c.id_calendario "
        "WHERE c.id_colegio IS NULL AND c.id_anio_escolar = p.id_anio_escolar "
        "AND cp.tipo = 'PA' AND cp.numero = p.numero"
    )
    op.execute(
        'UPDATE periodo_academico p SET fecha_inicio = a.fecha_inicio, fecha_fin = a.fecha_fin '
        'FROM "año_escolar" a WHERE a.id_anio_escolar = p.id_anio_escolar AND p.fecha_inicio IS NULL'
    )
    op.alter_column('periodo_academico', 'fecha_inicio', nullable=False)
    op.alter_column('periodo_academico', 'fecha_fin', nullable=False)
    op.create_table(
        'dia_no_laborable',
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        *_auditoria(),
        sa.PrimaryKeyConstraint('fecha'),
    )
    op.execute(
        "INSERT INTO dia_no_laborable (fecha, motivo, creado_por, creado_en, modificado_por, modificado_en) "
        "SELECT DISTINCT ON (dia) dia, d.motivo, d.creado_por, d.creado_en, d.modificado_por, d.modificado_en "
        "FROM dia_no_lectivo d JOIN calendario c ON c.id_calendario = d.id_calendario "
        "CROSS JOIN LATERAL generate_series(d.fecha_inicio, d.fecha_fin, interval '1 day') AS serie(momento) "
        "CROSS JOIN LATERAL (SELECT serie.momento::date AS dia) AS dias "
        "WHERE c.id_colegio IS NULL ORDER BY dia, d.id"
    )
    op.drop_constraint('uq_periodo_academico_anio_numero', 'periodo_academico', type_='unique')
    op.drop_index('ix_dia_no_lectivo_calendario_inicio', table_name='dia_no_lectivo')
    op.drop_table('dia_no_lectivo')
    op.drop_table('calendario_periodo')
    op.drop_index('uq_calendario_particular_activo', table_name='calendario')
    op.drop_index('uq_calendario_general_por_anio', table_name='calendario')
    op.drop_table('calendario')
