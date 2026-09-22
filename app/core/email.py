import smtplib
from email.mime.text import MIMEText

from app.core.config import settings


def enviar_correo_bienvenida_profesor(correo_destino: str, contraseña_temporal: str) -> bool:
    """Intenta enviar el correo de bienvenida con la contraseña temporal.
    Devuelve False (sin lanzar excepción) si algo falla — el llamador decide el fallback."""
    cuerpo = (
        f"Tu cuenta en SICEDU fue creada. Correo: {correo_destino}. "
        f"Contraseña temporal: {contraseña_temporal}. "
        "Cámbiala en tu primer inicio de sesión."
    )
    mensaje = MIMEText(cuerpo, "plain")
    mensaje["Subject"] = "Bienvenido a SICEDU"
    mensaje["From"] = settings.GMAIL_SMTP_USER
    mensaje["To"] = correo_destino

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(settings.GMAIL_SMTP_USER, settings.GMAIL_SMTP_APP_PASSWORD)
            servidor.send_message(mensaje)
        return True
    except Exception:
        return False
