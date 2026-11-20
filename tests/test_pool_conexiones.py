"""Pool de conexiones a PostgreSQL (documento de diseño: máximo inicial de 10 conexiones,
ajustable hasta 20). Los parámetros del pool solo se pasan con PostgreSQL: SQLite rechaza
`max_overflow`."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.database import opciones_de_pool

URL_POSTGRES = "postgresql+psycopg2://u:p@h/db"


def test_pool_de_postgresql_limitado_a_db_pool_max():
    esperado = {"pool_size": settings.DB_POOL_MAX, "max_overflow": 0, "pool_pre_ping": True}

    assert opciones_de_pool(URL_POSTGRES) == esperado


def test_pool_por_defecto_es_10():
    assert Settings.model_fields["DB_POOL_MAX"].default == 10


def test_sqlite_no_recibe_opciones_de_pool():
    assert opciones_de_pool("sqlite://") == {}


def test_pool_respeta_otro_db_pool_max(monkeypatch):
    monkeypatch.setattr(settings, "DB_POOL_MAX", 20)

    assert opciones_de_pool(URL_POSTGRES)["pool_size"] == 20


@pytest.mark.parametrize("valor", ["0", "-1"])
def test_db_pool_max_menor_que_1_se_rechaza(monkeypatch, valor):
    monkeypatch.setenv("DB_POOL_MAX", valor)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
