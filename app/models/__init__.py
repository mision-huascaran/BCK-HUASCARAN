"""Modelos SQLModel, separados por grupo del diseño de BD v3.

Se importan todos aquí para que cualquier `import app.models...` registre las 32 tablas
en `SQLModel.metadata` (lo usan Alembic y el `create_all` de los tests).
"""
from app.models import evaluacion, organizacion, seguridad, trazabilidad  # noqa: F401
