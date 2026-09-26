"""Pruebas de app/core/email.py.

Las tres funciones prometen lo mismo: intentar el envío y devolver True o False sin
lanzar nunca una excepción, porque quien las llama ya decide el plan B (devolver la
contraseña temporal en la respuesta, avisar con un 503, o no hacer nada).

El SMTP real está sustituido por la fixture `correos`, que es autouse: ninguna prueba
de esta suite sale a la red.
"""
from app.core.config import settings
from app.core.email import (
    enviar_correo_bienvenida_profesor,
    enviar_correo_codigo_verificacion,
    enviar_correo_confirmacion_cambio,
)
from tests.conftest import texto_del_correo


def test_correo_de_bienvenida(correos):
    enviado = enviar_correo_bienvenida_profesor("pedro@sicedu.test", "Temporal123", "Pedro")

    assert enviado is True
    mensaje = correos[0]
    assert mensaje["Subject"] == "Bienvenido a SICEDU - Tu cuenta ha sido creada"
    assert mensaje["To"] == "pedro@sicedu.test"
    assert mensaje["From"] == settings.GMAIL_SMTP_USER
    cuerpo = texto_del_correo(mensaje)
    assert "Pedro" in cuerpo
    assert "Temporal123" in cuerpo


def test_correo_del_codigo_de_verificacion(correos):
    enviado = enviar_correo_codigo_verificacion("rosa@sicedu.test", "AB12CD", "Rosa")

    assert enviado is True
    assert correos[0]["Subject"] == "Tu código de verificación de SICEDU"
    assert "AB12CD" in texto_del_correo(correos[0])


def test_correo_de_confirmacion_del_cambio(correos):
    enviado = enviar_correo_confirmacion_cambio("rosa@sicedu.test", "Rosa")

    assert enviado is True
    assert correos[0]["Subject"] == "Tu contraseña de SICEDU fue cambiada"
    assert "contacta a tu supervisor" in texto_del_correo(correos[0])


def test_todos_los_correos_llevan_version_html(correos):
    enviar_correo_bienvenida_profesor("pedro@sicedu.test", "Temporal123", "Pedro")

    tipos = [parte.get_content_type() for parte in correos[0].walk()]
    assert "text/plain" in tipos
    assert "text/html" in tipos


def test_se_conecta_al_smtp_de_gmail_con_las_credenciales_configuradas(correos):
    enviar_correo_confirmacion_cambio("rosa@sicedu.test", "Rosa")

    assert correos.servidor == ("smtp.gmail.com", 465)
    assert correos.credenciales == (settings.GMAIL_SMTP_USER, settings.GMAIL_SMTP_APP_PASSWORD)


def test_si_el_smtp_falla_devuelven_false_sin_reventar(correos):
    """Un correo caído no puede tumbar el alta de un profesor ni el cambio de clave."""
    correos.fallar = True

    assert enviar_correo_bienvenida_profesor("pedro@sicedu.test", "Temporal123", "Pedro") is False
    assert enviar_correo_codigo_verificacion("rosa@sicedu.test", "AB12CD", "Rosa") is False
    assert enviar_correo_confirmacion_cambio("rosa@sicedu.test", "Rosa") is False
    assert correos == []
