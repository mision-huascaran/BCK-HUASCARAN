"""inicializar-supervisor-original: crea el Supervisor original (CU001) desde el entorno.

Se ejecuta en cada arranque: si ya existe, no hace nada. Se crea por script y no por
migración para no versionar credenciales (diseño v3, §3). Nunca registra la contraseña
ni los hashes en el log.
"""
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.cli.comun import Resumen, id_supervisor_original
from app.core.config import settings
from app.core.correo import normalizar_correo
from app.core.politica_contrasena import errores_politica_contrasena
from app.core.security import hash_password
from app.core.tiempo import ahora_utc
from app.models.organizacion import Rol, Usuario
from app.models.seguridad import RecoveryKey

CANTIDAD_HASHES = 10
PATRON_DNI = re.compile(r"^\d{8}$")
# $2a$/$2b$/$2y$, costo de 2 dígitos, 22 de salt + 31 de hash en el alfabeto de bcrypt.
PATRON_BCRYPT = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
PREFIJO = "SUPERVISOR_ORIGINAL_"


@dataclass
class DatosSupervisor:
    correo: str
    password: str
    nombres: str
    apellidos: str
    dni: str
    recovery_hashes: str

    @classmethod
    def desde_settings(cls) -> "DatosSupervisor":
        return cls(
            correo=settings.SUPERVISOR_ORIGINAL_CORREO,
            password=settings.SUPERVISOR_ORIGINAL_PASSWORD.get_secret_value(),
            nombres=settings.SUPERVISOR_ORIGINAL_NOMBRES,
            apellidos=settings.SUPERVISOR_ORIGINAL_APELLIDOS,
            dni=settings.SUPERVISOR_ORIGINAL_DNI,
            recovery_hashes=settings.SUPERVISOR_ORIGINAL_RECOVERY_HASHES.get_secret_value(),
        )

    def faltantes(self) -> list[str]:
        return [PREFIJO + campo.upper() for campo, valor in vars(self).items() if not valor.strip()]

    def hashes(self) -> list[str]:
        return [h.strip() for h in self.recovery_hashes.split(",") if h.strip()]


def errores_de_validacion(datos: DatosSupervisor) -> list[str]:
    """Validaciones que no necesitan la BD. Los mensajes nunca incluyen secretos."""
    errores = []
    reglas = errores_politica_contrasena(datos.password)
    if reglas:
        errores.append(f"{PREFIJO}PASSWORD no cumple la política de CU006: {', '.join(reglas)}")
    if not PATRON_DNI.match(datos.dni.strip()):
        errores.append(f"{PREFIJO}DNI debe tener exactamente 8 dígitos")
    if "@" not in datos.correo:
        errores.append(f"{PREFIJO}CORREO no parece un correo")
    hashes = datos.hashes()
    if len(hashes) != CANTIDAD_HASHES:
        errores.append(
            f"{PREFIJO}RECOVERY_HASHES debe tener exactamente {CANTIDAD_HASHES} hashes "
            f"separados por comas; tiene {len(hashes)}"
        )
    invalidos = sum(1 for h in hashes if not PATRON_BCRYPT.match(h))
    if invalidos:
        errores.append(f"{PREFIJO}RECOVERY_HASHES: {invalidos} hash(es) sin formato bcrypt")
    if len(set(hashes)) != len(hashes):
        errores.append(f"{PREFIJO}RECOVERY_HASHES tiene hashes repetidos")
    return errores


def _siguiente_id_usuario(db: Session) -> int:
    """Id que tendrá el usuario, para que `creado_por` apunte a sí mismo en un solo INSERT."""
    if db.get_bind().dialect.name == "postgresql":
        return db.connection().execute(
            text("SELECT nextval(pg_get_serial_sequence('usuario', 'id_usuario'))")
        ).scalar_one()
    return (db.exec(select(func.max(Usuario.id_usuario))).one() or 0) + 1


def inicializar_supervisor_original(
    db: Session, datos: Optional[DatosSupervisor] = None
) -> Resumen:
    resumen = Resumen()
    if id_supervisor_original(db) is not None:
        resumen.omitir("el Supervisor original ya existe")
        return resumen

    datos = datos or DatosSupervisor.desde_settings()
    faltantes = datos.faltantes()
    if faltantes:
        resumen.advertir(
            f"No se crea el Supervisor original: faltan variables de entorno ({', '.join(faltantes)})"
        )
        return resumen

    errores = errores_de_validacion(datos)
    correo = normalizar_correo(datos.correo)
    dni = datos.dni.strip()
    if db.exec(select(Usuario).where(Usuario.correo == correo)).first() is not None:
        errores.append(f"el correo {correo} ya está en uso por otra cuenta")
    if db.exec(select(Usuario).where(Usuario.dni == dni)).first() is not None:
        errores.append("el DNI ya está en uso por otra cuenta")
    rol = db.exec(select(Rol).where(Rol.nombre == "Supervisor")).first()
    if rol is None:
        errores.append("no existe el rol Supervisor (¿se ejecutó cargar-catalogos?)")
    if errores:
        for mensaje in errores:
            resumen.error(f"No se crea el Supervisor original: {mensaje}")
        return resumen

    ahora = ahora_utc()
    id_usuario = _siguiente_id_usuario(db)
    db.add(
        Usuario(
            id_usuario=id_usuario,
            id_rol=rol.id_rol,
            correo=correo,
            password_hash=hash_password(datos.password),
            dni=dni,
            nombres=datos.nombres.strip(),
            apellidos=datos.apellidos.strip(),
            activo=True,
            es_supervisor_original=True,
            creado_por=id_usuario,
            creado_en=ahora,
        )
    )
    db.flush()
    for llave_hash in datos.hashes():
        db.add(RecoveryKey(id_usuario=id_usuario, llave_hash=llave_hash, creada_en=ahora))
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        resumen.error(f"No se crea el Supervisor original: la BD rechazó la fila ({e.orig.__class__.__name__})")
        return resumen

    resumen.creados["usuario (Supervisor original)"] += 1
    resumen.creados["recovery_key"] += CANTIDAD_HASHES
    return resumen
