"""anio_escolar_desde_calendario: nombre único y fechas del año desde su calendario general (CU027)

Revision ID: e4a9b7c2d815
Revises: c3f8d2e61a57
Create Date: 2026-10-24 23:00:00.000000

Escrita a mano:

1. Si hay años escolares con el mismo `nombre`, falla con un mensaje claro: no se borran
   años (tienen datos colgando); hay que resolverlos a mano.
2. Crea `uq_anio_escolar_nombre`.
3. Decisión del equipo: las fechas del año son las de su calendario general, inicio del
   PA1 a fin del PR4. Se sincronizan las de cada año que tiene general. Las semanas
   (`semana_reporte`) no se regeneran aquí: lo hace `inicializar` (cargar-calendario) al
   arrancar.

Downgrade: quita el UNIQUE (las fechas sincronizadas no se revierten).
"""
from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a9b7c2d815'
down_revision: str | Sequence[str] | None = 'c3f8d2e61a57'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = 'año_escolar'
UQ = 'uq_anio_escolar_nombre'


def _exigir_nombres_unicos() -> None:
    if context.is_offline_mode():
        op.execute(f'-- verificar a mano que "{TABLA}".nombre no tenga duplicados')
        return
    repetidos = op.get_bind().execute(sa.text(
        f'SELECT nombre, count(*) FROM "{TABLA}" GROUP BY nombre HAVING count(*) > 1 ORDER BY nombre'
    )).all()
    if repetidos:
        detalle = ", ".join(f"'{nombre}' ({veces} veces)" for nombre, veces in repetidos)
        raise RuntimeError(
            f"No se puede crear {UQ}: hay años escolares con el mismo nombre: {detalle}. "
            "Resuélvelos a mano (no se borran años automáticamente) y vuelve a migrar."
        )


def _sincronizar_fechas() -> None:
    op.execute(
        f'UPDATE "{TABLA}" a SET fecha_inicio = pa1.fecha_inicio, fecha_fin = pr4.fecha_fin '
        "FROM calendario c "
        "JOIN calendario_periodo pa1 ON pa1.id_calendario = c.id_calendario AND pa1.tipo = 'PA' AND pa1.numero = 1 "
        "JOIN calendario_periodo pr4 ON pr4.id_calendario = c.id_calendario AND pr4.tipo = 'PR' AND pr4.numero = 4 "
        "WHERE c.id_colegio IS NULL AND c.id_anio_escolar = a.id_anio_escolar "
        "AND (a.fecha_inicio <> pa1.fecha_inicio OR a.fecha_fin <> pr4.fecha_fin)"
    )


def upgrade() -> None:
    """Upgrade schema."""
    _exigir_nombres_unicos()
    op.create_unique_constraint(UQ, TABLA, ['nombre'])
    _sincronizar_fechas()


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(UQ, TABLA, type_='unique')
