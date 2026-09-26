"""Pruebas del cambio y la recuperación de contraseña.

Hay dos caminos: el del usuario que ya entró (/me/password/*) y el público, sin sesión
(/password/*), para quien olvidó su contraseña. El público responde 200 aunque el correo
no exista, a propósito: si distinguiera, serviría para averiguar qué correos tienen
cuenta probándolos uno por uno.
"""
import re
from datetime import datetime, timedelta

from sqlmodel import select

from app.models.organizacion import Usuario
from tests.conftest import CORREOS, PASSWORD, texto_del_correo

CLAVE_NUEVA = "ClaveNueva456"


def codigo_del_correo(mensaje) -> str:
    encontrado = re.search(r"código de verificación es: ([A-Z0-9]{6})", texto_del_correo(mensaje))
    assert encontrado, texto_del_correo(mensaje)
    return encontrado.group(1)


def cuerpo_cambio(codigo, nueva=CLAVE_NUEVA, confirmacion=None):
    return {
        "codigo": codigo,
        "contraseña_nueva": nueva,
        "confirmar_contraseña_nueva": nueva if confirmacion is None else confirmacion,
    }


# ── Con sesión iniciada ──────────────────────────────────────────────────────────────

def test_solicitar_codigo_lo_envia_por_correo(client, entorno, sesiones, correos):
    respuesta = client.post("/me/password/codigo", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"detail": "Código enviado a tu correo"}
    assert len(correos) == 1
    assert correos[0]["To"] == CORREOS["Supervisor"]
    assert len(codigo_del_correo(correos[0])) == 6


def test_si_el_correo_no_sale_el_codigo_no_sirve_de_nada(client, entorno, sesiones, correos):
    correos.fallar = True

    respuesta = client.post("/me/password/codigo", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 503
    assert respuesta.json()["detail"] == "No pudimos enviar el código, intenta de nuevo"


def test_verificar_el_codigo_recibido(client, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])
    codigo = codigo_del_correo(correos[0])

    respuesta = client.post(
        "/me/password/verificar-codigo", json={"codigo": codigo}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"detail": "Código correcto"}


