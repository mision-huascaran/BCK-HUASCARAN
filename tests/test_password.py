"""Recuperación y cambio de contraseña con PIN (CU004 a CU006) y Recovery Keys (CU001)."""
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import RecoveryKey, Sesion
from app.services.password_service import enviar_pin, solicitar_pin_publico
from tests.conftest import CORREOS, LLAVES, PASSWORD, cabecera_de, motivo, releer

NUEVA = "Nueva-Clave-9"
DOCENTE = CORREOS["Docente"]
ORIGINAL = CORREOS["Original"]


def pedir_pin(client, correo=DOCENTE):
    return client.post("/password/recuperar", json={"correo": correo})


def restablecer(client, codigo, nueva=NUEVA, confirmacion=None, correo=DOCENTE):
    return client.post(
        "/password/restablecer",
        json={
            "correo": correo,
            "codigo": codigo,
            "contraseña_nueva": nueva,
            "confirmar_contraseña_nueva": confirmacion if confirmacion is not None else nueva,
        },
    )


def otro_pin(pin: str) -> str:
    return f"{(int(pin) + 1) % 1_000_000:06d}"


# ── CU005: pedir el PIN ─────────────────────────────────────────────────────────────

def test_cu005_recuperar_envia_un_pin_de_6_digitos(client, entorno, correos):
    respuesta = pedir_pin(client)

    assert respuesta.status_code == 200
    assert respuesta.json() == {"mensaje": "Si el correo está registrado y activo, recibirá un PIN."}
    pin = correos.pin(DOCENTE)
    assert len(pin) == 6
    assert pin.isdigit()


@pytest.mark.parametrize("caso", ["inexistente", "desactivada"])
def test_cu005_recuperar_responde_igual_sin_enviar_nada(client, db, entorno, correos, caso):
    correo = "nadie@sicedu.test" if caso == "inexistente" else DOCENTE
    if caso == "desactivada":
        entorno.docente.activo = False
        db.add(entorno.docente)
        db.commit()

    respuesta = pedir_pin(client, correo)

    assert respuesta.json()["mensaje"] == "Si el correo está registrado y activo, recibirá un PIN."
    assert correos == []


def test_cu005_limite_de_5_solicitudes_por_hora(client, entorno, correos, reloj):
    reloj.fijar(ahora_utc())
    for _ in range(6):
        assert pedir_pin(client).status_code == 200
    assert len(correos.para(DOCENTE)) == 5

    reloj.avanzar(hours=1, seconds=1)
    pedir_pin(client)
    assert len(correos.para(DOCENTE)) == 6


def test_cu005_si_el_correo_no_sale_el_pin_queda_invalidado(client, db, entorno, correos):
    correos.fallar = True

    pedir_pin(client)

    assert releer(db, Usuario, entorno.docente.id_usuario).codigo_verificacion is None


def test_cu005_un_pin_no_enviado_no_pisa_uno_nuevo(motor, db, entorno, correos):
    """Si el correo falla después de que se generó otro PIN, el nuevo queda intacto."""
    envio = solicitar_pin_publico(db, DOCENTE)
    solicitar_pin_publico(db, DOCENTE)
    correos.fallar = True

    enviar_pin(lambda: Session(motor), envio)

    assert releer(db, Usuario, entorno.docente.id_usuario).codigo_verificacion is not None


# ── CU006: verificar el PIN ─────────────────────────────────────────────────────────

def test_cu006_verificar_el_pin_recibido(client, entorno, correos):
    pedir_pin(client)

    respuesta = client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": correos.pin(DOCENTE)})

    assert respuesta.status_code == 200
    assert respuesta.json() == {"valido": True}


def test_cu006_pin_incorrecto_suma_un_intento(client, db, entorno, correos):
    pedir_pin(client)

    respuesta = client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": otro_pin(correos.pin(DOCENTE))})

    assert respuesta.status_code == 400
    assert respuesta.json() == {
        "detail": "El código ingresado es incorrecto. Inténtelo nuevamente.",
        "motivo": "incorrecto",
    }
    assert releer(db, Usuario, entorno.docente.id_usuario).codigo_verificacion_intentos == 1


