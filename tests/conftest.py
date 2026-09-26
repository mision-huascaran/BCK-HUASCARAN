"""
Fixtures de las pruebas de la API.

Las pruebas no necesitan PostgreSQL: cada una corre sobre una base SQLite en
memoria, recién creada, que reemplaza a get_db. Así se ejecutan igual en local
y en el stage de pruebas de Jenkins, donde no hay base de datos.
"""
import os
import smtplib
from datetime import date, timedelta

# Settings exige estas variables al importarse: deben existir antes de importar app.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "clave-solo-para-pruebas")

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core import security
from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models.organizacion import (
    Alumno,
    AnioEscolar,
    CicloEbr,
    Colegio,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Rol,
    Usuario,
)

PASSWORD = "ClaveValida123"

# Correos de las cuentas que arma la fixture `entorno`, una por rol.
CORREOS = {
    "Docente": "docente@sicedu.test",
    "Supervisor": "supervisor@sicedu.test",
    "Directivo": "directivo@sicedu.test",
}

# bcrypt con el coste de produccion tarda del orden de medio segundo por hash, y estas
# pruebas crean varias cuentas cada una. Con el coste minimo el algoritmo es el mismo y
# la suite baja de minutos a segundos; el codigo bajo prueba no cambia.
security.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)


def crear_motor_de_pruebas():
    """Motor SQLite en memoria con las funciones que la app espera de PostgreSQL."""
    motor = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # listar_alumnos busca sin tildes con translate(), que en PostgreSQL es nativa y en
    # SQLite no existe. Se registra con la misma semantica para poder probar la busqueda
    # de verdad, en vez de saltarsela por ser otra base.
    @event.listens_for(motor, "connect")
    def registrar_translate(conexion, _registro):  # pragma: no cover - lo llama SQLAlchemy
        conexion.create_function(
            "translate",
            3,
            lambda texto, desde, hasta: (
                None if texto is None else texto.translate(str.maketrans(desde, hasta))
            ),
        )

    SQLModel.metadata.create_all(motor)
    return motor


@pytest.fixture
def session():
    # La base vive en memoria y muere con el motor al terminar la prueba: no hace
    # falta borrar tablas, y cada prueba arranca con la base vacia.
    motor = crear_motor_de_pruebas()
    with Session(motor) as sesion:
        yield sesion


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

    # El modelo v2 exige creado_por y creado_en en toda fila auditable. El primer
    # usuario se auto-referencia, igual que en app/seed_data.py.
    hoy = date.today()
    activo = Usuario(
        id_rol=rol.id_rol,
        correo="activo@prueba.test",
        password_hash=hash_password(PASSWORD),
        nombres="Ana",
        apellidos="Prueba",
        activo=True,
        creado_por=1,
        creado_en=hoy,
    )
    session.add(activo)
    session.commit()
    session.refresh(activo)
    activo.creado_por = activo.id_usuario
    session.add(activo)
    session.commit()

    inactivo = Usuario(
        id_rol=rol.id_rol,
        correo="inactivo@prueba.test",
        password_hash=hash_password(PASSWORD),
        nombres="Beto",
        apellidos="Prueba",
        activo=False,
        creado_por=activo.id_usuario,
        creado_en=hoy,
    )
    session.add(inactivo)
    session.commit()
    session.refresh(inactivo)
    return {"activo": activo, "inactivo": inactivo}


@pytest.fixture
def token(client, usuarios):
    respuesta = client.post("/login", json={"correo": "activo@prueba.test", "password": PASSWORD})
    return respuesta.json()["access_token"]


# ── Entorno completo: catálogo, cuentas de los tres roles y una asignación ───────────

