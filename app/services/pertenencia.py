"""Pertenencia histórica del alumno a un grupo (CU014).

Un grupo es colegio + grado + sección + subprograma. Cada intervalo de
`alumno_pertenencia` dice desde qué día (`desde`, inclusivo) y hasta qué día (`hasta`,
exclusivo: el primero en que ya no pertenece; NULL = vigente) estuvo el alumno en él. Los
intervalos de un alumno no se superponen y como mucho uno está vigente.

- Lecturas para Asistencias, Lectura, Rúbrica y Registro de Vuelo: `pertenencia_en`,
  `alumnos_del_grupo` y `alumnos_del_grupo_en_rango`.
- Escrituras para CU014: `abrir` (crear, activar), `cambiar_grupo` (editar) y `cerrar`
  (inactivar). Ninguna hace commit.
Las fechas efectivas nunca son futuras y respetan el orden del historial.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.core.tiempo import hoy_lima
from app.models.organizacion import Alumno, AlumnoPertenencia

MENSAJE_FECHA_FUTURA = "Selecciona la fecha de hoy o una fecha anterior."
MENSAJE_ANTES_DEL_GRUPO_ACTUAL = "Selecciona el {fecha} o una fecha posterior: ese día empezó su grupo actual."
MENSAJE_ANTES_DE_LA_SALIDA = "Selecciona el {fecha} o una fecha posterior: ese día dejó su último grupo."
FORMATO_FECHA = "%d/%m/%Y"
# Campo virtual de la auditoría del alumno: su valor nuevo es la fecha efectiva.
CAMPO_AUDITORIA = "pertenencia"


@dataclass(frozen=True)
class Movimiento:
    """Quién registra un cambio de pertenencia, cuándo y en qué actividad (si es Docente)."""

    id_actor: int
    ahora: datetime
    id_actividad: Optional[uuid.UUID] = None


def fecha_legible(fecha: date) -> str:
    """DD/MM/AAAA, como se muestra en la interfaz."""
    return fecha.strftime(FORMATO_FECHA)


# ── Lecturas ────────────────────────────────────────────────────────────────────────

def _vigente_en(fecha: date) -> tuple:
    return (
        AlumnoPertenencia.desde <= fecha,
        or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > fecha),
    )


def _del_grupo(id_colegio: int, id_grado: int, id_seccion: int, id_programa: Optional[int]) -> list:
    filtro = [
        AlumnoPertenencia.id_colegio == id_colegio,
        AlumnoPertenencia.id_grado == id_grado,
        AlumnoPertenencia.id_seccion == id_seccion,
    ]
    if id_programa is not None:
        filtro.append(AlumnoPertenencia.id_programa == id_programa)
    return filtro


def _alumnos(db: Session, filtro: list) -> list[Alumno]:
    ids = select(AlumnoPertenencia.id_alumno).where(*filtro)
    return list(
        db.exec(
            select(Alumno)
            .where(col(Alumno.id_alumno).in_(ids))
            .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
        ).all()
    )


def pertenencia_en(db: Session, id_alumno: int, fecha: date) -> Optional[AlumnoPertenencia]:
    """El intervalo del alumno que contiene `fecha`, o None si ese día no pertenecía a
    ningún grupo (todavía no estaba o estaba inactivo)."""
    return db.exec(
        select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == id_alumno, *_vigente_en(fecha))
    ).first()


def alumnos_del_grupo(
    db: Session, id_colegio: int, id_grado: int, id_seccion: int, fecha: date, id_programa: Optional[int] = None
) -> list[Alumno]:
    """Alumnos que pertenecían al grupo en `fecha`, por apellidos y nombres. No depende de
    su ficha actual: un alumno que después cambió de grupo o se inactivó también sale."""
    return _alumnos(db, [*_del_grupo(id_colegio, id_grado, id_seccion, id_programa), *_vigente_en(fecha)])


def alumnos_del_grupo_en_rango(
    db: Session,
    id_colegio: int,
    id_grado: int,
    id_seccion: int,
    desde: date,
    hasta: date,
    id_programa: Optional[int] = None,
) -> list[Alumno]:
    """Alumnos que pertenecieron al grupo al menos un día entre `desde` y `hasta` (ambos
    inclusivos, como un mes o un PA), por apellidos y nombres."""
    return _alumnos(
        db,
        [
            *_del_grupo(id_colegio, id_grado, id_seccion, id_programa),
            AlumnoPertenencia.desde <= hasta,
            or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > desde),
        ],
    )


def vigente(db: Session, id_alumno: int) -> Optional[AlumnoPertenencia]:
    return db.exec(
        select(AlumnoPertenencia).where(
            AlumnoPertenencia.id_alumno == id_alumno, col(AlumnoPertenencia.hasta).is_(None)
        )
    ).first()


def _ultima(db: Session, id_alumno: int) -> Optional[AlumnoPertenencia]:
    return db.exec(
        select(AlumnoPertenencia)
        .where(AlumnoPertenencia.id_alumno == id_alumno)
        .order_by(col(AlumnoPertenencia.desde).desc())
    ).first()


# ── Validaciones ────────────────────────────────────────────────────────────────────

def validar_contra_listas_historicas(db: Session, id_alumno: int, desde: date, hasta: Optional[date]) -> None:
    """Punto de extensión de CU014 para T6: rechazar un cambio de pertenencia que
    contradiga una lista de clase ya guardada (`clase_alumno`) entre `desde` (inclusivo) y
    `hasta` (exclusivo; None = sin fin). Hoy no hay listas guardadas, así que no rechaza
    nada. T6 lo implementa con un 409 que nombre las clases afectadas."""


def validar_fecha_efectiva(fecha: date, campo: str) -> None:
    """422 en `campo` si la fecha es futura (día de Lima)."""
    if fecha > hoy_lima():
        raise error_de_campo(campo, MENSAJE_FECHA_FUTURA)


def _no_antes_de(fecha: date, limite: date, mensaje: str, campo: str) -> None:
    if fecha < limite:
        raise error_de_campo(campo, mensaje.format(fecha=fecha_legible(limite)))


# ── Escrituras ──────────────────────────────────────────────────────────────────────

def _nuevo_intervalo(alumno: Alumno, desde: date, movimiento: Movimiento) -> AlumnoPertenencia:
    return AlumnoPertenencia(
        id_alumno=alumno.id_alumno,
        id_colegio=alumno.id_colegio,
        id_grado=alumno.id_grado,
        id_seccion=alumno.id_seccion,
        id_programa=alumno.id_programa_actual,
        desde=desde,
        id_actividad=movimiento.id_actividad,
        creado_por=movimiento.id_actor,
        creado_en=movimiento.ahora,
    )


def _modificado(intervalo: AlumnoPertenencia, movimiento: Movimiento) -> None:
    intervalo.modificado_por = movimiento.id_actor
    intervalo.modificado_en = movimiento.ahora


def abrir(db: Session, alumno: Alumno, desde: date, campo: str, movimiento: Movimiento) -> None:
    """Abre un intervalo vigente con el grupo de la ficha del alumno (crear o activar).
    `desde` no puede ser futura ni anterior al día en que dejó su último grupo."""
    validar_fecha_efectiva(desde, campo)
    ultima = _ultima(db, alumno.id_alumno)
    if ultima is not None:
        _no_antes_de(desde, ultima.hasta or ultima.desde, MENSAJE_ANTES_DE_LA_SALIDA, campo)
    validar_contra_listas_historicas(db, alumno.id_alumno, desde, None)
    db.add(_nuevo_intervalo(alumno, desde, movimiento))


def cerrar(db: Session, id_alumno: int, hasta: date, campo: str, movimiento: Movimiento) -> None:
    """Cierra el intervalo vigente: desde `hasta` el alumno ya no pertenece al grupo
    (inactivar). Si `hasta` es el mismo día en que empezó, el intervalo no llegó a tener
    días y se elimina."""
    validar_fecha_efectiva(hasta, campo)
    actual = vigente(db, id_alumno)
    if actual is None:
        return
    _no_antes_de(hasta, actual.desde, MENSAJE_ANTES_DEL_GRUPO_ACTUAL, campo)
    validar_contra_listas_historicas(db, id_alumno, hasta, None)
    if hasta == actual.desde:
        db.delete(actual)
        return
    actual.hasta = hasta
    _modificado(actual, movimiento)
    db.add(actual)


def cambiar_grupo(db: Session, alumno: Alumno, desde: date, campo: str, movimiento: Movimiento) -> None:
    """Pasa al alumno al grupo de su ficha desde `desde`: cierra el intervalo vigente ese
    día y abre el nuevo, en la misma transacción. Si `desde` es el día en que empezó el
    vigente, se reemplaza ese intervalo. Sin intervalo vigente, abre uno."""
    validar_fecha_efectiva(desde, campo)
    actual = vigente(db, alumno.id_alumno)
    if actual is None:
        abrir(db, alumno, desde, campo, movimiento)
        return
    _no_antes_de(desde, actual.desde, MENSAJE_ANTES_DEL_GRUPO_ACTUAL, campo)
    validar_contra_listas_historicas(db, alumno.id_alumno, desde, None)
    if desde == actual.desde:
        nuevo = _nuevo_intervalo(alumno, desde, movimiento)
        for atributo in ("id_colegio", "id_grado", "id_seccion", "id_programa", "id_actividad"):
            setattr(actual, atributo, getattr(nuevo, atributo))
        _modificado(actual, movimiento)
        db.add(actual)
        return
    actual.hasta = desde
    _modificado(actual, movimiento)
    db.add(actual)
    # El vigente se cierra antes de insertar el nuevo: un solo intervalo con hasta NULL.
    db.flush()
    db.add(_nuevo_intervalo(alumno, desde, movimiento))
