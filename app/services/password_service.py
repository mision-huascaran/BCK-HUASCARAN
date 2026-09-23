from datetime import date, datetime, timedelta

from sqlmodel import Session

from app.core.email import enviar_correo_codigo_verificacion, enviar_correo_confirmacion_cambio
from app.core.security import generar_codigo_verificacion, hash_password, verify_password
from app.models.organizacion import Usuario
from app.schemas.password import CambiarContraseñaRequest


class CodigoInvalidoOExpirado(Exception):
    """El código no coincide, ya se usó, o expiró."""


class ContraseñaIgualALaActual(Exception):
    """La contraseña nueva es igual a la que ya tenía el usuario."""


class EnvioDeCodigoFallido(Exception):
    """El correo con el código de verificación no se pudo enviar."""


def solicitar_codigo(db: Session, usuario_actual: Usuario) -> None:
    codigo = generar_codigo_verificacion()
    usuario_actual.codigo_verificacion = codigo
    usuario_actual.codigo_verificacion_expira = datetime.utcnow() + timedelta(minutes=10)
    db.add(usuario_actual)
    db.commit()

    enviado = enviar_correo_codigo_verificacion(usuario_actual.correo, codigo, usuario_actual.nombres)
    if not enviado:
        raise EnvioDeCodigoFallido()


def verificar_codigo(usuario_actual: Usuario, codigo: str) -> bool:
    """Solo lectura — no consume el código. Para feedback de UX, nada más."""
    if usuario_actual.codigo_verificacion is None:
        return False
    if usuario_actual.codigo_verificacion_expira < datetime.utcnow():
        return False
    return usuario_actual.codigo_verificacion == codigo


def cambiar_contraseña(db: Session, usuario_actual: Usuario, data: CambiarContraseñaRequest) -> None:
    if not verificar_codigo(usuario_actual, data.codigo):
        raise CodigoInvalidoOExpirado()

    if verify_password(data.contraseña_nueva, usuario_actual.password_hash):
        raise ContraseñaIgualALaActual()

    usuario_actual.password_hash = hash_password(data.contraseña_nueva)
    usuario_actual.codigo_verificacion = None
    usuario_actual.codigo_verificacion_expira = None
    usuario_actual.modificado_por = usuario_actual.id_usuario
    usuario_actual.modificado_en = date.today()
    db.add(usuario_actual)
    db.commit()

    enviar_correo_confirmacion_cambio(usuario_actual.correo, usuario_actual.nombres)
