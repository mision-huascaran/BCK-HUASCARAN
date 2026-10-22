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

from fastapi import status
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio, error_de_campo
from app.core.tiempo import hoy_lima
from app.models.evaluacion import Clase, ClaseAlumno
from app.models.organizacion import Alumno, AlumnoPertenencia, Colegio, Grado, Seccion

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


def lista_del_grupo(
    db: Session, id_colegio: int, id_grado: int, id_seccion: int, fecha: date
) -> list[tuple[Alumno, int]]:
    """Como `alumnos_del_grupo`, con el subprograma de cada alumno en esa fecha (la lista
    que se congela en una clase)."""
    return [
        (alumno, id_programa)
        for alumno, id_programa in db.exec(
            select(Alumno, AlumnoPertenencia.id_programa)
            .join(AlumnoPertenencia, AlumnoPertenencia.id_alumno == Alumno.id_alumno)
            .where(*_del_grupo(id_colegio, id_grado, id_seccion, None), *_vigente_en(fecha))
            .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
        ).all()
    ]


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

MENSAJE_CONFLICTO_LISTAS = (
    "No se puede aplicar el cambio desde esa fecha: contradice la lista de alumnos de clases que ya "
    "tienen asistencias registradas."
)


def _clases_donde_figura_sin_pertenecer(db: Session, id_alumno: int, intervalos: list[AlumnoPertenencia]) -> list[Clase]:
    """Clases con el alumno en su lista congelada en cuya fecha ya no pertenece a ese
    grupo (cambio de grupo o inactivación desde una fecha anterior, o inicio posterior)."""
    clases = db.exec(
        select(Clase).join(ClaseAlumno, ClaseAlumno.id_clase == Clase.id_clase).where(ClaseAlumno.id_alumno == id_alumno)
    ).all()

    def pertenece(clase: Clase) -> bool:
        return any(
            (i.id_colegio, i.id_grado, i.id_seccion) == (clase.id_colegio, clase.id_grado, clase.id_seccion)
            and i.desde <= clase.fecha and (i.hasta is None or i.hasta > clase.fecha)
            for i in intervalos
        )

    return [c for c in clases if not pertenece(c)]


def _clases_donde_pertenece_sin_figurar(db: Session, id_alumno: int, intervalos: list[AlumnoPertenencia]) -> list[Clase]:
    """Clases con lista congelada de un grupo al que el alumno pertenecía en su fecha,
    pero en cuya lista no figura (pasarlo a un grupo con listas ya guardadas)."""
    en_sus_listas = select(ClaseAlumno.id_clase).where(ClaseAlumno.id_alumno == id_alumno)
    clases: list[Clase] = []
    for intervalo in intervalos:
        en_el_intervalo = [Clase.fecha >= intervalo.desde]
        if intervalo.hasta is not None:
            en_el_intervalo.append(Clase.fecha < intervalo.hasta)
        clases.extend(db.exec(
            select(Clase).where(
                Clase.id_colegio == intervalo.id_colegio,
                Clase.id_grado == intervalo.id_grado,
                Clase.id_seccion == intervalo.id_seccion,
                col(Clase.lista_congelada_en).is_not(None),
                col(Clase.id_clase).not_in(en_sus_listas),
                *en_el_intervalo,
            )
        ).all())
    return clases


def _descripcion(db: Session, clases: list[Clase]) -> list[dict]:
    nombres = {
        (c, g, s): (colegio, grado, seccion)
        for c, g, s, colegio, grado, seccion in db.exec(
            select(Seccion.id_colegio, Seccion.id_grado, Seccion.id_seccion, Colegio.nombre, Grado.nombre, Seccion.nombre)
            .join(Colegio, Colegio.id_colegio == Seccion.id_colegio)
            .join(Grado, Grado.id_grado == Seccion.id_grado)
            .where(col(Seccion.id_seccion).in_({c.id_seccion for c in clases}))
        ).all()
    }
    salida = []
    for clase in sorted({c.id_clase: c for c in clases}.values(), key=lambda c: (c.fecha, c.id_clase)):
        colegio, grado, seccion = nombres[(clase.id_colegio, clase.id_grado, clase.id_seccion)]
        salida.append({
            "id_clase": clase.id_clase, "fecha": clase.fecha.isoformat(), "colegio": colegio, "grado": grado,
            "seccion": seccion,
        })
    return salida


def validar_contra_listas_historicas(db: Session, id_alumno: int) -> None:
    """Rechaza (409 `conflicto_listas_historicas`, con las `clases` afectadas) un cambio
    de pertenencia que contradiga una lista de clase congelada (`clase_alumno`). Se llama
    con el cambio ya aplicado (sin commit) y compara el historial resultante con las
    listas:

    - el alumno figura en una lista y en su fecha ya no pertenecería a ese grupo (cambio
      de grupo o inactivación desde una fecha anterior, o inicio posterior a la lista);
    - el alumno pertenecería a un grupo en la fecha de una lista congelada en la que no
      figura (cambio a un grupo que ya tiene listas desde esa fecha).

    La petición termina en error y nada se guarda."""
    db.flush()
    intervalos = list(db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == id_alumno)).all())
    conflictos = [
        *_clases_donde_figura_sin_pertenecer(db, id_alumno, intervalos),
        *_clases_donde_pertenece_sin_figurar(db, id_alumno, intervalos),
    ]
    if conflictos:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT, MENSAJE_CONFLICTO_LISTAS, motivo="conflicto_listas_historicas",
            extra={"clases": _descripcion(db, conflictos)},
        )


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
    db.add(_nuevo_intervalo(alumno, desde, movimiento))
    validar_contra_listas_historicas(db, alumno.id_alumno)


def cerrar(db: Session, id_alumno: int, hasta: date, campo: str, movimiento: Movimiento) -> None:
    """Cierra el intervalo vigente: desde `hasta` el alumno ya no pertenece al grupo
    (inactivar). Si `hasta` es el mismo día en que empezó, el intervalo no llegó a tener
    días y se elimina."""
    validar_fecha_efectiva(hasta, campo)
    actual = vigente(db, id_alumno)
    if actual is None:
        return
    _no_antes_de(hasta, actual.desde, MENSAJE_ANTES_DEL_GRUPO_ACTUAL, campo)
    if hasta == actual.desde:
        db.delete(actual)
    else:
        actual.hasta = hasta
        _modificado(actual, movimiento)
        db.add(actual)
    validar_contra_listas_historicas(db, id_alumno)


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
    if desde == actual.desde:
        nuevo = _nuevo_intervalo(alumno, desde, movimiento)
        for atributo in ("id_colegio", "id_grado", "id_seccion", "id_programa", "id_actividad"):
            setattr(actual, atributo, getattr(nuevo, atributo))
        _modificado(actual, movimiento)
        db.add(actual)
    else:
        actual.hasta = desde
        _modificado(actual, movimiento)
        db.add(actual)
        # El vigente se cierra antes de insertar el nuevo: un solo intervalo con hasta NULL.
        db.flush()
        db.add(_nuevo_intervalo(alumno, desde, movimiento))
    validar_contra_listas_historicas(db, alumno.id_alumno)
