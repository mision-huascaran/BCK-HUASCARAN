"""Convención única de fechas y horas del backend.

Hay dos clases de valores temporales y cada una tiene su tipo, su columna y su fuente:

- INSTANTE (cuándo pasó o vence algo; p. ej. el vencimiento de un código de
  verificación o el `exp` de un JWT): columna `timestamptz` (`UTCDateTime` en el
  modelo), valor `datetime` con zona en UTC, obtenido con `ahora_utc()`. Se convierte a
  hora local solo al mostrarlo.
- DÍA DE CALENDARIO (qué día es para el usuario; p. ej. `creado_en`, `modificado_en`,
  la vigencia de un periodo académico): columna `date`, obtenido con `hoy_lima()`. El
  servidor corre en UTC, así que `date.today()` daría el día siguiente entre las 19:00 y
  las 24:00 de Lima.

La zona se obtiene por nombre con zoneinfo ("America/Lima"). Prohibido restar horas a
mano (-5): si Perú vuelve a cambiar de horario, la base de zonas lo resuelve.

Fuera de este módulo no se usa `datetime.now()`, `datetime.utcnow()` ni `date.today()`.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

ZONA_LIMA = ZoneInfo("America/Lima")


def ahora_utc() -> datetime:
    """Instante actual, con zona, en UTC."""
    return datetime.now(timezone.utc)


def hoy_lima() -> date:
    """Día de calendario actual en Lima, sin importar la zona del servidor."""
    return datetime.now(ZONA_LIMA).date()


def _a_utc(valor: datetime) -> datetime:
    # Sin zona se asume UTC: es lo que guarda SQLite (los tests) y lo que había en la
    # columna `timestamp` antes de pasar a `timestamptz`.
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


class UTCDateTime(TypeDecorator):
    """`timestamptz` que siempre entrega y recibe `datetime` con zona en UTC.

    PostgreSQL devuelve el valor con zona; SQLite lo devuelve sin zona. Con este tipo
    los dos se comportan igual y se pueden comparar contra `ahora_utc()` sin TypeError.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _a_utc(value)

    def process_result_value(self, value, dialect):
        # Al leer se aplica la misma normalización que al escribir.
        return self.process_bind_param(value, dialect)
