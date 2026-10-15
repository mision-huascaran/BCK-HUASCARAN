"""PIN de recuperación guardado como HMAC-SHA256

Revision ID: e127e57a7a50
Revises: b7f46024bef7
Create Date: 2026-10-08 10:00:00.000000

Escrita a mano (no autogenerate). CU005, CU006.

`usuario.codigo_verificacion` deja de guardar el código en texto plano (6 caracteres) y
pasa a guardar el HMAC-SHA256 del PIN en hexadecimal (64 caracteres, ver
app/core/pin.py).

Los códigos vigentes en texto plano ya no serían verificables contra un HMAC, así que el
upgrade los invalida (código y vencimiento en NULL, intentos en 0): quien tuviera uno
pendiente solo debe pedir otro. El downgrade también los deja en NULL antes de volver a
VARCHAR(6), porque un HMAC no entra en 6 caracteres y tampoco serviría con el código
anterior.

`batch_alter_table`: en PostgreSQL emite un ALTER COLUMN normal; en SQLite (que no
soporta cambiar el tipo de una columna) recrea la tabla.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e127e57a7a50'
down_revision: str | Sequence[str] | None = 'b7f46024bef7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVALIDAR_CODIGOS = (
    "UPDATE usuario SET codigo_verificacion = NULL, "
    "codigo_verificacion_expira = NULL, codigo_verificacion_intentos = 0"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(INVALIDAR_CODIGOS)
    with op.batch_alter_table('usuario') as batch:
        batch.alter_column(
            'codigo_verificacion',
            type_=sa.String(length=64),
            existing_type=sa.String(length=6),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(INVALIDAR_CODIGOS)
    with op.batch_alter_table('usuario') as batch:
        batch.alter_column(
            'codigo_verificacion',
            type_=sa.String(length=6),
            existing_type=sa.String(length=64),
            existing_nullable=True,
        )
