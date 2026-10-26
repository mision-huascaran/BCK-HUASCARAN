"""reglas_rubrica_confirmadas: una rúbrica por alumno y mes, y el PA del mes con R1 confirmada

Revision ID: d06ce68c5f14
Revises: a4e56dc37694
Create Date: 2026-10-24 10:00:00.000000

Escrita a mano (CU029, CU030, reglas confirmadas por el analista):

- R2: una sola `rubrica_mensual` por (alumno, año escolar, mes), en cualquier grupo. Antes
  de cambiar el UNIQUE se eliminan los duplicados (datos ficticios de qa y uat): se
  conserva la de `fecha_registro` más reciente y, en empate, la de mayor `id`. Los
  `registro_vuelo` que tomaban de referencia una eliminada quedan sin referencia
  (`id_rubrica_referencia` y `rubrica_version` en NULL) y se borran sus filas de
  `auditoria`.
- Cambia `uq_rubrica_mensual_alumno_mes_grupo` por `uq_rubrica_mensual_alumno_mes`.
- R1: recalcula `id_periodo_academico` de las rúbricas existentes: el PA con más días
  lectivos del mes en el calendario de su colegio (el particular activo o, si no tiene, el
  general del año) y, en empate, el del último día lectivo. Los días no lectivos y los
  recesos no cuentan. Se calcula aquí, sin importar servicios de la app. Un mes sin días
  lectivos de un PA conserva el que tenía.

Downgrade: restaura el UNIQUE anterior (siempre se cumple con el nuevo). Los duplicados
eliminados y el PA recalculado no se revierten.
"""
from calendar import monthrange
from collections import Counter
from datetime import date, timedelta
from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd06ce68c5f14'
down_revision: str | Sequence[str] | None = 'a4e56dc37694'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUBRICA = 'rubrica_mensual'
UQ_VIEJO = 'uq_rubrica_mensual_alumno_mes_grupo'
UQ_NUEVO = 'uq_rubrica_mensual_alumno_mes'
COLUMNAS_VIEJAS = ['id_alumno', 'id_anio_escolar', 'mes', 'id_colegio', 'id_grado', 'id_seccion', 'id_programa']
COLUMNAS_NUEVAS = ['id_alumno', 'id_anio_escolar', 'mes']
VIERNES = 4

DUPLICADAS = (
    "SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY id_alumno, id_anio_escolar, mes "
    "ORDER BY fecha_registro DESC, id DESC) AS orden FROM rubrica_mensual) AS r WHERE orden > 1"
)


def _eliminar_duplicadas() -> None:
    op.execute(
        f"UPDATE registro_vuelo SET id_rubrica_referencia = NULL, rubrica_version = NULL "
        f"WHERE id_rubrica_referencia IN ({DUPLICADAS})"
    )
    op.execute(
        f"DELETE FROM auditoria WHERE tabla = '{RUBRICA}' "
        f"AND id_registro IN (SELECT CAST(id AS TEXT) FROM ({DUPLICADAS}) AS d)"
    )
    op.execute(f"DELETE FROM rubrica_mensual WHERE id IN ({DUPLICADAS})")


def _rango_del_mes(inicio: date, fin: date, mes: int):
    """Primer y último día del mes del año escolar (el del año calendario que se superpone)."""
    for anio in range(inicio.year, fin.year + 1):
        primero, ultimo = date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])
        if primero <= fin and ultimo >= inicio:
            return primero, ultimo
    return None


def _calendario_de(conexion, id_anio: int, id_colegio: int):
    """Id del calendario del colegio en el año: el particular activo o el general."""
    return conexion.execute(
        sa.text(
            "SELECT id_calendario FROM calendario WHERE id_anio_escolar = :anio AND activo "
            "AND (id_colegio = :colegio OR id_colegio IS NULL) ORDER BY id_colegio IS NULL LIMIT 1"
        ),
        {"anio": id_anio, "colegio": id_colegio},
    ).scalar()


def _pa_del_mes(conexion, id_calendario: int, id_anio: int, desde: date, hasta: date):
    """R1: el id del PA con más días lectivos de [desde, hasta]; en empate, el del último."""
    pas = conexion.execute(
        sa.text(
            "SELECT cp.fecha_inicio, cp.fecha_fin, p.id_periodo_academico FROM calendario_periodo cp "
            "JOIN periodo_academico p ON p.id_anio_escolar = :anio AND p.numero = cp.numero "
            "WHERE cp.id_calendario = :calendario AND cp.tipo = 'PA'"
        ),
        {"anio": id_anio, "calendario": id_calendario},
    ).all()
    no_lectivos = conexion.execute(
        sa.text("SELECT fecha_inicio, fecha_fin FROM dia_no_lectivo WHERE id_calendario = :calendario"),
        {"calendario": id_calendario},
    ).all()
    conteo, ultimo = Counter(), {}
    dia = desde
    while dia <= hasta:
        pa = next((p for inicio, fin, p in pas if inicio <= dia <= fin), None)
        lectivo = dia.weekday() <= VIERNES and not any(i <= dia <= f for i, f in no_lectivos)
        if pa is not None and lectivo:
            conteo[pa] += 1
            ultimo[pa] = dia
        dia += timedelta(days=1)
    return max(conteo, key=lambda p: (conteo[p], ultimo[p])) if conteo else None


def _recalcular_periodos(conexion) -> None:
    meses = conexion.execute(
        sa.text(
            'SELECT DISTINCT r.id_colegio, r.id_anio_escolar, r.mes, a.fecha_inicio, a.fecha_fin '
            'FROM rubrica_mensual r JOIN "año_escolar" a ON a.id_anio_escolar = r.id_anio_escolar'
        )
    ).all()
    for id_colegio, id_anio, mes, inicio, fin in meses:
        rango = _rango_del_mes(inicio, fin, mes)
        id_calendario = _calendario_de(conexion, id_anio, id_colegio)
        if rango is None or id_calendario is None:
            continue
        pa = _pa_del_mes(conexion, id_calendario, id_anio, *rango)
        if pa is None:
            continue
        conexion.execute(
            sa.text(
                "UPDATE rubrica_mensual SET id_periodo_academico = :pa WHERE id_colegio = :colegio "
                "AND id_anio_escolar = :anio AND mes = :mes AND id_periodo_academico <> :pa"
            ),
            {"pa": pa, "colegio": id_colegio, "anio": id_anio, "mes": mes},
        )


def upgrade() -> None:
    """Upgrade schema."""
    _eliminar_duplicadas()
    op.drop_constraint(UQ_VIEJO, RUBRICA, type_='unique')
    op.create_unique_constraint(UQ_NUEVO, RUBRICA, COLUMNAS_NUEVAS)
    if context.is_offline_mode():
        # `--sql` no puede leer los calendarios: el recálculo solo corre con conexión.
        op.execute("-- recálculo de rubrica_mensual.id_periodo_academico (R1): solo en modo online")
    else:
        _recalcular_periodos(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(UQ_NUEVO, RUBRICA, type_='unique')
    op.create_unique_constraint(UQ_VIEJO, RUBRICA, COLUMNAS_VIEJAS)