def test_verificar_un_codigo_equivocado(client, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])

    respuesta = client.post(
        "/me/password/verificar-codigo", json={"codigo": "XXXXXX"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Código incorrecto o expirado"


def test_verificar_sin_haber_pedido_ningun_codigo(client, entorno, sesiones):
    respuesta = client.post(
        "/me/password/verificar-codigo", json={"codigo": "ABC123"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 400


def test_verificar_un_codigo_vencido(client, session, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])
    codigo = codigo_del_correo(correos[0])

    usuario = entorno["usuarios"]["Supervisor"]
    usuario.codigo_verificacion_expira = datetime.utcnow() - timedelta(minutes=1)
    session.add(usuario)
    session.commit()

    respuesta = client.post(
        "/me/password/verificar-codigo", json={"codigo": codigo}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 400


def test_cambiar_la_contrasena_con_el_codigo(client, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])
    codigo = codigo_del_correo(correos[0])

    respuesta = client.post(
        "/me/password", json=cuerpo_cambio(codigo), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"detail": "Contraseña actualizada correctamente"}
    # Aviso de que la contraseña cambió, además del correo con el código.
    assert len(correos) == 2
    assert correos[1]["Subject"] == "Tu contraseña de SICEDU fue cambiada"

    entra_con_la_nueva = client.post(
        "/login", json={"correo": CORREOS["Supervisor"], "password": CLAVE_NUEVA}
    )
    entra_con_la_vieja = client.post(
        "/login", json={"correo": CORREOS["Supervisor"], "password": PASSWORD}
    )
    assert entra_con_la_nueva.status_code == 200
    assert entra_con_la_vieja.status_code == 401


def test_el_codigo_se_consume_al_cambiar_la_contrasena(client, session, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])
    codigo = codigo_del_correo(correos[0])
    client.post("/me/password", json=cuerpo_cambio(codigo), headers=sesiones["Supervisor"])

    session.expire_all()
    respuesta = client.post(
        "/me/password",
        json=cuerpo_cambio(codigo, nueva="OtraClave789"),
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 400


def test_cambiar_con_un_codigo_equivocado(client, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])

    respuesta = client.post(
        "/me/password", json=cuerpo_cambio("XXXXXX"), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Código incorrecto o expirado"


def test_la_contrasena_nueva_no_puede_ser_la_misma(client, entorno, sesiones, correos):
    client.post("/me/password/codigo", headers=sesiones["Supervisor"])
    codigo = codigo_del_correo(correos[0])

    respuesta = client.post(
        "/me/password", json=cuerpo_cambio(codigo, nueva=PASSWORD), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "La contraseña nueva no puede ser igual a la actual"


def test_reglas_de_formato_de_la_contrasena(client, entorno, sesiones):
    cabecera = sesiones["Supervisor"]
    casos = {
        "no coinciden": cuerpo_cambio("ABC123", confirmacion="OtraCosa123"),
        "muy corta": cuerpo_cambio("ABC123", nueva="Corta1"),
        "con símbolos": cuerpo_cambio("ABC123", nueva="ClaveCon$imbolo1"),
    }
    for caso, cuerpo in casos.items():
        respuesta = client.post("/me/password", json=cuerpo, headers=cabecera)
        assert respuesta.status_code == 422, f"{caso}: {respuesta.text}"


def test_el_cambio_de_contrasena_necesita_sesion(client, entorno):
    assert client.post("/me/password/codigo").status_code == 401
    assert client.post("/me/password/verificar-codigo", json={"codigo": "X"}).status_code == 401
    assert client.post("/me/password", json=cuerpo_cambio("X")).status_code == 401


# ── Flujo público, sin sesión ────────────────────────────────────────────────────────

def test_recuperar_envia_el_codigo_al_correo_registrado(client, entorno, correos):
    respuesta = client.post("/password/recuperar", json={"correo": CORREOS["Docente"]})

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "detail": "Si el correo está registrado, enviamos un código de verificación"
    }
    assert len(correos) == 1
    assert correos[0]["To"] == CORREOS["Docente"]


def test_recuperar_responde_igual_si_el_correo_no_existe(client, entorno, correos):
    """Misma respuesta que con un correo real: así no sirve para sondear quién tiene cuenta."""
    respuesta = client.post("/password/recuperar", json={"correo": "nadie@sicedu.test"})

    assert respuesta.status_code == 200
    assert respuesta.json()["detail"].startswith("Si el correo está registrado")
    assert correos == []


def test_recuperar_no_manda_nada_a_una_cuenta_desactivada(client, session, entorno, correos):
    usuario = entorno["usuarios"]["Docente"]
    usuario.activo = False
    session.add(usuario)
    session.commit()

    respuesta = client.post("/password/recuperar", json={"correo": CORREOS["Docente"]})

    assert respuesta.status_code == 200
    assert correos == []


def test_restablecer_cierra_el_flujo_publico(client, session, entorno, correos):
    client.post("/password/recuperar", json={"correo": CORREOS["Docente"]})
    codigo = codigo_del_correo(correos[0])

    respuesta = client.post(
        "/password/restablecer",
        json={"correo": CORREOS["Docente"], **cuerpo_cambio(codigo)},
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {"detail": "Contraseña actualizada correctamente"}

    entra = client.post("/login", json={"correo": CORREOS["Docente"], "password": CLAVE_NUEVA})
    assert entra.status_code == 200

    session.expire_all()
    usuario = session.exec(select(Usuario).where(Usuario.correo == CORREOS["Docente"])).one()
    assert usuario.codigo_verificacion is None


def test_restablecer_con_un_correo_que_no_existe(client, entorno):
    """Mismo error que un código equivocado: la respuesta no distingue los dos casos."""
    respuesta = client.post(
        "/password/restablecer",
        json={"correo": "nadie@sicedu.test", **cuerpo_cambio("ABC123")},
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Código incorrecto o expirado"


def test_restablecer_con_un_codigo_equivocado(client, entorno, correos):
    client.post("/password/recuperar", json={"correo": CORREOS["Docente"]})

    respuesta = client.post(
        "/password/restablecer",
        json={"correo": CORREOS["Docente"], **cuerpo_cambio("XXXXXX")},
    )

    assert respuesta.status_code == 400


def test_restablecer_con_la_misma_contrasena(client, entorno, correos):
    client.post("/password/recuperar", json={"correo": CORREOS["Docente"]})
    codigo = codigo_del_correo(correos[0])

    respuesta = client.post(
        "/password/restablecer",
        json={"correo": CORREOS["Docente"], **cuerpo_cambio(codigo, nueva=PASSWORD)},
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "La contraseña nueva no puede ser igual a la actual"


def test_restablecer_valida_el_formato_igual_que_el_otro_camino(client, entorno):
    respuesta = client.post(
        "/password/restablecer",
        json={"correo": CORREOS["Docente"], **cuerpo_cambio("ABC123", nueva="corta")},
    )

    assert respuesta.status_code == 422
