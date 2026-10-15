"""Login, sesión, logout y cierre diferido (CU002, CU003, CU007, CU008)."""
import uuid
from datetime import timedelta

import pytest
from jose import jwt
from sqlmodel import select

from app.core.security import create_access_token
from app.core.tiempo import ahora_utc
from app.models.seguridad import ControlAccesoCorreo, Sesion
from app.models.trazabilidad import Actividad
from app.services.sesiones import DURACION_SESION
from tests.conftest import (
    CORREOS,
    PASSWORD,
    cabecera_de,
    id_sesion_de,
    iniciar_actividad,
    instante,
    iso,
    motivo,
    releer,
)

MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Por seguridad, intente nuevamente en 15 minutos."


def login(client, correo, password=PASSWORD):
    return client.post("/login", json={"correo": correo, "password": password})


def bearer(respuesta) -> dict:
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


# ── CU002: login ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol", ["Docente", "Supervisor", "Directivo"])
def test_cu002_login_correcto_abre_una_sesion_de_8_horas(client, db, entorno, rol):
    respuesta = login(client, CORREOS[rol])

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["token_type"] == "bearer"
    assert cuerpo["usuario"]["rol"] == rol
    assert cuerpo["usuario"]["es_supervisor_original"] is False
    sesion = db.exec(select(Sesion)).one()
    assert instante(cuerpo["sesion"]["expira"]) - instante(cuerpo["sesion"]["inicio"]) == DURACION_SESION
    claims = jwt.get_unverified_claims(cuerpo["access_token"])
    assert claims["jti"] == cuerpo["sesion"]["id"]
    assert sesion.fin is None


def test_cu002_el_correo_se_normaliza(client, entorno):
    assert login(client, "  DOCENTE@Sicedu.Test ").status_code == 200


@pytest.mark.parametrize(
    "correo, password",
    [(CORREOS["Docente"], "Incorrecta-1"), ("nadie@sicedu.test", PASSWORD)],
    ids=["contrasena_incorrecta", "correo_inexistente"],
)
def test_cu002_credenciales_invalidas_dan_el_mismo_401(client, entorno, correo, password):
    respuesta = login(client, correo, password)

    assert respuesta.status_code == 401
    assert respuesta.json() == {"detail": "Correo o contraseña incorrectos.", "motivo": "credenciales_invalidas"}


def test_cu002_cuenta_desactivada_avisa_y_cuenta_como_fallo(client, db, entorno):
    docente = entorno.docente
    docente.activo = False
    db.add(docente)
    db.commit()

    respuesta = login(client, CORREOS["Docente"])

    assert respuesta.status_code == 403
    assert motivo(respuesta) == "cuenta_desactivada"
    assert releer(db, ControlAccesoCorreo, CORREOS["Docente"]).intentos_fallidos == 1


@pytest.mark.parametrize("correo", [CORREOS["Docente"], "nadie@sicedu.test"], ids=["existente", "inexistente"])
def test_cu002_login_bloqueado_tras_5_fallos(client, entorno, correo):
    codigos = [login(client, correo, "Incorrecta-1").status_code for _ in range(5)]

    assert codigos == [401, 401, 401, 401, 429]
    bloqueado = login(client, correo)
    assert bloqueado.status_code == 429
    assert bloqueado.json() == {"detail": MENSAJE_BLOQUEO, "motivo": "bloqueo_temporal"}


def test_cu002_el_bloqueo_vence_a_los_15_minutos(client, entorno, reloj):
    reloj.fijar(ahora_utc())
    for _ in range(5):
        login(client, CORREOS["Docente"], "Incorrecta-1")
    reloj.avanzar(minutes=16)

    assert login(client, CORREOS["Docente"]).status_code == 200


def test_cu002_un_login_exitoso_reinicia_el_contador(client, db, entorno):
    for _ in range(4):
        login(client, CORREOS["Docente"], "Incorrecta-1")
    assert login(client, CORREOS["Docente"]).status_code == 200

    assert releer(db, ControlAccesoCorreo, CORREOS["Docente"]).intentos_fallidos == 0
    assert login(client, CORREOS["Docente"], "Incorrecta-1").status_code == 401


# ── Sesión: el token vale mientras su sesión siga abierta y vigente ─────────────────

def test_me_devuelve_el_perfil_y_la_sesion(client, entorno, cabeceras):
    respuesta = client.get("/me", headers=cabeceras["Docente"])

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["correo"] == CORREOS["Docente"]
    assert cuerpo["id_docente"] == entorno.id_docente
    assert set(cuerpo["sesion"]) == {"inicio", "expira"}


