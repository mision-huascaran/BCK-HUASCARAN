"""Si el usuario puede escribir en un módulo para un grupo y PA (el bloque `escritura` de
las consultas de Asistencias, Lectura y Rúbrica).

Solo el Docente escribe: con habilitación del módulo para ese colegio, grado y PA
(app/services/habilitacion.py) y una actividad activa. Si escribe con un permiso de
corrección (CU028), se avisa hasta cuándo vale.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from app.core.tiempo import ZONA_LIMA
from app.models.organizacion import PermisoCorreccion, Usuario
from app.schemas.asistencia import Escritura
from app.services.actividad import actividad_activa
from app.services.calendario import PeriodoCalendario, nombre_periodo
from app.services.habilitacion import habilitacion_escritura
from app.services.pertenencia import fecha_legible
from app.services.usuario_service import DOCENTE, nombre_rol

AVISO_PERMISO = "Corrección excepcional habilitada para {pa} hasta el {fecha} a las {hora}."


@dataclass(frozen=True)
class MensajesEscritura:
    """Los textos de cada CU para cuando no se puede escribir."""

    solo_lectura: str
    sin_habilitacion: str
    sin_actividad: str


@dataclass(frozen=True)
class Destino:
    """Dónde se escribiría: módulo, colegio, grado y PA."""

    modulo: str
    id_colegio: int
    id_grado: int
    periodo: PeriodoCalendario


def aviso_permiso(periodo: PeriodoCalendario, fin: datetime) -> str:
    local = fin.astimezone(ZONA_LIMA)
    return AVISO_PERMISO.format(
        pa=nombre_periodo(periodo.tipo, periodo.numero), fecha=fecha_legible(local.date()), hora=local.strftime("%H:%M")
    )


def escritura(db: Session, usuario: Usuario, destino: Destino, mensajes: MensajesEscritura) -> Escritura:
    """Si el usuario puede escribir ahora en el destino y, si no, por qué."""
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return Escritura(permitida=False, motivo=mensajes.solo_lectura)
    habilitacion = habilitacion_escritura(
        db, usuario, destino.id_colegio, destino.id_grado, destino.periodo.id_periodo_academico, destino.modulo
    )
    if not habilitacion.permitido:
        return Escritura(permitida=False, motivo=mensajes.sin_habilitacion)
    aviso = None
    if habilitacion.id_permiso is not None:
        aviso = aviso_permiso(destino.periodo, db.get(PermisoCorreccion, habilitacion.id_permiso).fin)
    if actividad_activa(db, usuario.id_docente) is None:
        return Escritura(
            permitida=False, motivo=mensajes.sin_actividad, id_permiso=habilitacion.id_permiso, aviso_permiso=aviso
        )
    return Escritura(permitida=True, id_permiso=habilitacion.id_permiso, aviso_permiso=aviso)
