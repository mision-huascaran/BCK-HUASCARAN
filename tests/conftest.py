"""
Fixtures de las pruebas de la API.

Las pruebas no necesitan PostgreSQL: cada una corre sobre una base SQLite en
memoria, recién creada, que reemplaza a get_db. Así se ejecutan igual en local
y en el stage de pruebas de Jenkins, donde no hay base de datos.
"""
import os

# Settings exige estas variables al importarse: deben existir antes de importar app.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "clave-solo-para-pruebas")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models.organizacion import Rol, Usuario

PASSWORD = "ClaveValida123"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sesion:
        yield sesion
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def usuarios(session):
    rol = Rol(nombre="Profesor")
    session.add(rol)
    session.commit()
    session.refresh(rol)

    activo = Usuario(
        id_rol=rol.id_rol,
        correo="activo@prueba.test",
        password_hash=hash_password(PASSWORD),
        nombres="Ana",
        apellidos="Prueba",
        activo=True,
    )
    inactivo = Usuario(
        id_rol=rol.id_rol,
        correo="inactivo@prueba.test",
        password_hash=hash_password(PASSWORD),
        nombres="Beto",
        apellidos="Prueba",
        activo=False,
    )
    session.add_all([activo, inactivo])
    session.commit()
    session.refresh(activo)
    session.refresh(inactivo)
    return {"activo": activo, "inactivo": inactivo}


@pytest.fixture
def token(client, usuarios):
    respuesta = client.post("/login", json={"correo": "activo@prueba.test", "password": PASSWORD})
    return respuesta.json()["access_token"]
