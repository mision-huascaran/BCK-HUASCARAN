"""lectura_semanal_por_semana: la tabla de lectura se identifica por semana, sin mes (CU023 a CU026)

Revision ID: c3f8d2e61a57
Revises: b7e2a91c4d30
Create Date: 2026-10-24 20:00:00.000000

Escrita a mano:

1. Duplicados por (colegio, grado, sección, subprograma, semana) en los datos ficticios de
   qa y uat (una semana partida entre dos meses podía tener una tabla por mes): se
   conserva una, la que tenga datos de lectura (LSN o LSL); si empatan, la de
   `fecha_registro` más reciente y luego la de mayor `id`. Las demás se borran con sus
   filas (`lectura_semanal_alumno`, única FK hacia la tabla) y la auditoría de ambas.
2. `mes` e `id_anio_escolar` pasan a ser solo la ubicación: se recalculan con la fecha de
   la clase de referencia y el año de la semana.
3. `uq_lectura_semanal_grupo_semana` pasa de (grupo, subprograma, año, mes, semana) a
   (grupo, subprograma, semana).

Downgrade: restaura el UNIQUE anterior (más amplio: siempre se cumple). Lo borrado y el
mes recalculado no se revierten.
"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3f8d2e61a57'
down_revision: str | Sequence[str] | None = 'b7e2a91c4d30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'lectura_semanal'
UQ = 'uq_lectura_semanal_grupo_semana'
COLUMNAS_VIEJAS = ['id_colegio', 'id_grado', 'id_seccion', 'id_programa', 'id_anio_escolar', 'mes', 'id_semana']
COLUMNAS_NUEVAS = ['id_colegio', 'id_grado', 'id_seccion', 'id_programa', 'id_semana']

CON_DATOS = (
    "EXISTS (SELECT 1 FROM lectura_semanal_alumno f WHERE f.id_lectura_semanal = t.id AND "
    "(f.lsn_titulo IS NOT NULL OR f.lsn_nota IS NOT NULL OR f.lsn_maximo IS NOT NULL OR f.cantidad_lsl IS NOT NULL))"
)
SOBRANTES = (
    "SELECT id FROM (SELECT t.id, row_number() OVER ("
    "PARTITION BY t.id_colegio, t.id_grado, t.id_seccion, t.id_programa, t.id_semana "
    f"ORDER BY {CON_DATOS} DESC, t.fecha_registro DESC, t.id DESC) AS orden FROM lectura_semanal t) AS r "
    "WHERE orden > 1"
)


def _eliminar_duplicadas() -> None:
    # Las sobrantes se calculan una sola vez: al borrar las filas, CON_DATOS cambiaría.
    op.execute(f"CREATE TEMPORARY TABLE lectura_sobrante AS {SOBRANTES}")
    op.execute(
        "DELETE FROM auditoria WHERE tabla = 'lectura_semanal_alumno' AND id_registro IN ("
        "SELECT CAST(id AS TEXT) FROM lectura_semanal_alumno "
        "WHERE id_lectura_semanal IN (SELECT id FROM lectura_sobrante))"
    )
    op.execute(
        "DELETE FROM auditoria WHERE tabla = 'lectura_semanal' "
        "AND id_registro IN (SELECT CAST(id AS TEXT) FROM lectura_sobrante)"
    )
    op.execute("DELETE FROM lectura_semanal_alumno WHERE id_lectura_semanal IN (SELECT id FROM lectura_sobrante)")
    op.execute("DELETE FROM lectura_semanal WHERE id IN (SELECT id FROM lectura_sobrante)")
    op.execute("DROP TABLE lectura_sobrante")


def _recalcular_ubicacion() -> None:
    op.execute(
        "UPDATE lectura_semanal t SET mes = CAST(EXTRACT(MONTH FROM c.fecha) AS INTEGER), "
        "id_anio_escolar = s.id_anio_escolar "
        "FROM clase c, semana_reporte s "
        "WHERE c.id_clase = t.id_clase_referencia AND s.id_semana = t.id_semana "
        "AND (t.mes <> CAST(EXTRACT(MONTH FROM c.fecha) AS INTEGER) OR t.id_anio_escolar <> s.id_anio_escolar)"
    )


def upgrade() -> None:
    """Upgrade schema."""
    _eliminar_duplicadas()
    _recalcular_ubicacion()
    op.drop_constraint(UQ, TABLA, type_='unique')
    op.create_unique_constraint(UQ, TABLA, COLUMNAS_NUEVAS)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(UQ, TABLA, type_='unique')
    op.create_unique_constraint(UQ, TABLA, COLUMNAS_VIEJAS)
