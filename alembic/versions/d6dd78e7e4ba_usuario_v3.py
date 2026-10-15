"""usuario v3: dni, supervisor original, intentos del codigo e id_docente unico

Revision ID: d6dd78e7e4ba
Revises: 2ecf474d0b22
Create Date: 2026-10-07 16:53:48.558036

Escrita a mano (no autogenerate). Diseño v3, corrección 26.

- `dni`: texto de 8 dígitos (puede empezar con 0), nullable en BD mientras existan
  cuentas sin DNI, único y con CHECK de formato.
- `es_supervisor_original`: el índice único parcial garantiza que solo una fila
  tenga true.
- `codigo_verificacion_intentos`: contador de intentos del PIN.
- UNIQUE sobre `id_docente`: un docente tiene a lo sumo una cuenta.
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6dd78e7e4ba'
down_revision: str | Sequence[str] | None = '2ecf474d0b22'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('usuario', sa.Column('dni', sa.String(length=8), nullable=True))
    op.create_unique_constraint('uq_usuario_dni', 'usuario', ['dni'])
    op.create_check_constraint('ck_usuario_dni_formato', 'usuario', "dni ~ '^[0-9]{8}$'")

    op.add_column(
        'usuario',
        sa.Column(
            'es_supervisor_original', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
    )
    op.create_index(
        'uq_usuario_supervisor_original',
        'usuario',
        ['es_supervisor_original'],
        unique=True,
        postgresql_where=sa.text('es_supervisor_original'),
    )

    op.add_column(
        'usuario',
        sa.Column(
            'codigo_verificacion_intentos', sa.Integer(), server_default=sa.text('0'), nullable=False
        ),
    )

    op.create_unique_constraint('uq_usuario_id_docente', 'usuario', ['id_docente'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_usuario_id_docente', 'usuario', type_='unique')
    op.drop_column('usuario', 'codigo_verificacion_intentos')
    op.drop_index('uq_usuario_supervisor_original', table_name='usuario')
    op.drop_column('usuario', 'es_supervisor_original')
    op.drop_constraint('ck_usuario_dni_formato', 'usuario', type_='check')
    op.drop_constraint('uq_usuario_dni', 'usuario', type_='unique')
    op.drop_column('usuario', 'dni')
