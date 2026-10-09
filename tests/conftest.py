"""
Fixtures de las pruebas de la API.

Las pruebas no necesitan PostgreSQL: cada una corre sobre una base SQLite en memoria,
recién creada, que reemplaza a get_db. Así se ejecutan igual en local y en el stage de
pruebas de Jenkins, donde no hay base de datos.

Piezas principales (para agregar pruebas, partir de estas fixtures y helpers):

- `db`: sesión sobre la base de la prueba, para preparar datos y comprobar resultados.
- `entorno`: catálogos reales (los carga `cargar_catalogos`), calendario relativo a hoy,
  dos colegios, cuentas de los tres roles más el Supervisor original, una asignación
  vigente del Docente y alumnos de ambos subprogramas.
- `cabeceras`: cabecera Authorization por rol, con una sesión real en la tabla `sesion`.
- `client`: TestClient; cada petición usa su propia sesión de BD, como en producción.
- `correos`: buzón que reemplaza al SMTP (autouse): ninguna prueba sale a la red.
- `reloj`: fija la hora del servidor (`ahora_utc` y `hoy_lima`) para las pruebas que
  dependen del calendario.
"""
import os
import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Settings se lee al importar app: el entorno de prueba se fija antes, y no depende del
# .env de la máquina (en Jenkins no hay .env en el stage de pruebas).
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "clave-solo-para-pruebas"
os.environ["GMAIL_SMTP_USER"] = "sicedu.pruebas@sicedu.test"
os.environ["GMAIL_SMTP_APP_PASSWORD"] = "clave-de-aplicacion-de-prueba"
os.environ["CARGAR_COLEGIOS_INICIALES"] = "false"
for _variable in ("CORREO", "PASSWORD", "NOMBRES", "APELLIDOS", "DNI", "RECOVERY_HASHES"):
    os.environ[f"SUPERVISOR_ORIGINAL_{_variable}"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from passlib.context import CryptContext  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.cli.catalogos import cargar_catalogos  # noqa: E402
from app.cli.supervisor import DatosSupervisor, inicializar_supervisor_original  # noqa: E402
from app.core import security, tiempo  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.recovery_key import generar_llave  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organizacion import (  # noqa: E402
    Alumno,
    AnioEscolar,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Rol,
    Usuario,
)
from app.services import auth_service, recovery_key_service  # noqa: E402
from app.services.sesiones import crear_sesion_y_token  # noqa: E402

PASSWORD = "Clave-Valida-1"

# bcrypt con el coste de producción tarda del orden de medio segundo por hash, y estas
# pruebas crean varias cuentas cada una. Con el coste mínimo el algoritmo es el mismo y
# la suite baja de minutos a segundos; el código bajo prueba no cambia.
security.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)
_HASH_FICTICIO = security.hash_password("valor-ficticio-de-pruebas")
for _modulo in (security, auth_service, recovery_key_service):
    _modulo.HASH_FICTICIO = _HASH_FICTICIO

# Las 10 Recovery Keys del Supervisor original de prueba (en claro, solo para las pruebas).
LLAVES = [generar_llave() for _ in range(10)]
HASHES_LLAVES = [security.hash_password(llave) for llave in LLAVES]

CORREOS = {
    "Docente": "docente@sicedu.test",
    "Supervisor": "supervisor@sicedu.test",
    "Directivo": "directivo@sicedu.test",
    "Original": "original@sicedu.test",
}
ROLES = ("Docente", "Supervisor", "Directivo")


# ── Base de datos ───────────────────────────────────────────────────────────────────

def crear_motor_de_pruebas():
    """Motor SQLite en memoria con las funciones que la app espera de PostgreSQL."""
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    # listar_alumnos busca sin tildes con translate(), que en PostgreSQL es nativa y en
    # SQLite no existe. Se registra con la misma semántica para probar la búsqueda real.
    @event.listens_for(motor, "connect")
    def registrar_translate(conexion, _registro):
        conexion.create_function(
            "translate",
            3,
            lambda texto, desde, hasta: None if texto is None else texto.translate(str.maketrans(desde, hasta)),
        )

    SQLModel.metadata.create_all(motor)
    return motor


@pytest.fixture
def motor():
    motor = crear_motor_de_pruebas()
    yield motor
    motor.dispose()


@pytest.fixture
def db(motor):
    """Sesión para preparar datos y comprobar resultados. Antes de leer lo que escribió
    una petición, usar `db.expire_all()` (o `releer`)."""
    with Session(motor) as sesion:
        yield sesion