@pytest.mark.parametrize(
    "cabecera",
    [{}, {"Authorization": "Bearer no-es-un-jwt"}, {"Authorization": "Bearer " + jwt.encode({"sub": "1"}, "otra-clave")}],
    ids=["sin_token", "malformado", "otra_firma"],
)
def test_token_invalido_da_401_sesion_invalida(client, entorno, cabecera):
    respuesta = client.get("/me", headers=cabecera)

    assert respuesta.status_code == 401
    assert motivo(respuesta) == "sesion_invalida"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_token_sin_jti_no_sirve(client, entorno):
    token = create_access_token({"sub": str(entorno.docente.id_usuario)})
    respuesta = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert motivo(respuesta) == "sesion_invalida"


def test_cu007_sesion_vencida_da_401_sesion_expirada_y_queda_cerrada(client, db, entorno, cabeceras, reloj):
    reloj.fijar(ahora_utc() + DURACION_SESION + timedelta(seconds=1))

    respuesta = client.get("/me", headers=cabeceras["Docente"])

    assert respuesta.status_code == 401
    assert motivo(respuesta) == "sesion_expirada"
    sesion = db.exec(select(Sesion).where(Sesion.id_usuario == entorno.docente.id_usuario)).one()
    db.refresh(sesion)
    assert sesion.tipo_cierre == "Automático por expiración"
    assert sesion.fin == sesion.expira
    assert motivo(client.get("/me", headers=cabeceras["Docente"])) == "sesion_invalida"


def test_cu007_la_expiracion_cierra_la_actividad_en_el_vencimiento(client, db, entorno, cabeceras, reloj):
    reloj.fijar(ahora_utc())
    actividad = iniciar_actividad(client, cabeceras["Docente"])
    reloj.avanzar(hours=9)

    login(client, CORREOS["Docente"])

    fila = releer(db, Actividad, uuid.UUID(actividad["id"]))
    sesion = db.get(Sesion, fila.id_sesion)
    assert fila.tipo_cierre == "Automático por expiración de sesión"
    assert fila.fin == sesion.expira


def test_usuario_desactivado_despues_del_login_pierde_el_acceso(client, db, entorno, cabeceras):
    docente = entorno.docente
    docente.activo = False
    db.add(docente)
    db.commit()

    assert motivo(client.get("/me", headers=cabeceras["Docente"])) == "sesion_invalida"


# ── CU003 / CU008: logout ───────────────────────────────────────────────────────────

def test_cu003_logout_cierra_la_sesion_en_el_servidor(client, entorno, cabeceras):
    respuesta = client.post("/logout", headers=cabeceras["Supervisor"])

    assert respuesta.status_code == 204
    assert motivo(client.get("/me", headers=cabeceras["Supervisor"])) == "sesion_invalida"
    assert client.post("/logout", headers=cabeceras["Supervisor"]).status_code == 204


def test_cu008_logout_fuerza_el_cierre_de_la_actividad(client, db, entorno, cabeceras):
    actividad = iniciar_actividad(client, cabeceras["Docente"])

    client.post("/logout", headers=cabeceras["Docente"])

    db.expire_all()
    fila = db.exec(select(Actividad)).one()
    assert str(fila.id_actividad) == actividad["id"]
    assert fila.tipo_cierre == "Forzado por cierre de sesión"
    assert fila.fin is not None


def test_cu003_logout_de_una_sesion_vencida_la_cierra_por_expiracion(client, db, entorno, cabeceras, reloj):
    reloj.fijar(ahora_utc() + timedelta(hours=9))

    assert client.post("/logout", headers=cabeceras["Docente"]).status_code == 204

    db.expire_all()
    assert db.exec(select(Sesion.tipo_cierre).where(Sesion.id_usuario == entorno.docente.id_usuario)).one() == (
        "Automático por expiración"
    )


@pytest.mark.parametrize("cabecera", [{}, {"Authorization": "Bearer basura"}], ids=["sin_token", "invalido"])
def test_cu003_logout_sin_token_valido_da_401(client, entorno, cabecera):
    assert client.post("/logout", headers=cabecera).status_code == 401


