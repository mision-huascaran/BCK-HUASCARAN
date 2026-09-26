from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.core.email import enviar_correo_codigo_verificacion, enviar_correo_confirmacion_cambio
from app.core.security import generar_codigo_verificacion, hash_password, verify_password
from app.models.organizacion import Usuario
from app.schemas.password import CambiarContraseñaRequest, RestablecerContraseñaRequest


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


def solicitar_codigo_publico(db: Session, correo: str) -> None:
    """Envia un codigo al correo indicado, sin sesion iniciada.

    No lanza excepcion si el correo no existe o la cuenta esta inactiva: el endpoint
    responde 200 siempre, para que nadie pueda averiguar que correos estan registrados
    probandolos uno por uno.
    """
    usuario = db.exec(select(Usuario).where(Usuario.correo == correo)).first()
    if usuario is None or not usuario.activo:
        return

    codigo = generar_codigo_verificacion()
    usuario.codigo_verificacion = codigo
    usuario.codigo_verificacion_expira = datetime.utcnow() + timedelta(minutes=10)
    db.add(usuario)
    db.commit()

    enviar_correo_codigo_verificacion(usuario.correo, codigo, usuario.nombres)


def restablecer_contraseña(db: Session, data: RestablecerContraseñaRequest) -> None:
    """Cierra el flujo publico de recuperacion.

    Si el correo no existe se levanta CodigoInvalidoOExpirado, el mismo error que un
    codigo equivocado: asi la respuesta no distingue entre 'ese correo no existe' y
    'ese codigo esta mal'.
    """
    usuario = db.exec(select(Usuario).where(Usuario.correo == data.correo)).first()
    if usuario is None or not usuario.activo:
        raise CodigoInvalidoOExpirado()

    if not verificar_codigo(usuario, data.codigo):
        raise CodigoInvalidoOExpirado()

    if verify_password(data.contraseña_nueva, usuario.password_hash):
        raise ContraseñaIgualALaActual()

    usuario.password_hash = hash_password(data.contraseña_nueva)
    usuario.codigo_verificacion = None
    usuario.codigo_verificacion_expira = None
    usuario.modificado_por = usuario.id_usuario
    usuario.modificado_en = date.today()
    db.add(usuario)
    db.commit()

    enviar_correo_confirmacion_cambio(usuario.correo, usuario.nombres)
