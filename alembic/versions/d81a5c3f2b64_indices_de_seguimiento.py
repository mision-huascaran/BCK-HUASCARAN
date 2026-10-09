"""índices para los listados de actividades y la última conexión

Revision ID: d81a5c3f2b64
Revises: c4f18e2a9d37
Create Date: 2026-10-08 22:00:00.000000

Escrita a mano (no autogenerate). CU017, CU020, CU021.

- `ix_actividad_docente_inicio (id_docente, inicio)`: las actividades de un docente se
  listan por `inicio` descendente y se filtran por fecha. Sin el índice, cada página
  recorre todas las actividades de todos los docentes (una o más por día hábil cada uno).
- `ix_sesion_usuario_inicio (id_usuario, inicio)`: la última conexión es el máximo de
  `sesion.inicio` por usuario; con el índice se resuelve sin recorrer todas las sesiones.

Downgrade: quita ambos índices.
"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd81a5c3f2b64'
down_revision: str | Sequence[str] | None = 'c4f18e2a9d37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_actividad_docente_inicio', 'actividad', ['id_docente', 'inicio'])
    op.create_index('ix_sesion_usuario_inicio', 'sesion', ['id_usuario', 'inicio'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_sesion_usuario_inicio', table_name='sesion')
    op.drop_index('ix_actividad_docente_inicio', table_name='actividad')