def test_cu003_logout_con_token_sin_jti_responde_204(client, entorno):
    token = create_access_token({"sub": "x"})
    assert client.post("/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204


# ── CU008: cierre diferido de una sesión anterior (Tanda 8.1) ───────────────────────

@pytest.fixture
def dos_sesiones(client, db, entorno):
    """Docente con una actividad en curso en la sesión A, que sigue abierta, y una
    sesión B nueva desde la que se sincroniza."""
    cabecera_a = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera_a)
    sesion_a = db.exec(select(Sesion).where(Sesion.id_usuario == entorno.docente.id_usuario)).one()
    cabecera_b = cabecera_de(db, entorno.docente)
    return {"a": sesion_a, "cabecera_a": cabecera_a, "b": cabecera_b, "actividad": actividad}


def cerrar(client, cabecera, id_sesion, fin):
    return client.post(f"/me/sesiones/{id_sesion}/cerrar", json={"fin": fin}, headers=cabecera)


def test_cu008_cierre_diferido_cierra_la_sesion_y_fuerza_la_actividad(client, db, dos_sesiones):
    fin = iso(ahora_utc())

    respuesta = cerrar(client, dos_sesiones["b"], dos_sesiones["a"].id_sesion, fin)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["sesion"]["tipo_cierre"] == "Manual"
    assert cuerpo["actividad"]["id"] == dos_sesiones["actividad"]["id"]
    assert cuerpo["actividad"]["tipo_cierre"] == "Forzado por cierre de sesión"
    assert motivo(client.get("/me", headers=dos_sesiones["cabecera_a"])) == "sesion_invalida"
    assert iniciar_actividad(client, dos_sesiones["b"])["estado"] == "en_curso"
    repetida = cerrar(client, dos_sesiones["b"], dos_sesiones["a"].id_sesion, fin)
    assert repetida.status_code == 200
    assert repetida.json() == cuerpo


def test_cu008_cierre_diferido_corrige_un_cierre_por_expiracion(client, db, entorno, reloj):
    reloj.fijar(ahora_utc())
    cabecera_a = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera_a)
    reloj.avanzar(hours=9)
    cabecera_b = cabecera_de(db, entorno.docente)
    sesion_a = db.get(Sesion, uuid.UUID(id_sesion_de(cabecera_a)))
    fin = sesion_a.inicio + timedelta(hours=2)

    respuesta = cerrar(client, cabecera_b, sesion_a.id_sesion, iso(fin))

    assert respuesta.status_code == 200
    assert instante(respuesta.json()["sesion"]["fin"]) == fin
    assert respuesta.json()["actividad"]["id"] == actividad["id"]
    assert instante(respuesta.json()["actividad"]["fin"]) == fin


@pytest.mark.parametrize(
    "caso, esperado",
    [("propia", "sesion_actual"), ("inexistente", "id_sesion_invalido"), ("antes", "fin_invalido"), ("futuro", "fin_invalido")],
)
def test_cu008_cierre_diferido_rechaza_datos_invalidos(client, db, dos_sesiones, caso, esperado):
    sesion_a = dos_sesiones["a"]
    id_sesion = {"propia": id_sesion_de(dos_sesiones["b"]), "inexistente": uuid.uuid4()}.get(caso, sesion_a.id_sesion)
    fin = {"antes": sesion_a.inicio - timedelta(seconds=1), "futuro": ahora_utc() + timedelta(minutes=5)}.get(
        caso, ahora_utc()
    )

    respuesta = cerrar(client, dos_sesiones["b"], id_sesion, iso(fin))

    assert respuesta.status_code == 422
    assert motivo(respuesta) == esperado


def test_cu008_cierre_diferido_exige_fin_con_zona(client, dos_sesiones):
    respuesta = cerrar(client, dos_sesiones["b"], dos_sesiones["a"].id_sesion, "2026-01-01T10:00:00")

    assert motivo(respuesta) == "validacion"


def test_cu008_cierre_diferido_de_una_sesion_invalidada_no_cambia_nada(client, db, entorno, cabeceras):
    id_sesion_a = id_sesion_de(cabecera_de(db, entorno.docente))
    client.patch(f"/usuarios/{entorno.docente.id_usuario}/desactivar", headers=cabeceras["Supervisor"])
    client.patch(f"/usuarios/{entorno.docente.id_usuario}/activar", headers=cabeceras["Supervisor"])
    cabecera_b = cabecera_de(db, releer(db, type(entorno.docente), entorno.docente.id_usuario))

    respuesta = cerrar(client, cabecera_b, id_sesion_a, iso(ahora_utc()))

    assert respuesta.status_code == 200
    assert respuesta.json()["sesion"]["tipo_cierre"] == "Invalidada por desactivación"
    assert respuesta.json()["actividad"] is None