@pytest.mark.parametrize("correo", ["nadie@sicedu.test", DOCENTE], ids=["sin_cuenta", "sin_pin"])
def test_cu006_sin_pin_vigente_responde_incorrecto(client, entorno, correo):
    respuesta = client.post("/password/verificar-codigo", json={"correo": correo, "codigo": "123456"})

    assert motivo(respuesta) == "incorrecto"


def test_cu006_pin_vencido_a_los_15_minutos(client, entorno, correos, reloj):
    reloj.fijar(ahora_utc())
    pedir_pin(client)
    reloj.avanzar(minutes=15, seconds=1)

    respuesta = client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": correos.pin(DOCENTE)})

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "El código ha expirado. Solicite un nuevo PIN."
    assert motivo(respuesta) == "expirado"


def test_cu006_el_quinto_fallo_agota_el_pin(client, entorno, correos):
    pedir_pin(client)
    correcto = correos.pin(DOCENTE)
    motivos = [
        motivo(client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": otro_pin(correcto)}))
        for _ in range(5)
    ]

    assert motivos == ["incorrecto"] * 4 + ["intentos_agotados"]
    despues = client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": correcto})
    assert motivo(despues) == "incorrecto"


def test_cu006_con_el_maximo_de_intentos_ya_guardado_se_agota(client, db, entorno, correos):
    pedir_pin(client)
    usuario = releer(db, Usuario, entorno.docente.id_usuario)
    usuario.codigo_verificacion_intentos = 5
    db.add(usuario)
    db.commit()

    respuesta = client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": correos.pin(DOCENTE)})

    assert motivo(respuesta) == "intentos_agotados"


def test_cu005_un_pin_nuevo_invalida_el_anterior(client, entorno, correos):
    pedir_pin(client)
    viejo = correos.pin(DOCENTE)
    pedir_pin(client)
    nuevo = correos.pin(DOCENTE)

    if viejo != nuevo:
        assert motivo(client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": viejo})) == "incorrecto"
    assert client.post("/password/verificar-codigo", json={"correo": DOCENTE, "codigo": nuevo}).status_code == 200


# ── CU006: restablecer ──────────────────────────────────────────────────────────────

def test_cu006_restablecer_cambia_la_contrasena_e_invalida_las_sesiones(client, db, entorno, correos):
    cabecera = cabecera_de(db, entorno.docente)
    pedir_pin(client)

    respuesta = restablecer(client, correos.pin(DOCENTE))

    assert respuesta.status_code == 200
    assert respuesta.json() == {"mensaje": "Contraseña actualizada correctamente."}
    assert motivo(client.get("/me", headers=cabecera)) == "sesion_invalida"
    db.expire_all()
    assert db.exec(select(Sesion.tipo_cierre)).one() == "Invalidada por restablecimiento de contraseña"
    assert client.post("/login", json={"correo": DOCENTE, "password": NUEVA}).status_code == 200
    assert client.post("/login", json={"correo": DOCENTE, "password": PASSWORD}).status_code == 401
    assert correos[-1]["Subject"] == "Tu contraseña de SICEDU fue cambiada"


def test_cu006_el_pin_es_de_un_solo_uso(client, entorno, correos):
    pedir_pin(client)
    pin = correos.pin(DOCENTE)
    restablecer(client, pin)

    assert motivo(restablecer(client, pin, nueva="Otra-Clave-77")) == "incorrecto"


def test_cu006_contrasenas_que_no_coinciden(client, entorno, correos):
    pedir_pin(client)

    respuesta = restablecer(client, correos.pin(DOCENTE), confirmacion="Distinta-1")

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert cuerpo["motivo"] == "validacion"
    assert cuerpo["errores"] == [{"campo": "confirmar_contraseña_nueva", "mensaje": "Las contraseñas no coinciden."}]


@pytest.mark.parametrize(
    "nueva, incumplidos",
    [
        ("Ab1-", ["longitud_minima"]),
        ("clave-larga-1", ["mayuscula"]),
        ("CLAVE-LARGA-1", ["minuscula"]),
        ("Clave-Larga", ["numero"]),
        ("ClaveLarga1", ["caracter_especial"]),
        ("abc", ["longitud_minima", "mayuscula", "numero", "caracter_especial"]),
    ],
)
def test_cu006_politica_de_contrasena(client, db, entorno, correos, nueva, incumplidos):
    pedir_pin(client)

    respuesta = restablecer(client, correos.pin(DOCENTE), nueva=nueva)

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert cuerpo["motivo"] == "validacion"
    assert cuerpo["requisitos_incumplidos"] == incumplidos
    assert [e["campo"] for e in cuerpo["errores"]] == ["contraseña_nueva"] * len(incumplidos)
    assert releer(db, Usuario, entorno.docente.id_usuario).codigo_verificacion_intentos == 0


def test_cu006_la_nueva_debe_ser_distinta_de_la_actual(client, entorno, correos):
    pedir_pin(client)

    respuesta = restablecer(client, correos.pin(DOCENTE), nueva=PASSWORD)

    assert respuesta.status_code == 400
    assert respuesta.json() == {
        "detail": "La nueva contraseña debe ser diferente de la contraseña anterior.",
        "motivo": "password_igual",
    }


def test_cu006_restablecer_con_un_correo_que_no_existe(client, entorno):
    assert motivo(restablecer(client, "123456", correo="nadie@sicedu.test")) == "incorrecto"


def test_cu006_error_de_bd_al_guardar_da_500_sin_cambios(client, db, entorno, correos, monkeypatch):
    pedir_pin(client)
    pin = correos.pin(DOCENTE)

    def commit_que_falla(self):
        raise OperationalError("UPDATE usuario", {}, Exception("caída simulada"))

    monkeypatch.setattr(Session, "commit", commit_que_falla)
    respuesta = restablecer(client, pin)
    monkeypatch.undo()

    assert respuesta.status_code == 500
    assert motivo(respuesta) == "error_actualizacion"
    assert client.post("/login", json={"correo": DOCENTE, "password": PASSWORD}).status_code == 200


# ── Cambio con sesión iniciada (/me/password) ───────────────────────────────────────

def test_me_password_flujo_completo_conserva_la_sesion_actual(client, db, entorno, cabeceras, correos):
    otra = cabecera_de(db, entorno.docente)
    cabecera = cabeceras["Docente"]

    assert client.post("/me/password/codigo", headers=cabecera).json() == {"mensaje": "Se envió un PIN a su correo."}
    pin = correos.pin(DOCENTE)
    assert client.post("/me/password/verificar-codigo", json={"codigo": pin}, headers=cabecera).json() == {"valido": True}
    respuesta = client.post(
        "/me/password",
        json={"codigo": pin, "contraseña_nueva": NUEVA, "confirmar_contraseña_nueva": NUEVA},
        headers=cabecera,
    )

    assert respuesta.status_code == 200
    assert client.get("/me", headers=cabecera).status_code == 200
    assert motivo(client.get("/me", headers=otra)) == "sesion_invalida"


@pytest.mark.parametrize(
    "nueva, confirmacion, esperado",
    [("Ab1-", "Ab1-", 422), (NUEVA, "Otra-Clave-1", 422), (PASSWORD, PASSWORD, 400)],
    ids=["politica", "no_coinciden", "igual"],
)
def test_me_password_aplica_las_mismas_reglas(client, entorno, cabeceras, correos, nueva, confirmacion, esperado):
    cabecera = cabeceras["Supervisor"]
    client.post("/me/password/codigo", headers=cabecera)

    respuesta = client.post(
        "/me/password",
        json={"codigo": correos.pin(CORREOS["Supervisor"]), "contraseña_nueva": nueva, "confirmar_contraseña_nueva": confirmacion},
        headers=cabecera,
    )

    assert respuesta.status_code == esperado


def test_me_password_codigo_con_correo_caido_da_503(client, entorno, cabeceras, correos):
    correos.fallar = True

    respuesta = client.post("/me/password/codigo", headers=cabeceras["Directivo"])

    assert respuesta.status_code == 503
    assert motivo(respuesta) == "envio_fallido"


def test_me_password_codigo_limite_por_hora_da_429(client, entorno, cabeceras):
    codigos = [client.post("/me/password/codigo", headers=cabeceras["Docente"]).status_code for _ in range(6)]

    assert codigos == [200] * 5 + [429]


def test_me_password_requiere_sesion(client, entorno):
    assert client.post("/me/password/codigo").status_code == 401
    assert client.post("/me/password/verificar-codigo", json={"codigo": "123456"}).status_code == 401


def test_me_password_codigo_incorrecto(client, entorno, cabeceras, correos):
    client.post("/me/password/codigo", headers=cabeceras["Docente"])

    respuesta = client.post(
        "/me/password/verificar-codigo", json={"codigo": otro_pin(correos.pin(DOCENTE))}, headers=cabeceras["Docente"]
    )

    assert motivo(respuesta) == "incorrecto"


# ── CU001: Recovery Keys del Supervisor original ────────────────────────────────────

def recuperar(client, llave, correo=ORIGINAL, nueva=NUEVA, confirmacion=None):
    return client.post(
        "/password/recuperar-con-llave",
        json={
            "correo": correo,
            "llave": llave,
            "contraseña_nueva": nueva,
            "confirmar_contraseña_nueva": confirmacion if confirmacion is not None else nueva,
        },
    )


def test_cu001_llave_valida_recupera_la_cuenta_y_se_consume(client, db, entorno):
    respuesta = recuperar(client, LLAVES[0].lower().replace("-", " "))

    assert respuesta.status_code == 200
    assert respuesta.json() == {"mensaje": "Contraseña actualizada correctamente.", "llaves_restantes": 9}
    assert client.post("/login", json={"correo": ORIGINAL, "password": NUEVA}).status_code == 200
    usada = recuperar(client, LLAVES[0], nueva="Otra-Clave-77")
    assert usada.status_code == 400
    assert motivo(usada) == "llave_invalida"
    db.expire_all()
    assert len(db.exec(select(RecoveryKey).where(RecoveryKey.usada_en.is_not(None))).all()) == 1


@pytest.mark.parametrize("correo", [ORIGINAL, CORREOS["Supervisor"], "nadie@sicedu.test"])
def test_cu001_llave_invalida_da_400_generico(client, entorno, correo):
    respuesta = recuperar(client, LLAVES[1] if correo != ORIGINAL else "AAAA-BBBB", correo=correo)

    assert respuesta.status_code == 400
    assert respuesta.json() == {"detail": "Correo o llave inválidos.", "motivo": "llave_invalida"}


def test_cu001_el_quinto_fallo_bloquea_y_comparte_contador_con_el_login(client, entorno):
    for _ in range(4):
        client.post("/login", json={"correo": ORIGINAL, "password": "Incorrecta-1"})

    respuesta = recuperar(client, "llave-equivocada")

    assert respuesta.status_code == 429
    assert motivo(respuesta) == "bloqueo_temporal"
    assert motivo(recuperar(client, LLAVES[2])) == "bloqueo_temporal"


def test_cu001_contrasena_invalida_no_consume_la_llave(client, db, entorno):
    respuesta = recuperar(client, LLAVES[3], nueva="corta")

    assert respuesta.status_code == 422
    db.expire_all()
    assert db.exec(select(RecoveryKey).where(RecoveryKey.usada_en.is_not(None))).all() == []


def test_cu001_recuperar_invalida_las_sesiones(client, db, entorno):
    cabecera = cabecera_de(db, entorno.usuarios["Original"])

    recuperar(client, LLAVES[4])

    assert motivo(client.get("/me", headers=cabecera)) == "sesion_invalida"
    assert ahora_utc() - timedelta(minutes=1) < releer(db, Usuario, entorno.usuarios["Original"].id_usuario).modificado_en
