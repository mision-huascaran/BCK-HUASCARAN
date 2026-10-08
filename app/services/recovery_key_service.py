"""Recuperación del Supervisor original con una Recovery Key (CU001).

Permite recuperar el acceso sin depender del correo. Contra la fuerza bruta usa el mismo
bloqueo que tendrá el login: 5 fallos seguidos bloquean el correo 15 minutos.
"""
from fastapi import status
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.correo import normalizar_correo
from app.core.errores import ErrorNegocio
from app.core.recovery_key import CANTIDAD_LLAVES, normalizar_llave
from app.core.security import HASH_FICTICIO, verify_password
from app.core.tiempo import ahora_utc
from app.models.organizacion import Usuario
from app.models.seguridad import RecoveryKey
from app.services.control_acceso import (
    bloquear_y_verificar,
    registrar_intento_exitoso,
    registrar_intento_fallido,
)
from app.services.password_service import (
    aplicar_contrasena_nueva,
    confirmar_cambio,
    validar_contrasena_nueva,
)

# Un solo mensaje para "no existe", "no es el Supervisor original" y "llave incorrecta":
# la respuesta no debe ayudar a averiguar cuál de los tres es.
MENSAJE_CREDENCIALES_INVALIDAS = "Correo o llave inválidos."


def _supervisor_original(db: Session, correo: str) -> Usuario | None:
    return db.exec(
        select(Usuario)
        .where(
            Usuario.correo == normalizar_correo(correo),
            col(Usuario.es_supervisor_original).is_(True),
            col(Usuario.activo).is_(True),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _llave_que_coincide(db: Session, usuario: Usuario | None, llave: str) -> RecoveryKey | None:
    """Compara la llave con las no usadas del usuario (bcrypt).

    Si no hay coincidencia, completa con verificaciones contra un hash ficticio hasta
    CANTIDAD_LLAVES: un fallo tarda lo mismo exista o no la cuenta y le queden las llaves
    que le queden, y el tiempo no delata cuál de los casos es.
    """
    llaves: list[RecoveryKey] = []
    if usuario is not None:
        llaves = list(
            db.exec(
                select(RecoveryKey)
                .where(RecoveryKey.id_usuario == usuario.id_usuario, col(RecoveryKey.usada_en).is_(None))
                .with_for_update()
            ).all()
        )
    for candidata in llaves:
        if verify_password(llave, candidata.llave_hash):
            return candidata
    for _ in range(CANTIDAD_LLAVES - len(llaves)):
        verify_password(llave, HASH_FICTICIO)
    return None


def llaves_restantes(db: Session, id_usuario: int) -> int:
    return db.exec(
        select(func.count())
        .select_from(RecoveryKey)
        .where(RecoveryKey.id_usuario == id_usuario, col(RecoveryKey.usada_en).is_(None))
    ).one()


def recuperar_con_llave(
    db: Session, correo: str, llave: str, nueva: str, confirmacion: str
) -> tuple[Usuario, int]:
    """Devuelve el usuario y cuántas llaves le quedan sin usar.

    1. Con bloqueo vigente: 429, sin evaluar nada más.
    2. Cuenta inexistente, que no es el Supervisor original o llave sin coincidencia:
       suma un fallo y responde 400 genérico.
    3. Contraseña nueva inválida: error, sin consumir la llave ni sumar fallo.
    4. Éxito, en una transacción: contraseña, llave usada, sesiones cerradas y contador
       de fallos en 0.
    """
    control = bloquear_y_verificar(db, normalizar_correo(correo))
    usuario = _supervisor_original(db, correo)
    coincidente = _llave_que_coincide(db, usuario, normalizar_llave(llave))
    if usuario is None or coincidente is None:
        registrar_intento_fallido(db, control)
        db.commit()
        raise ErrorNegocio(status.HTTP_400_BAD_REQUEST, MENSAJE_CREDENCIALES_INVALIDAS)

    validar_contrasena_nueva(nueva, confirmacion, usuario.password_hash)

    coincidente.usada_en = ahora_utc()
    db.add(coincidente)
    aplicar_contrasena_nueva(db, usuario, nueva)
    registrar_intento_exitoso(db, control)
    confirmar_cambio(db)
    return usuario, llaves_restantes(db, usuario.id_usuario)
