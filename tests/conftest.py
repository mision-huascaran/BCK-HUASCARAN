"""
Fixtures de las pruebas de la API.

Las pruebas no necesitan PostgreSQL: cada una corre sobre una base SQLite en memoria,
recién creada, que reemplaza a get_db. Así se ejecutan igual en local y en el stage de
pruebas de Jenkins, donde no hay base de datos.

Piezas principales (para agregar pruebas, partir de estas fixtures y helpers):

- `db`: sesión sobre la base de la prueba, para preparar datos y comprobar resultados.
- `entorno`: catálogos reales (los carga `cargar_catalogos`), calendario general relativo
  a hoy (PA1 terminado, hoy en PA2, PA3 y PA4 futuros, con sus PR), dos colegios, cuentas
  de los tres roles más el Supervisor original, una asignación vigente del Docente y
  alumnos de ambos subprogramas.
- `crear_calendario` / `agregar_no_lectivo` / `fijar_periodo`: fábricas para armar o
  mover calendarios (general o particular) sin pasar por las validaciones de la API.
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
for _variable in ("CORREO", "PASSWORD", "NOMBRES", "APELLIDOS", "DNI"):
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
from app.main import app  # noqa: E402
from app.models.organizacion import (  # noqa: E402
    ORIGEN_MANUAL,
    SECCION_UNICA,
    Alumno,
    AlumnoPertenencia,
    AnioEscolar,
    Calendario,
    CalendarioPeriodo,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    Docente,
    DiaNoLectivo,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Rol,
    Seccion,
    Usuario,
)
from app.services import auth_service  # noqa: E402
from app.services.sesiones import crear_sesion_y_token  # noqa: E402

PASSWORD = "Clave-Valida-1"

# bcrypt con el coste de producción tarda del orden de medio segundo por hash, y estas
# pruebas crean varias cuentas cada una. Con el coste mínimo el algoritmo es el mismo y
# la suite baja de minutos a segundos; el código bajo prueba no cambia.
security.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)
_HASH_FICTICIO = security.hash_password("valor-ficticio-de-pruebas")
for _modulo in (security, auth_service):
    _modulo.HASH_FICTICIO = _HASH_FICTICIO

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


# ── Calendario ──────────────────────────────────────────────────────────────────────

# Claves de `entorno.periodos` -> número de PA.
NUMERO_DE_PA = {"pasado": 1, "vigente": 2, "futuro": 3, "cuarto": 4}


def rangos_relativos(hoy: date) -> dict[str, tuple[date, date]]:
    """Los 8 periodos del calendario del entorno: PA1 terminado, hoy en PA2, PA3 y PA4
    futuros; los PR entre ellos (PR2 y PR3 de un día)."""
    dia = timedelta(days=1)
    return {
        "PA1": (hoy - 150 * dia, hoy - 60 * dia),
        "PR1": (hoy - 59 * dia, hoy - 31 * dia),
        "PA2": (hoy - 30 * dia, hoy + 30 * dia),
        "PR2": (hoy + 31 * dia, hoy + 31 * dia),
        "PA3": (hoy + 32 * dia, hoy + 120 * dia),
        "PR3": (hoy + 121 * dia, hoy + 121 * dia),
        "PA4": (hoy + 122 * dia, hoy + 140 * dia),
        "PR4": (hoy + 141 * dia, hoy + 150 * dia),
    }


def crear_calendario(
    db, id_anio_escolar: int, rangos: dict[str, tuple[date, date]], id_actor: int, id_colegio=None, activo=True
) -> Calendario:
    """Calendario (general o, con `id_colegio`, particular) con los periodos de `rangos`
    ({"PA1": (inicio, fin), ...}), sin validar. Crea los PA del año que falten."""
    ahora = tiempo.ahora_utc()
    existentes = {
        p.numero for p in db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == id_anio_escolar))
    }
    for numero in sorted({1, 2, 3, 4} - existentes):
        db.add(PeriodoAcademico(id_anio_escolar=id_anio_escolar, numero=numero, creado_por=id_actor, creado_en=ahora))
    calendario = Calendario(
        id_anio_escolar=id_anio_escolar, id_colegio=id_colegio, activo=activo, creado_por=id_actor, creado_en=ahora
    )
    db.add(calendario)
    db.flush()
    for nombre, (inicio, fin) in rangos.items():
        db.add(CalendarioPeriodo(id_calendario=calendario.id_calendario, tipo=nombre[:2], numero=int(nombre[2]),
                                 fecha_inicio=inicio, fecha_fin=fin))
    db.flush()
    return calendario


def rangos_en_receso(hoy: date, fin_pa2=None) -> dict[str, tuple[date, date]]:
    """Rangos relativos con el PA2 terminado (por defecto, ayer) y el PR2 desde el día
    siguiente: hoy ese calendario está en receso."""
    rangos = rangos_relativos(hoy)
    fin = fin_pa2 or hoy - timedelta(days=1)
    rangos["PA2"] = (rangos["PA2"][0], fin)
    rangos["PR2"] = (fin + timedelta(days=1), rangos["PR2"][1])
    return rangos


def crear_particular(db, entorno: "Entorno", rangos=None, colegio="Lejano") -> Calendario:
    """Calendario particular del colegio en el año del entorno (por defecto, como el general)."""
    particular = crear_calendario(
        db, entorno.anio.id_anio_escolar, rangos or rangos_relativos(entorno.hoy), 1,
        id_colegio=entorno.colegios[colegio].id_colegio,
    )
    db.commit()
    return particular


def crear_calendario_general(db, anio: AnioEscolar, id_actor: int, rangos=None) -> Calendario:
    """Calendario general del año (por defecto, los rangos relativos a hoy)."""
    return crear_calendario(db, anio.id_anio_escolar, rangos or rangos_relativos(tiempo.hoy_lima()), id_actor)


def agregar_no_lectivo(db, calendario: Calendario, inicio: date, fin=None, motivo="Feriado", origen=ORIGEN_MANUAL):
    db.add(DiaNoLectivo(id_calendario=calendario.id_calendario, motivo=motivo, fecha_inicio=inicio,
                        fecha_fin=fin or inicio, origen=origen, creado_por=1, creado_en=tiempo.ahora_utc()))
    db.commit()


def fijar_periodo(db, calendario: Calendario, nombre: str, inicio=None, fin=None) -> None:
    """Mueve las fechas de un periodo ("PA2", "PR4"...) sin validar (para preparar casos)."""
    fila = db.exec(select(CalendarioPeriodo).where(
        CalendarioPeriodo.id_calendario == calendario.id_calendario,
        CalendarioPeriodo.tipo == nombre[:2], CalendarioPeriodo.numero == int(nombre[2]),
    )).one()
    fila.fecha_inicio = inicio or fila.fecha_inicio
    fila.fecha_fin = fin or fila.fecha_fin
    db.add(fila)
    db.commit()


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
    # "pasado" (PA1), "vigente" (PA2), "futuro" (PA3) y "cuarto" (PA4).
    periodos: dict[str, PeriodoAcademico]
    calendario: Calendario
    # Fechas del calendario general: {"PA1": (inicio, fin), ..., "PR4": (...)}.
    rangos: dict[str, tuple[date, date]]
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

    def _clave(self, clave: str) -> str:
        return f"PA{NUMERO_DE_PA[clave]}" if clave in NUMERO_DE_PA else clave

    def inicio(self, clave: str) -> date:
        """Primer día del PA de esa clave ("vigente") o del periodo ("PR1")."""
        return self.rangos[self._clave(clave)][0]

    def fin(self, clave: str) -> date:
        return self.rangos[self._clave(clave)][1]


def terminar_pa_vigente(db, entorno: Entorno) -> None:
    """El PA2 termina ayer y hoy empieza el PR2 (los colegios del general quedan en receso)."""
    fijar_periodo(db, entorno.calendario, "PA2", fin=entorno.hoy - timedelta(days=1))
    fijar_periodo(db, entorno.calendario, "PR2", inicio=entorno.hoy)


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


def crear_colegio(db, nombre, grados, programas, id_actor, dias_clase=None, **campos) -> Colegio:
    """Colegio con esos grados y programas. `dias_clase`: {id_grado: día} (sin día por defecto)."""
    ahora = tiempo.ahora_utc()
    dias_clase = dias_clase or {}
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
        db.add(ColegioGrado(id_colegio=colegio.id_colegio, id_grado=id_grado, dia_clase=dias_clase.get(id_grado),
                            creado_por=id_actor, creado_en=ahora))
        db.add(Seccion(id_colegio=colegio.id_colegio, id_grado=id_grado, nombre=SECCION_UNICA,
                       creado_por=id_actor, creado_en=ahora))
    for id_programa in programas:
        db.add(
            ColegioPrograma(
                id_colegio=colegio.id_colegio, id_programa=id_programa, creado_por=id_actor, creado_en=ahora
            )
        )
    db.flush()
    return colegio


def seccion_de(db, colegio, id_grado, nombre=SECCION_UNICA) -> Seccion:
    return db.exec(
        select(Seccion).where(
            Seccion.id_colegio == colegio.id_colegio, Seccion.id_grado == id_grado, Seccion.nombre == nombre
        )
    ).one()


def agregar_seccion(db, colegio, id_grado, nombre, id_actor) -> Seccion:
    seccion = Seccion(id_colegio=colegio.id_colegio, id_grado=id_grado, nombre=nombre,
                      creado_por=id_actor, creado_en=tiempo.ahora_utc())
    db.add(seccion)
    db.flush()
    return seccion


def crear_alumno(
    db, nombres, apellidos, colegio, id_grado, id_programa, id_actor, activo=True, seccion=None, desde=None
) -> Alumno:
    """Alumno en la sección "Única" de su grado (o `seccion`). Si está activo, con su
    intervalo de pertenencia vigente desde `desde` (hoy por defecto)."""
    seccion = seccion or seccion_de(db, colegio, id_grado)
    alumno = Alumno(
        nombres=nombres,
        apellidos=apellidos,
        id_colegio=colegio.id_colegio,
        id_grado=id_grado,
        id_seccion=seccion.id_seccion,
        id_programa_actual=id_programa,
        fecha_registro=tiempo.hoy_lima(),
        activo=activo,
        creado_por=id_actor,
        creado_en=tiempo.ahora_utc(),
    )
    db.add(alumno)
    db.flush()
    if activo:
        db.add(AlumnoPertenencia(
            id_alumno=alumno.id_alumno, id_colegio=colegio.id_colegio, id_grado=id_grado,
            id_seccion=seccion.id_seccion, id_programa=id_programa, desde=desde or tiempo.hoy_lima(),
            creado_por=id_actor, creado_en=tiempo.ahora_utc(),
        ))
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
            dni="40000000",
        ),
    )
    original = db.exec(select(Usuario).where(Usuario.correo == CORREOS["Original"])).one()
    # Nace con la contraseña temporal por cambiar (CU001); aquí ya la cambió, para que las
    # pruebas puedan entrar con él. El cambio obligatorio se prueba en test_cli.
    original.debe_cambiar_contrasena = False
    original.contrasena_temporal_expira = None
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
    rangos = rangos_relativos(hoy)
    calendario = crear_calendario_general(db, anio, id_actor, rangos)
    por_numero = {
        p.numero: p
        for p in db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar))
    }
    periodos = {clave: por_numero[numero] for clave, numero in NUMERO_DE_PA.items()}

    todos_los_grados = sorted(grados.values())
    todos_los_programas = sorted(programas.values())
    colegios = {
        "Andino": crear_colegio(db, "Colegio Andino", todos_los_grados, todos_los_programas, id_actor),
        "Lejano": crear_colegio(
            db, "Colegio Lejano", todos_los_grados, todos_los_programas, id_actor,
            departamento="Lima", provincia="Lima", distrito="Miraflores",
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
    filas = [original, supervisor, directivo, docente, anio, calendario, *periodos.values(), *colegios.values(),
             *alumnos.values()]
    for fila in filas:
        db.refresh(fila)

    return Entorno(
        hoy=hoy,
        roles=roles,
        grados=grados,
        programas=programas,
        usuarios={"Docente": docente, "Supervisor": supervisor, "Directivo": directivo, "Original": original},
        anio=anio,
        periodos=periodos,
        calendario=calendario,
        rangos=rangos,
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


def hoy_iso() -> str:
    """Hoy (día de Lima) como fecha ISO, para las fechas efectivas de los requests."""
    return tiempo.hoy_lima().isoformat()


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
