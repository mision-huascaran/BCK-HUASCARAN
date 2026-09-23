import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings


def enviar_correo_bienvenida_profesor(
    correo_destino: str, contraseña_temporal: str, nombres: str
) -> bool:
    """Intenta enviar el correo de bienvenida con la contraseña temporal.
    Devuelve False (sin lanzar excepción) si algo falla — el llamador decide el fallback."""
    texto_plano = f"""Hola {nombres},

Tu cuenta en SICEDU (Sistema de Centralización de Datos Educativos) fue creada.

Correo: {correo_destino}
Contraseña temporal: {contraseña_temporal}

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
    <p style="margin: 4px 0;"><strong>Contraseña temporal:</strong> {contraseña_temporal}</p>
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

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(settings.GMAIL_SMTP_USER, settings.GMAIL_SMTP_APP_PASSWORD)
            servidor.send_message(mensaje)
        return True
    except Exception:
        return False