@pytest.fixture
def client(motor):
    def sesion_por_peticion():
        with Session(motor) as sesion:
            yield sesion

    app.dependency_overrides[get_db] = sesion_por_peticion
    yield TestClient(app)
    app.dependency_overrides.clear()


def releer(db: Session, modelo, clave):
    """La fila tal como está ahora en la BD (después de una petición)."""
    db.expire_all()
    return db.get(modelo, clave)


# ── Reloj ───────────────────────────────────────────────────────────────────────────

@dataclass
class Reloj:
    """Hora fija del servidor. `ahora_utc()` y `hoy_lima()` la usan (app/core/tiempo.py)."""

    monkeypatch: pytest.MonkeyPatch
    instante: Optional[datetime] = None

    def fijar(self, instante: datetime) -> None:
        self.instante = instante.astimezone(timezone.utc)
        reloj = self

        class RelojFijo(datetime):
            @classmethod
            def now(cls, tz=None):
                return reloj.instante.astimezone(tz) if tz else reloj.instante.replace(tzinfo=None)

        self.monkeypatch.setattr(tiempo, "datetime", RelojFijo)

    def avanzar(self, **delta) -> None:
        self.fijar(self.instante + timedelta(**delta))


@pytest.fixture
def reloj(monkeypatch) -> Reloj:
    return Reloj(monkeypatch)


def a_las(dia: date, hora: int = 10, minuto: int = 0) -> datetime:
    """Instante de un día de Lima a esa hora."""
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=tiempo.ZONA_LIMA)


# ── Entorno ─────────────────────────────────────────────────────────────────────────

@dataclass
class Entorno:
    """Lo que arma la fixture `entorno`. Los ids se leen de aquí, no se asumen."""

    hoy: date
    roles: dict[str, int]
    grados: dict[str, int]
    programas: dict[str, int]
    usuarios: dict[str, Usuario]
    anio: AnioEscolar
    periodos: dict[str, PeriodoAcademico]
    colegios: dict[str, Colegio]
    alumnos: dict[str, Alumno]
    id_docente: int
    extra: dict = field(default_factory=dict)

    @property
    def supervisor(self) -> Usuario:
        return self.usuarios["Supervisor"]

    @property
    def docente(self) -> Usuario:
        return self.usuarios["Docente"]


def _usuario(db, correo, id_rol, nombres, apellidos, dni, creado_por=None, **extra) -> Usuario:
    usuario = Usuario(
        id_rol=id_rol,
        correo=correo,
        password_hash=security.hash_password(PASSWORD),
        dni=dni,
        nombres=nombres,
        apellidos=apellidos,
        activo=True,
        creado_por=creado_por or 1,
        creado_en=tiempo.ahora_utc(),
        **extra,
    )
    db.add(usuario)
    db.flush()
    if creado_por is None:
        usuario.creado_por = usuario.id_usuario
    return usuario


def crear_colegio(db, nombre, grados, programas, id_actor, **campos) -> Colegio:
    ahora = tiempo.ahora_utc()
    colegio = Colegio(
        nombre=nombre,
        departamento=campos.pop("departamento", "Áncash"),
        provincia=campos.pop("provincia", "Carhuaz"),
        distrito=campos.pop("distrito", "Marcará"),
        creado_por=id_actor,
        creado_en=ahora,
        **campos,
    )
    db.add(colegio)
    db.flush()
    for id_grado in grados:
        db.add(ColegioGrado(id_colegio=colegio.id_colegio, id_grado=id_grado, creado_por=id_actor, creado_en=ahora))
    for id_programa in programas:
        db.add(
            ColegioPrograma(
                id_colegio=colegio.id_colegio, id_programa=id_programa, creado_por=id_actor, creado_en=ahora
            )
        )
    db.flush()
    return colegio


def crear_alumno(db, nombres, apellidos, colegio, id_grado, id_programa, id_actor, activo=True) -> Alumno:
    alumno = Alumno(
        nombres=nombres,
        apellidos=apellidos,
        id_colegio=colegio.id_colegio,
        id_grado=id_grado,
        id_programa_actual=id_programa,
        fecha_registro=tiempo.hoy_lima(),
        activo=activo,
        creado_por=id_actor,
        creado_en=tiempo.ahora_utc(),
    )
    db.add(alumno)
    db.flush()
    return alumno