@pytest.fixture
def entorno(session):
    """Base mínima con la misma forma que deja app/seed_data.py.

    Incluye dos colegios y dos grados a proposito: uno dentro del alcance del docente y
    otro fuera, que es lo que distingue los casos de permiso de los de error.
    """
    hoy = date.today()

    roles = {nombre: Rol(nombre=nombre) for nombre in ("Docente", "Supervisor", "Directivo")}
    ciclo = CicloEbr(nombre="III")
    for fila in [*roles.values(), ciclo]:
        session.add(fila)
    session.commit()

    grados = [
        Grado(nombre="1° primaria", id_ciclo=ciclo.id_ciclo),
        Grado(nombre="2° primaria", id_ciclo=ciclo.id_ciclo),
    ]
    programas = [Programa(nombre="Lectura"), Programa(nombre="Matemática")]
    for fila in [*grados, *programas]:
        session.add(fila)
    session.commit()

    # Supervisor primero: es el unico que puede auto-referenciarse en creado_por, y a
    # partir de el cuelga la auditoria del resto de las filas.
    supervisor = Usuario(
        id_rol=roles["Supervisor"].id_rol,
        correo=CORREOS["Supervisor"],
        password_hash=hash_password(PASSWORD),
        nombres="Sara",
        apellidos="Supervisora",
        activo=True,
        creado_por=1,
        creado_en=hoy,
    )
    session.add(supervisor)
    session.commit()
    session.refresh(supervisor)
    supervisor.creado_por = supervisor.id_usuario
    session.add(supervisor)
    session.commit()

    auditoria = {"creado_por": supervisor.id_usuario, "creado_en": hoy}

    colegios = [
        Colegio(nombre="Colegio Andino", zona="Rural", **auditoria),
        Colegio(nombre="Colegio Lejano", zona="Urbana", **auditoria),
    ]
    anio = AnioEscolar(
        nombre=str(hoy.year),
        fecha_inicio=hoy - timedelta(days=200),
        fecha_fin=hoy + timedelta(days=100),
        **auditoria,
    )
    ficha_docente = Docente(nombres="Rosa", apellidos="Quispe", activo=True, **auditoria)
    for fila in [*colegios, anio, ficha_docente]:
        session.add(fila)
    session.commit()

    periodos = [
        PeriodoAcademico(
            id_anio_escolar=anio.id_anio_escolar,
            numero=1,
            fecha_inicio=hoy - timedelta(days=30),
            fecha_fin=hoy + timedelta(days=30),
            **auditoria,
        ),
        # Cerrado: sirve para comprobar que una asignacion vencida no da alcance.
        PeriodoAcademico(
            id_anio_escolar=anio.id_anio_escolar,
            numero=0,
            fecha_inicio=hoy - timedelta(days=400),
            fecha_fin=hoy - timedelta(days=370),
            **auditoria,
        ),
    ]
    docente = Usuario(
        id_rol=roles["Docente"].id_rol,
        correo=CORREOS["Docente"],
        password_hash=hash_password(PASSWORD),
        id_docente=ficha_docente.id_docente,
        nombres="Rosa",
        apellidos="Quispe",
        activo=True,
        **auditoria,
    )
    directivo = Usuario(
        id_rol=roles["Directivo"].id_rol,
        correo=CORREOS["Directivo"],
        password_hash=hash_password(PASSWORD),
        nombres="Diego",
        apellidos="Directivo",
        activo=True,
        **auditoria,
    )
    for fila in [*periodos, docente, directivo]:
        session.add(fila)
    session.commit()

    asignacion = DocenteColegioGrado(
        id_docente=ficha_docente.id_docente,
        id_colegio=colegios[0].id_colegio,
        id_grado=grados[0].id_grado,
        id_periodo_academico=periodos[0].id_periodo_academico,
        **auditoria,
    )
    session.add(asignacion)
    session.commit()

    alumnos = [
        # Con tilde a proposito: la busqueda debe encontrarlo escribiendo "perez".
        Alumno(
            nombres="Luis",
            apellidos="Pérez Quispe",
            id_colegio=colegios[0].id_colegio,
            id_grado=grados[0].id_grado,
            id_programa_actual=programas[0].id_programa,
            fecha_registro=hoy,
            activo=True,
            **auditoria,
        ),
        # Fuera del alcance del docente: otro colegio y otro grado.
        Alumno(
            nombres="Mario",
            apellidos="Lejano",
            id_colegio=colegios[1].id_colegio,
            id_grado=grados[1].id_grado,
            id_programa_actual=programas[1].id_programa,
            fecha_registro=hoy,
            activo=False,
            **auditoria,
        ),
    ]
    for fila in alumnos:
        session.add(fila)
    session.commit()
    for fila in [*colegios, *grados, *programas, *periodos, *alumnos, ficha_docente, docente, directivo]:
        session.refresh(fila)

    return {
        "roles": roles,
        "grados": grados,
        "programas": programas,
        "colegios": colegios,
        "periodos": periodos,
        "ficha_docente": ficha_docente,
        "asignacion": asignacion,
        "alumnos": alumnos,
        "usuarios": {"Docente": docente, "Supervisor": supervisor, "Directivo": directivo},
    }


@pytest.fixture
def sesiones(client, entorno):
    """Cabecera Authorization ya lista para cada rol: sesiones["Supervisor"]."""
    cabeceras = {}
    for rol, correo in CORREOS.items():
        respuesta = client.post("/login", json={"correo": correo, "password": PASSWORD})
        assert respuesta.status_code == 200, respuesta.text
        cabeceras[rol] = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}
    return cabeceras


# ── Correo: ninguna prueba sale a la red ─────────────────────────────────────────────

class Buzon(list):
    """Correos enviados durante la prueba.

    `fallar = True` simula un SMTP caido, que es la rama que decide si la contraseña
    temporal se devuelve en la respuesta o se queda solo en el buzon del usuario.
    """

    fallar = False
    servidor = None
    credenciales = None


@pytest.fixture(autouse=True)
def correos(monkeypatch):
    """Sustituye smtplib.SMTP_SSL por un doble que guarda los mensajes.

    Es autouse a proposito: crear un profesor o pedir un codigo de verificacion intenta
    conectarse a smtp.gmail.com, y sin esto la suite se quedaria esperando el timeout.
    """
    buzon = Buzon()

    class SmtpDePrueba:
        def __init__(self, host, puerto):
            buzon.servidor = (host, puerto)
            if buzon.fallar:
                raise OSError("SMTP no disponible (simulado)")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def login(self, usuario, contraseña):
            buzon.credenciales = (usuario, contraseña)

        def send_message(self, mensaje):
            buzon.append(mensaje)

    monkeypatch.setattr(smtplib, "SMTP_SSL", SmtpDePrueba)
    return buzon


def texto_del_correo(mensaje) -> str:
    """Parte en texto plano de un correo del buzon, ya decodificada.

    Los mensajes llevan tildes, asi que viajan en base64: hay que decodificarlos para
    poder buscar texto dentro.
    """
    return "\n".join(
        parte.get_payload(decode=True).decode("utf-8")
        for parte in mensaje.walk()
        if parte.get_content_type() == "text/plain"
    )
