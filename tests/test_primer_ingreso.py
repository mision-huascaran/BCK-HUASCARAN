"""Contraseña temporal de 48 horas y cambio obligatorio en el primer ingreso (CU001, CU016)."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app import seed_data
from app.cli.catalogos import cargar_catalogos
from app.cli.seed_e2e import CUENTAS, sembrar_e2e
from app.cli.supervisor import DatosSupervisor, inicializar_supervisor_original
from app.core.politica_contrasena import VIGENCIA_CONTRASENA_TEMPORAL
from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import ControlAccesoCorreo, Sesion
from tests.conftest import CORREOS, PASSWORD, motivo, releer

NUEVO = "nuevo.docente@sicedu.test"
ORIGINAL = CORREOS["Original"]
NUEVA = "Definitiva-9"
TEMPORAL_EXPIRADA = "Tu contraseña temporal ha expirado. Usa '¿Olvidaste tu contraseña?' para generar una nueva."


@pytest.fixture
def nuevo(client, entorno, cabeceras, correos) -> dict:
    """Cuenta de Directivo recién creada por el Supervisor: su id y su contraseña temporal."""
    respuesta = client.post(
        "/usuarios",
        json={"nombres": "Nora", "apellidos": "Nueva", "dni": "50000009", "correo": NUEVO, "id_rol": entorno.roles["Directivo"]},
        headers=cabeceras["Supervisor"],
    )
    assert respuesta.status_code == 201, respuesta.text
    return {"id": respuesta.json()["usuario"]["id"], "temporal": correos.temporal(NUEVO)}


def login(client, correo=NUEVO, password=None):
    return client.post("/login", json={"correo": correo, "password": password})


def primer_ingreso(client, temporal, nueva=NUEVA, confirmacion=None, correo=NUEVO):
    return client.post(
        "/password/primer-ingreso",
        json={
            "correo": correo,
            "contraseña_temporal": temporal,
            "contraseña_nueva": nueva,
            "confirmar_contraseña_nueva": confirmacion if confirmacion is not None else nueva,
        },
    )


def vencer_temporal(db, id_usuario) -> None:
    usuario = releer(db, Usuario, id_usuario)
    usuario.contrasena_temporal_expira = ahora_utc() - timedelta(minutes=1)
    db.add(usuario)
    db.commit()


def fallos(db, correo=NUEVO) -> int:
    db.expire_all()
    control = db.get(ControlAccesoCorreo, correo)
    return 0 if control is None else control.intentos_fallidos


# ── Alta ────────────────────────────────────────────────────────────────────────────

def test_la_cuenta_nueva_debe_cambiar_su_temporal_en_48_horas(client, db, cabeceras, nuevo, correos):
    usuario = releer(db, Usuario, nuevo["id"])

    assert usuario.debe_cambiar_contrasena is True
    restante = usuario.contrasena_temporal_expira - ahora_utc()
    assert timedelta(hours=47, minutes=59) < restante <= timedelta(hours=48)
    texto = correos.ultimo_texto(NUEVO)
    assert "vence en 48 horas" in texto
    assert "¿Olvidaste tu contraseña?" in texto
    assert "primer ingreso" in texto


def test_el_supervisor_ve_quien_no_hizo_su_primer_ingreso(client, entorno, cabeceras, nuevo):
    sup = cabeceras["Supervisor"]

    detalle = client.get(f"/usuarios/{nuevo['id']}", headers=sup).json()
    listado = client.get("/usuarios", params={"rol": "Directivo"}, headers=sup).json()["items"]

    marcas = {u["correo"]: u["debe_cambiar_contrasena"] for u in listado}
    assert detalle["debe_cambiar_contrasena"] is True
    assert len(marcas) == 2
    assert marcas[NUEVO] is True
    assert marcas[CORREOS["Directivo"]] is False


# ── Login con la temporal ───────────────────────────────────────────────────────────

def test_login_con_la_temporal_pide_el_cambio_sin_abrir_sesion(client, db, nuevo):
    for _ in range(4):
        login(client, password="Incorrecta-1")

    respuesta = login(client, password=nuevo["temporal"])

    assert respuesta.status_code == 403
    assert respuesta.json() == {
        "detail": "Debes establecer una nueva contraseña para continuar.", "motivo": "cambio_contrasena_requerido",
    }
    assert db.exec(select(Sesion).where(Sesion.id_usuario == nuevo["id"])).all() == []
    assert fallos(db) == 0  # credenciales correctas: cuenta como intento exitoso


def test_login_con_la_temporal_vencida(client, db, nuevo):
    vencer_temporal(db, nuevo["id"])

    respuesta = login(client, password=nuevo["temporal"])

    assert respuesta.status_code == 403
    assert respuesta.json() == {"detail": TEMPORAL_EXPIRADA, "motivo": "contrasena_temporal_expirada"}


def test_login_con_la_temporal_equivocada_sigue_siendo_401(client, db, nuevo):
    respuesta = login(client, password="Incorrecta-1")

    assert (respuesta.status_code, motivo(respuesta)) == (401, "credenciales_invalidas")
    assert fallos(db) == 1


# ── POST /password/primer-ingreso ───────────────────────────────────────────────────

def test_primer_ingreso_cambia_la_contrasena_y_abre_la_sesion(client, db, nuevo):
    respuesta = primer_ingreso(client, nuevo["temporal"])

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"access_token", "token_type", "sesion", "usuario"}
    assert (cuerpo["usuario"]["id"], cuerpo["usuario"]["rol"]) == (nuevo["id"], "Directivo")
    me = client.get("/me", headers={"Authorization": f"Bearer {cuerpo['access_token']}"})
    assert me.status_code == 200
    usuario = releer(db, Usuario, nuevo["id"])
    assert (usuario.debe_cambiar_contrasena, usuario.contrasena_temporal_expira) == (False, None)
    assert login(client, password=NUEVA).status_code == 200
    assert login(client, password=nuevo["temporal"]).status_code == 401
    # Ya no hay cambio pendiente: repetirlo es como cualquier credencial inválida.
    assert motivo(primer_ingreso(client, NUEVA, nueva="Otra-Clave-77")) == "credenciales_invalidas"


@pytest.mark.parametrize("caso", ["temporal_equivocada", "sin_cuenta", "sin_marca", "inactiva"])
def test_primer_ingreso_responde_el_401_generico(client, db, entorno, nuevo, caso):
    correo, temporal = NUEVO, nuevo["temporal"]
    if caso == "temporal_equivocada":
        temporal = "Incorrecta-1"
    elif caso == "sin_cuenta":
        correo = "nadie@sicedu.test"
    elif caso == "sin_marca":
        correo, temporal = CORREOS["Docente"], PASSWORD
    else:
        usuario = releer(db, Usuario, nuevo["id"])
        usuario.activo = False
        db.add(usuario)
        db.commit()

    respuesta = primer_ingreso(client, temporal, correo=correo)

    assert respuesta.status_code == 401
    assert respuesta.json() == {"detail": "Correo o contraseña incorrectos.", "motivo": "credenciales_invalidas"}
    assert fallos(db, correo) == 1


def test_primer_ingreso_comparte_el_bloqueo_del_login(client, nuevo):
    for _ in range(4):
        login(client, password="Incorrecta-1")

    quinto = primer_ingreso(client, "Incorrecta-1")

    assert (quinto.status_code, motivo(quinto)) == (429, "bloqueo_temporal")
    assert motivo(primer_ingreso(client, nuevo["temporal"])) == "bloqueo_temporal"


def test_primer_ingreso_con_la_temporal_vencida(client, db, nuevo):
    vencer_temporal(db, nuevo["id"])

    respuesta = primer_ingreso(client, nuevo["temporal"])

    assert (respuesta.status_code, motivo(respuesta)) == (403, "contrasena_temporal_expirada")
    assert releer(db, Usuario, nuevo["id"]).debe_cambiar_contrasena is True
    assert fallos(db) == 0


@pytest.mark.parametrize(
    "nueva, confirmacion, codigo, esperado",
    [("corta", None, 422, "validacion"), (NUEVA, "Distinta-9", 422, "validacion"), (None, None, 400, "password_igual")],
    ids=["politica", "no_coinciden", "igual_a_la_temporal"],
)
def test_primer_ingreso_valida_la_contrasena_nueva(client, db, nuevo, nueva, confirmacion, codigo, esperado):
    respuesta = primer_ingreso(client, nuevo["temporal"], nueva=nueva or nuevo["temporal"], confirmacion=confirmacion)

    assert respuesta.status_code == codigo
    assert motivo(respuesta) == esperado
    assert releer(db, Usuario, nuevo["id"]).debe_cambiar_contrasena is True
    assert fallos(db) == 0


def test_restablecer_con_pin_quita_el_cambio_obligatorio(client, db, nuevo, correos):
    vencer_temporal(db, nuevo["id"])
    client.post("/password/recuperar", json={"correo": NUEVO})

    respuesta = client.post(
        "/password/restablecer",
        json={"correo": NUEVO, "codigo": correos.pin(NUEVO), "contraseña_nueva": NUEVA, "confirmar_contraseña_nueva": NUEVA},
    )

    assert respuesta.status_code == 200
    usuario = releer(db, Usuario, nuevo["id"])
    assert (usuario.debe_cambiar_contrasena, usuario.contrasena_temporal_expira) == (False, None)
    assert login(client, password=NUEVA).status_code == 200


# ── Cuentas que entran directo ──────────────────────────────────────────────────────

def test_cuentas_existentes_entran_directo(client, entorno):
    """El Original del entorno ya hizo su primer ingreso (lo deja así `armar_entorno`)."""
    for rol in ("Original", "Supervisor", "Docente", "Directivo"):
        assert login(client, CORREOS[rol], PASSWORD).status_code == 200, rol


def test_supervisor_original_nuevo_debe_cambiar_su_contrasena(client, db):
    """CU001: la contraseña del entorno es temporal, con la vigencia de las de CU016."""
    cargar_catalogos(db)
    inicializar_supervisor_original(
        db, DatosSupervisor(correo=ORIGINAL, password=PASSWORD, nombres="Olga", apellidos="Original", dni="40000000")
    )
    original = db.exec(select(Usuario).where(Usuario.correo == ORIGINAL)).one()

    respuesta = login(client, ORIGINAL, PASSWORD)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "cambio_contrasena_requerido")
    assert original.debe_cambiar_contrasena is True
    assert original.contrasena_temporal_expira == original.creado_en + VIGENCIA_CONTRASENA_TEMPORAL
    assert primer_ingreso(client, PASSWORD, correo=ORIGINAL).status_code == 200
    assert login(client, ORIGINAL, NUEVA).status_code == 200


def test_supervisor_original_existente_no_se_toca(db, entorno):
    """Si ya existe, el script no lo vuelve a marcar aunque se ejecute en cada arranque."""
    resumen = inicializar_supervisor_original(
        db, DatosSupervisor(correo=ORIGINAL, password=PASSWORD, nombres="Olga", apellidos="Original", dni="40000000")
    )

    assert resumen.omitidos == ["el Supervisor original ya existe"]
    assert releer(db, Usuario, entorno.usuarios["Original"].id_usuario).debe_cambiar_contrasena is False


def test_cuentas_del_seed_entran_directo(client, motor, monkeypatch):
    monkeypatch.setattr(seed_data, "engine", motor)
    seed_data.seed()

    for correo, password in (
        (seed_data.PROFESOR_CORREO, seed_data.PROFESOR_PASSWORD),
        (seed_data.JEFA_CORREO, seed_data.JEFA_PASSWORD),
        (seed_data.DIRECTIVO_CORREO, seed_data.DIRECTIVO_PASSWORD),
    ):
        assert login(client, correo, password).status_code == 200, correo


def test_cuentas_e2e_entran_directo_aunque_tuvieran_la_marca(client, db, entorno):
    contrasenas = {c.variable: f"{c.rol}-E2e-1" for c in CUENTAS}
    variables = {"SEED_E2E": "true", "ENTORNO": "qa", **contrasenas}
    sembrar_e2e(db, variables)
    activas = [c for c in CUENTAS if c.activo]
    for cuenta in activas:
        usuario = db.exec(select(Usuario).where(Usuario.correo == cuenta.correo)).one()
        usuario.debe_cambiar_contrasena = True
        usuario.contrasena_temporal_expira = ahora_utc() + timedelta(hours=1)
        db.add(usuario)
    db.commit()

    sembrar_e2e(db, variables)

    for cuenta in activas:
        assert login(client, cuenta.correo, contrasenas[cuenta.variable]).status_code == 200, cuenta.correo