def asignar(db, id_docente, colegio, ids_grados, periodo, id_actor, creado_en=None) -> None:
    for id_grado in ids_grados:
        db.add(
            DocenteColegioGrado(
                id_docente=id_docente,
                id_colegio=colegio.id_colegio,
                id_grado=id_grado,
                id_periodo_academico=periodo.id_periodo_academico,
                creado_por=id_actor,
                creado_en=creado_en or tiempo.ahora_utc(),
            )
        )
    db.flush()


def crear_docente(db, entorno: "Entorno", nombres, correo, dni) -> Usuario:
    """Cuenta de Docente con su ficha, sin asignaciones."""
    ahora = tiempo.ahora_utc()
    ficha = Docente(nombres=nombres, apellidos="Prueba", activo=True, creado_por=entorno.supervisor.id_usuario, creado_en=ahora)
    db.add(ficha)
    db.flush()
    return _usuario(
        db, correo, entorno.roles["Docente"], nombres, "Prueba", dni,
        creado_por=entorno.supervisor.id_usuario, id_docente=ficha.id_docente,
    )


def armar_entorno(db: Session) -> Entorno:
    hoy = tiempo.hoy_lima()
    cargar_catalogos(db)
    roles = {r.nombre: r.id_rol for r in db.exec(select(Rol)).all()}
    grados = {g.nombre: g.id_grado for g in db.exec(select(Grado)).all()}
    programas = {p.nombre: p.id_programa for p in db.exec(select(Programa)).all()}

    inicializar_supervisor_original(
        db,
        DatosSupervisor(
            correo=CORREOS["Original"], password=PASSWORD, nombres="Olga", apellidos="Original",
            dni="40000000", recovery_hashes=",".join(HASHES_LLAVES),
        ),
    )
    original = db.exec(select(Usuario).where(Usuario.correo == CORREOS["Original"])).one()
    id_actor = original.id_usuario
    supervisor = _usuario(db, CORREOS["Supervisor"], roles["Supervisor"], "Sara", "Supervisora", "40000001", id_actor)
    directivo = _usuario(db, CORREOS["Directivo"], roles["Directivo"], "Diego", "Directivo", "40000002", id_actor)
    ahora = tiempo.ahora_utc()
    ficha = Docente(nombres="Rosa", apellidos="Quispe", activo=True, creado_por=id_actor, creado_en=ahora)
    db.add(ficha)
    db.flush()
    docente = _usuario(
        db, CORREOS["Docente"], roles["Docente"], "Rosa", "Quispe", "40000003", id_actor, id_docente=ficha.id_docente
    )

    auditoria = {"creado_por": id_actor, "creado_en": ahora}
    anio = AnioEscolar(
        nombre=str(hoy.year), fecha_inicio=hoy - timedelta(days=200), fecha_fin=hoy + timedelta(days=150), **auditoria
    )
    db.add(anio)
    db.flush()
    rangos = {
        "pasado": (hoy - timedelta(days=150), hoy - timedelta(days=60), 1),
        "vigente": (hoy - timedelta(days=30), hoy + timedelta(days=30), 2),
        "futuro": (hoy + timedelta(days=31), hoy + timedelta(days=120), 3),
    }
    periodos = {}
    for clave, (inicio, fin, numero) in rangos.items():
        periodos[clave] = PeriodoAcademico(
            id_anio_escolar=anio.id_anio_escolar, numero=numero, fecha_inicio=inicio, fecha_fin=fin, **auditoria
        )
        db.add(periodos[clave])
    db.flush()

    todos_los_grados = sorted(grados.values())
    todos_los_programas = sorted(programas.values())
    colegios = {
        "Andino": crear_colegio(db, "Colegio Andino", todos_los_grados, todos_los_programas, id_actor),
        "Lejano": crear_colegio(
            db, "Colegio Lejano", todos_los_grados, todos_los_programas, id_actor,
            departamento="Lima", provincia=None, distrito="Miraflores",
        ),
    }
    primero, segundo, tercero = grados["1.º"], grados["2.º"], grados["3.º"]
    for periodo in ("pasado", "vigente"):
        asignar(db, ficha.id_docente, colegios["Andino"], [primero, segundo], periodos[periodo], id_actor)

    alfabetizacion, comprension = programas["Alfabetización"], programas["Comprensión Lectora"]
    alumnos = {
        # Con tilde a propósito: la búsqueda debe encontrarlo escribiendo "perez".
        "luis": crear_alumno(db, "Luis", "Pérez Quispe", colegios["Andino"], primero, alfabetizacion, id_actor),
        "ana": crear_alumno(db, "Ana", "Torres", colegios["Andino"], segundo, comprension, id_actor),
        "mario": crear_alumno(db, "Mario", "Lejano", colegios["Lejano"], tercero, comprension, id_actor),
        "inactivo": crear_alumno(
            db, "Iván", "Inactivo", colegios["Andino"], segundo, comprension, id_actor, activo=False
        ),
    }
    db.commit()
    for fila in [original, supervisor, directivo, docente, anio, *periodos.values(), *colegios.values(), *alumnos.values()]:
        db.refresh(fila)

    return Entorno(
        hoy=hoy,
        roles=roles,
        grados=grados,
        programas=programas,
        usuarios={"Docente": docente, "Supervisor": supervisor, "Directivo": directivo, "Original": original},
        anio=anio,
        periodos=periodos,
        colegios=colegios,
        alumnos=alumnos,
        id_docente=ficha.id_docente,
    )


