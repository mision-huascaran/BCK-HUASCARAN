import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings
from app.core.pin import VIGENCIA_PIN

SMTP_HOST = "smtp.gmail.com"
SMTP_PUERTO = 465
# Segundos máximos de espera por Gmail (conexión y cada operación): los envíos síncronos
# (POST /usuarios, POST /me/password/codigo) no deben dejar la petición colgada.
SMTP_TIMEOUT = 10
MINUTOS_VIGENCIA_PIN = int(VIGENCIA_PIN.total_seconds() // 60)

logger = logging.getLogger(__name__)


def enmascarar_correo(correo: str) -> str:
    """`rosa.c@mh.org` -> `ro***@mh.org`. Para los logs: identifica el caso sin exponer
    la dirección completa."""
    usuario, arroba, dominio = correo.partition("@")
    if not arroba:
        return "***"
    return f"{usuario[:2]}***@{dominio}"


def _enviar(mensaje: MIMEMultipart, tipo: str) -> bool:
    """Envía el mensaje por el SMTP de Gmail. Devuelve False (sin lanzar) si algo falla.

    El error queda en el log con su traza, pero sin el cuerpo del mensaje (que puede
    llevar un PIN o una contraseña temporal) y con el destinatario enmascarado.
    """
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PUERTO, timeout=SMTP_TIMEOUT) as servidor:
            servidor.login(settings.GMAIL_SMTP_USER, settings.GMAIL_SMTP_APP_PASSWORD)
            servidor.send_message(mensaje)
        return True
    except Exception:
        logger.exception(
            "No se pudo enviar el correo de %s a %s", tipo, enmascarar_correo(mensaje["To"] or "")
        )
        return False


def enviar_correo_bienvenida_profesor(
    correo_destino: str, contrasena_temporal: str, nombres: str
) -> bool:
    """Intenta enviar el correo de bienvenida con la contraseña temporal.
    Devuelve False (sin lanzar excepción) si algo falla — el llamador decide el fallback."""
    texto_plano = f"""Hola {nombres},

Tu cuenta en SICEDU (Sistema de Centralización de Datos Educativos) fue creada.

Correo: {correo_destino}
Contraseña temporal: {contrasena_temporal}

Por seguridad, cambia esta contraseña la primera vez que inicies sesión.

Equipo SICEDU - Misión Huascarán
"""

    html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1f2937;">
  <h2 style="color: #1e3a5f; margin-bottom: 4px;">SICEDU</h2>
  <p style="color: #6b7280; font-size: 13px; margin-top: 0;">Misión Huascarán</p>

  <p>Hola {nombres},</p>
  <p>Tu cuenta en SICEDU (Sistema de Centralización de Datos Educativos) fue creada.</p>

  <div style="background-color: #f3f4f6; border-radius: 6px; padding: 16px; margin: 16px 0;">
    <p style="margin: 4px 0;"><strong>Correo:</strong> {correo_destino}</p>
    <p style="margin: 4px 0;"><strong>Contraseña temporal:</strong> {contrasena_temporal}</p>
  </div>

  <p style="font-size: 13px; color: #6b7280;">
    Por seguridad, cambia esta contraseña la primera vez que inicies sesión.
  </p>

  <p style="margin-top: 32px; font-size: 13px; color: #6b7280;">
    Equipo SICEDU — Misión Huascarán
  </p>
</div>
"""

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = "Bienvenido a SICEDU - Tu cuenta ha sido creada"
    mensaje["From"] = settings.GMAIL_SMTP_USER
    mensaje["To"] = correo_destino
    mensaje.attach(MIMEText(texto_plano, "plain"))
    mensaje.attach(MIMEText(html, "html"))

    return _enviar(mensaje, "bienvenida")


def enviar_correo_codigo_verificacion(correo_destino: str, codigo: str, nombres: str) -> bool:
    """Intenta enviar el código de verificación para cambiar la contraseña.
    Devuelve False (sin lanzar excepción) si algo falla — el llamador decide qué hacer."""
    texto_plano = f"""Hola {nombres},

Recibimos una solicitud para cambiar la contraseña de tu cuenta en SICEDU.

Tu código de verificación es: {codigo}

Este código expira en {MINUTOS_VIGENCIA_PIN} minutos.

Si no solicitaste este cambio, ignora este correo.

Equipo SICEDU - Misión Huascarán
"""

    html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1f2937;">
  <h2 style="color: #1e3a5f; margin-bottom: 4px;">SICEDU</h2>
  <p style="color: #6b7280; font-size: 13px; margin-top: 0;">Misión Huascarán</p>

  <p>Hola {nombres},</p>
  <p>Recibimos una solicitud para cambiar la contraseña de tu cuenta en SICEDU.</p>

  <div style="background-color: #f3f4f6; border-radius: 6px; padding: 16px; margin: 16px 0; text-align: center;">
    <p style="margin: 0 0 4px 0; font-size: 13px; color: #6b7280;">Tu código de verificación</p>
    <p style="margin: 0; font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #1e3a5f;">{codigo}</p>
  </div>

  <p style="font-size: 13px; color: #6b7280;">
    Este código expira en {MINUTOS_VIGENCIA_PIN} minutos. Si no solicitaste este cambio, ignora este correo.
  </p>

  <p style="margin-top: 32px; font-size: 13px; color: #6b7280;">
    Equipo SICEDU — Misión Huascarán
  </p>
</div>
"""

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = "Tu código de verificación de SICEDU"
    mensaje["From"] = settings.GMAIL_SMTP_USER
    mensaje["To"] = correo_destino
    mensaje.attach(MIMEText(texto_plano, "plain"))
    mensaje.attach(MIMEText(html, "html"))

    return _enviar(mensaje, "código de verificación")


def enviar_correo_confirmacion_cambio(correo_destino: str, nombres: str) -> bool:
    """Intenta enviar la confirmación de que la contraseña fue cambiada.
    Devuelve False (sin lanzar excepción) si algo falla — es un aviso best-effort,
    la contraseña ya se cambió y esto no revierte nada."""
    texto_plano = f"""Hola {nombres},

Tu contraseña en SICEDU fue cambiada exitosamente.

Si no realizaste este cambio, contacta a tu supervisor.

Equipo SICEDU - Misión Huascarán
"""

    html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1f2937;">
  <h2 style="color: #1e3a5f; margin-bottom: 4px;">SICEDU</h2>
  <p style="color: #6b7280; font-size: 13px; margin-top: 0;">Misión Huascarán</p>

  <p>Hola {nombres},</p>
  <p>Tu contraseña en SICEDU fue cambiada exitosamente.</p>

  <p style="font-size: 13px; color: #6b7280;">
    Si no realizaste este cambio, contacta a tu supervisor.
  </p>

  <p style="margin-top: 32px; font-size: 13px; color: #6b7280;">
    Equipo SICEDU — Misión Huascarán
  </p>
</div>
"""

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = "Tu contraseña de SICEDU fue cambiada"
    mensaje["From"] = settings.GMAIL_SMTP_USER
    mensaje["To"] = correo_destino
    mensaje.attach(MIMEText(texto_plano, "plain"))
    mensaje.attach(MIMEText(html, "html"))

    return _enviar(mensaje, "confirmación de cambio de contraseña")
