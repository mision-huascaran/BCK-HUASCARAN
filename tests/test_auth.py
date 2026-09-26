"""Pruebas de la autenticación (HU14): POST /login, GET /me y POST /logout."""
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from tests.conftest import PASSWORD

MENSAJE_LOGIN = "Correo o contraseña incorrectos"
MENSAJE_SESION = "Credenciales inválidas o sesión expirada"
MENSAJE_INACTIVA = "Tu cuenta está deshabilitada. Contacta a tu supervisor."


def test_raiz_responde(client):
    respuesta = client.get("/")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


def test_login_correcto_devuelve_token_con_datos_del_usuario(client, usuarios):
    respuesta = client.post("/login", json={"correo": "activo@prueba.test", "password": PASSWORD})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["token_type"] == "bearer"
    payload = decode_access_token(cuerpo["access_token"])
    assert payload["id_usuario"] == usuarios["activo"].id_usuario
    assert payload["correo"] == "activo@prueba.test"
    assert "exp" in payload


def test_login_con_contrasena_incorrecta(client, usuarios):
    respuesta = client.post("/login", json={"correo": "activo@prueba.test", "password": "OtraClave999"})
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == MENSAJE_LOGIN


def test_login_con_correo_inexistente_da_el_mismo_mensaje(client, usuarios):
    respuesta = client.post("/login", json={"correo": "nadie@prueba.test", "password": PASSWORD})
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == MENSAJE_LOGIN


def test_login_de_usuario_inactivo_avisa_que_la_cuenta_esta_deshabilitada(client, usuarios):
    respuesta = client.post("/login", json={"correo": "inactivo@prueba.test", "password": PASSWORD})
    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == MENSAJE_INACTIVA


def test_login_sin_contrasena_es_error_de_validacion(client):
    respuesta = client.post("/login", json={"correo": "activo@prueba.test"})
    assert respuesta.status_code == 422
    assert isinstance(respuesta.json()["detail"], list)


def test_me_con_token_devuelve_el_perfil(client, token):
    respuesta = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    perfil = respuesta.json()
    assert perfil["correo"] == "activo@prueba.test"
    assert perfil["nombres"] == "Ana"
    assert perfil["id_docente"] is None
    assert perfil["activo"] is True
    assert "password_hash" not in perfil


def test_me_sin_token(client):
    respuesta = client.get("/me")
    assert respuesta.status_code == 401


def test_me_con_token_mal_formado(client):
    respuesta = client.get("/me", headers={"Authorization": "Bearer no-es-un-jwt"})
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == MENSAJE_SESION


def test_me_con_token_expirado(client, usuarios):
    vencido = jwt.encode(
        {"id_usuario": usuarios["activo"].id_usuario, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    respuesta = client.get("/me", headers={"Authorization": f"Bearer {vencido}"})
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == MENSAJE_SESION


def test_me_con_token_firmado_con_otra_clave(client, usuarios):
    ajeno = jwt.encode(
        {"id_usuario": usuarios["activo"].id_usuario, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "otra-clave",
        algorithm=settings.JWT_ALGORITHM,
    )
    respuesta = client.get("/me", headers={"Authorization": f"Bearer {ajeno}"})
    assert respuesta.status_code == 401


def test_me_con_token_sin_id_de_usuario(client):
    sin_id = create_access_token({"correo": "activo@prueba.test"})
    respuesta = client.get("/me", headers={"Authorization": f"Bearer {sin_id}"})
    assert respuesta.status_code == 401


def test_me_rechaza_usuario_desactivado_despues_del_login(client, session, usuarios, token):
    usuarios["activo"].activo = False
    session.add(usuarios["activo"])
    session.commit()

    respuesta = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 401


def test_logout(client):
    respuesta = client.post("/logout")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"detail": "Sesión cerrada correctamente"}


def test_cors_permite_un_origen_configurado(client):
    origen = settings.cors_origins_list[0]
    respuesta = client.options(
        "/login",
        headers={"Origin": origen, "Access-Control-Request-Method": "POST"},
    )
    assert respuesta.status_code == 200
    assert respuesta.headers["access-control-allow-origin"] == origen


def test_hash_de_contrasena():
    hash_ = hash_password(PASSWORD)
    assert hash_ != PASSWORD
    assert verify_password(PASSWORD, hash_)
    assert not verify_password("OtraClave999", hash_)