@pytest.fixture
def entorno(db) -> Entorno:
    return armar_entorno(db)


def cabecera_de(db: Session, usuario: Usuario) -> dict[str, str]:
    """Abre una sesión real para el usuario y devuelve su cabecera Authorization."""
    token, _ = crear_sesion_y_token(db, usuario)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def id_sesion_de(cabecera: dict[str, str]) -> str:
    """Id de la sesión (claim `jti`) del token de una cabecera."""
    return jwt.get_unverified_claims(cabecera["Authorization"].split()[1])["jti"]


@pytest.fixture
def cabeceras(db, entorno) -> dict[str, dict[str, str]]:
    """Cabecera Authorization ya lista para cada rol: cabeceras["Supervisor"]."""
    return {rol: cabecera_de(db, usuario) for rol, usuario in entorno.usuarios.items()}


def iniciar_actividad(client, cabecera, **cuerpo) -> dict:
    respuesta = client.post("/actividades", json={"id_actividad": str(uuid.uuid4()), **cuerpo}, headers=cabecera)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["actividad"]


@pytest.fixture
def actividad(client, cabeceras) -> dict:
    """Actividad en curso del Docente (habilita sus escrituras)."""
    return iniciar_actividad(client, cabeceras["Docente"])


def iso(instante: datetime) -> str:
    return instante.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


# ── Correo: ninguna prueba sale a la red ────────────────────────────────────────────

class Buzon(list):
    """Correos enviados durante la prueba.

    `fallar = True` simula un SMTP caído, que es la rama que decide si la contraseña
    temporal se devuelve en la respuesta o se queda solo en el buzón del usuario.
    """

    fallar = False
    servidor = None
    timeout = None
    credenciales = None

    def para(self, correo: str) -> list:
        return [m for m in self if m["To"] == correo]

    def ultimo_texto(self, correo: str) -> str:
        return texto_del_correo(self.para(correo)[-1])

    def pin(self, correo: str) -> str:
        texto = self.ultimo_texto(correo)
        return texto.split("código de verificación es: ")[1][:6]

    def temporal(self, correo: str) -> str:
        texto = self.ultimo_texto(correo)
        return texto.split("Contraseña temporal: ")[1].split()[0]


@pytest.fixture(autouse=True)
def correos(monkeypatch) -> Buzon:
    """Sustituye smtplib.SMTP_SSL por un doble que guarda los mensajes."""
    buzon = Buzon()

    class SmtpDePrueba:
        def __init__(self, host, puerto, timeout=None):
            buzon.servidor = (host, puerto)
            buzon.timeout = timeout
            if buzon.fallar:
                raise OSError("SMTP no disponible (simulado)")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def login(self, usuario, contrasena):
            buzon.credenciales = (usuario, contrasena)

        def send_message(self, mensaje):
            buzon.append(mensaje)

    monkeypatch.setattr(smtplib, "SMTP_SSL", SmtpDePrueba)
    return buzon


def texto_del_correo(mensaje) -> str:
    """Parte en texto plano de un correo del buzón, ya decodificada (viajan en base64)."""
    return "\n".join(
        parte.get_payload(decode=True).decode("utf-8")
        for parte in mensaje.walk()
        if parte.get_content_type() == "text/plain"
    )


def motivo(respuesta) -> Optional[str]:
    return respuesta.json().get("motivo")
