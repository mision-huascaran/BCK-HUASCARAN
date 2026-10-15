"""un docente por colegio, grado y periodo; correo normalizado

Revision ID: 5a73b20b7cb4
Revises: e127e57a7a50
Create Date: 2026-10-08 16:00:00.000000

Escrita a mano (no autogenerate). CU016.

1. UNIQUE `uq_docente_colegio_grado_periodo` sobre
   `docente_colegio_grado (id_colegio, id_grado, id_periodo_academico)`: un solo docente
   por colegio, grado y periodo. Si ya hay duplicados, la migración aborta y los lista;
   no borra nada, porque decidir cuál de las filas es la correcta es del Supervisor.
2. Correos normalizados (minúsculas, sin espacios alrededor). Si al normalizar dos
   cuentas quedarían con el mismo correo, aborta y las lista. Si no, normaliza y agrega
   el CHECK `ck_usuario_correo_normalizado` (solo PostgreSQL, como el CHECK del DNI).

Downgrade: quita el UNIQUE y el CHECK. Los correos quedan normalizados: el valor original
con mayúsculas no se guardó en ningún lado y, para el login, `Rosa@MH.org` y
`rosa@mh.org` ya eran la misma cuenta, así que no hay nada que revertir.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a73b20b7cb4'
down_revision: str | Sequence[str] | None = 'e127e57a7a50'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DUPLICADOS_ASIGNACION = sa.text(
    """
    SELECT id_colegio, id_grado, id_periodo_academico,
           string_agg(CAST(id AS TEXT), ', ') AS ids
    FROM docente_colegio_grado
    GROUP BY id_colegio, id_grado, id_periodo_academico
    HAVING count(*) > 1
    ORDER BY id_colegio, id_grado, id_periodo_academico
    """
)

CHOQUES_CORREO = sa.text(
    """
    SELECT lower(trim(correo)) AS normalizado,
           string_agg(CAST(id_usuario AS TEXT) || ':' || correo, ', ') AS cuentas
    FROM usuario
    GROUP BY lower(trim(correo))
    HAVING count(*) > 1
    ORDER BY 1
    """
)


class MigracionAbortada(RuntimeError):
    """Hay datos que impiden aplicar la migración; se listan para corregirlos a mano."""


def _abortar_si_hay(sentencia: sa.TextClause, titulo: str) -> None:
    filas = op.get_bind().execute(sentencia).fetchall()
    if filas:
        detalle = "\n".join(f"  - {tuple(fila)}" for fila in filas)
        raise MigracionAbortada(f"{titulo}. Corríjalos antes de migrar:\n{detalle}")


def upgrade() -> None:
    """Upgrade schema."""
    _abortar_si_hay(
        DUPLICADOS_ASIGNACION,
        "Hay asignaciones duplicadas por (id_colegio, id_grado, id_periodo_academico, ids)",
    )
    with op.batch_alter_table('docente_colegio_grado') as batch:
        batch.create_unique_constraint(
            'uq_docente_colegio_grado_periodo',
            ['id_colegio', 'id_grado', 'id_periodo_academico'],
        )

    _abortar_si_hay(
        CHOQUES_CORREO,
        "Hay cuentas cuyo correo quedaría repetido al normalizarlo (correo, id:correo)",
    )
    op.execute(
        "UPDATE usuario SET correo = lower(trim(correo)) WHERE correo <> lower(trim(correo))"
    )
    if op.get_bind().dialect.name == 'postgresql':
        op.create_check_constraint(
            'ck_usuario_correo_normalizado', 'usuario', 'correo = lower(btrim(correo))'
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_constraint('ck_usuario_correo_normalizado', 'usuario', type_='check')
    with op.batch_alter_table('docente_colegio_grado') as batch:
        batch.drop_constraint('uq_docente_colegio_grado_periodo', type_='unique')
