"""Asistencia por clase de los alumnos (CU022).

Una clase es fecha + colegio + grado + subprograma; se dicta una por semana, pero el
registro guarda la fecha de la clase.

- Lectura: el Docente, de un aula (colegio + grado) de su alcance vigente y en un día
  lectivo del periodo vigente; el Supervisor, de cualquier colegio y cualquier día lectivo
  ya llegado de cualquier periodo (atajos "Ver Asistencias" de CU021). Leer no exige
  actividad.
- Escritura: solo el Docente, con una actividad activa (queda en la auditoría y cuenta
  como productividad del módulo Asistencia), sobre alumnos activos de su alcance y en un
  día lectivo del periodo vigente. Todo o nada.
- Una fila por (alumno, fecha, subprograma): se crea la primera vez, con el aula
  (colegio y grado) que el alumno tiene en ese momento, y después se actualiza sin mudarla
  aunque el alumno haya rotado. Con dos dispositivos gana la captura más reciente
  (`fecha_registro`). Sin cambios reales no se escribe ni se audita.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import status
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc
from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION
from app.models.evaluacion import Asistencia
from app.models.organizacion import Alumno, Colegio, ColegioGrado, ColegioPrograma, Usuario
from app.schemas.asistencia import (
    AlumnoAsistencia,
    AsistenciaClase,
    AsistenciaGuardada,
    AsistenciaGuardar,
    RegistroAsistencia,
    RegistroGuardado,
)
from app.services.actividad import requerir_actividad_activa
from app.services.alumno_service import alcance_de
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import es_dia_lectivo, periodo_vigente
from app.services.sesiones import MARGEN_RELOJ

TABLA = "asistencia"
_asistencia = Asistencia.__table__
# Campos que un registro puede cambiar (los demás son de la captura).
CAMPOS = ("estado", "observacion")


# ── Errores ─────────────────────────────────────────────────────────────────────────

def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def _error_validacion(errores: list[tuple[str, str]]) -> ErrorNegocio:
    """422 con el formato único de validación (app/core/validacion.py)."""
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        MENSAJE_GENERAL,
        motivo=MOTIVO_VALIDACION,
        extra={"errores": [{"campo": campo, "mensaje": mensaje} for campo, mensaje in errores]},
    )


def _fecha_no_lectiva() -> ErrorNegocio:
    return _no_procesable(
        "La fecha seleccionada no corresponde a un día lectivo válido del periodo vigente.", "fecha_no_lectiva"
    )


# ── Fecha de la clase ───────────────────────────────────────────────────────────────

def lectiva_del_vigente(db: Session, fecha: date) -> bool:
    """Día lectivo del periodo vigente ya llegado: el único en que el Docente lee y
    registra. Sin periodo vigente, ninguno."""
    vigente = periodo_vigente(db)
    return vigente is not None and es_dia_lectivo(db, fecha, vigente)


# ── Consulta de la clase ────────────────────────────────────────────────────────────

def _validar_aula(db: Session, id_colegio: int, id_grado: int, id_programa: int) -> None:
    """422 si el colegio no existe o no ofrece ese grado o ese subprograma."""
    if db.get(Colegio, id_colegio) is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    if db.get(ColegioGrado, (id_colegio, id_grado)) is None:
        raise _no_procesable("El colegio no ofrece ese grado.", "grado_no_ofrecido")
    if db.get(ColegioPrograma, (id_colegio, id_programa)) is None:
        raise _no_procesable("El colegio no ofrece ese subprograma.", "programa_no_ofrecido")


def consultar_clase(
    db: Session, usuario: Usuario, id_colegio: int, id_grado: int, id_programa: int, fecha: date
) -> AsistenciaClase:
    """Asistencia de una clase (aula = colegio + grado + subprograma, en una fecha).

    Alumnos:
    - los que tienen un registro de ESA clase (por el aula guardada en el registro), aunque
      hoy estén inactivos (`activo: false`) o en otra aula (`en_aula: false`);
    - los activos que hoy pertenecen al aula y todavía no tienen registro en esa fecha y
      subprograma. Uno que ya tiene registro esa fecha en otra aula (rotó ese mismo día)
      no se lista: su asistencia de ese día ya se tomó allá y no puede tener dos.

    Errores: 403 `aula_fuera_de_alcance` (Docente), 422 del aula y 422
    `fecha_no_lectiva` (Docente: día lectivo del periodo vigente; Supervisor: cualquier
    día lectivo ya llegado).
    """
    alcance = alcance_de(db, usuario)
    es_docente = alcance is not None
    if es_docente and (id_colegio, id_grado) not in alcance:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "El colegio y grado indicados no están entre los que tiene asignados.",
            motivo="aula_fuera_de_alcance",
        )
    _validar_aula(db, id_colegio, id_grado, id_programa)
    lectiva = lectiva_del_vigente(db, fecha) if es_docente else es_dia_lectivo(db, fecha)
    if not lectiva:
        raise _fecha_no_lectiva()

    aula = (id_colegio, id_grado, id_programa)
    registrados = db.exec(
        select(Alumno, Asistencia.estado, Asistencia.observacion)
        .join(Alumno, Alumno.id_alumno == Asistencia.id_alumno)
        .where(
            Asistencia.id_colegio == id_colegio,
            Asistencia.id_grado == id_grado,
            Asistencia.id_programa == id_programa,
            Asistencia.fecha == fecha,
        )
    ).all()
    con_registro_en_la_fecha = select(Asistencia.id_alumno).where(
        Asistencia.fecha == fecha, Asistencia.id_programa == id_programa
    )
    sin_registro = db.exec(
        select(Alumno).where(
            Alumno.id_colegio == id_colegio,
            Alumno.id_grado == id_grado,
            Alumno.id_programa_actual == id_programa,
            col(Alumno.activo).is_(True),
            col(Alumno.id_alumno).not_in(con_registro_en_la_fecha),
        )
    ).all()
    filas = [*registrados, *((alumno, None, None) for alumno in sin_registro)]
    filas.sort(key=lambda f: (f[0].apellidos, f[0].nombres, f[0].id_alumno))
    return AsistenciaClase(
        fecha=fecha,
        editable=es_docente,
        alumnos=[
            AlumnoAsistencia(
                id_alumno=alumno.id_alumno, nombres=alumno.nombres, apellidos=alumno.apellidos,
                activo=alumno.activo, en_aula=_aula_de(alumno) == aula, estado=estado, observacion=observacion,
            )
            for alumno, estado, observacion in filas
        ],
    )


def _aula_de(alumno: Alumno) -> tuple[int, int, int]:
    """Aula a la que el alumno pertenece hoy: (colegio, grado, subprograma)."""
    return alumno.id_colegio, alumno.id_grado, alumno.id_programa_actual


# ── Registro ────────────────────────────────────────────────────────────────────────

def _fecha_registro(fecha: Optional[datetime], ahora: datetime) -> datetime:
    if fecha is None:
        return ahora
    fecha = fecha.astimezone(timezone.utc)
    if fecha > ahora + MARGEN_RELOJ:
        raise _error_validacion([("fecha_registro", "No puede ser posterior a la hora actual.")])
    return fecha


def _validar_registros(
    db: Session, usuario: Usuario, data: AsistenciaGuardar
) -> tuple[dict[int, Alumno], dict[tuple[int, int], Asistencia]]:
    """Los alumnos de los registros y sus filas ya guardadas en la fecha (bloqueadas).

    422 `validacion` con un error por registro inválido: alumno repetido, inexistente o
    inactivo, o fuera del alcance vigente del docente. El alcance se mide contra el aula
    del registro: la guardada si ya existe (no se muda), la actual del alumno si es nuevo.
    Si hay alguno, no se guarda nada."""
    alcance = alcance_de(db, usuario) or set()
    ids = {r.id_alumno for r in data.registros}
    alumnos = {a.id_alumno: a for a in db.exec(select(Alumno).where(col(Alumno.id_alumno).in_(ids))).all()}
    existentes = _existentes(db, data.fecha, list(alumnos))
    vistos: set[int] = set()
    errores = []
    for i, registro in enumerate(data.registros):
        campo = f"registros.{i}.id_alumno"
        alumno = alumnos.get(registro.id_alumno)
        fila = existentes.get((alumno.id_alumno, alumno.id_programa_actual)) if alumno else None
        if registro.id_alumno in vistos:
            errores.append((campo, "El alumno está repetido."))
        elif alumno is None or not alumno.activo:
            errores.append((campo, "El alumno no existe o está inactivo."))
        elif fila is not None and (fila.id_colegio, fila.id_grado) not in alcance:
            errores.append((campo, "Su asistencia de esa fecha es de un colegio y grado que no tiene asignados."))
        elif fila is None and (alumno.id_colegio, alumno.id_grado) not in alcance:
            errores.append((campo, "El alumno no pertenece a un colegio y grado que tenga asignados."))
        vistos.add(registro.id_alumno)
    if errores:
        raise _error_validacion(errores)
    return alumnos, existentes


def _existentes(db: Session, fecha: date, ids_alumno: list[int]) -> dict[tuple[int, int], Asistencia]:
    """Filas ya guardadas de esos alumnos en la fecha, por (alumno, subprograma),
    bloqueadas hasta el commit."""
    filas = db.exec(
        select(Asistencia)
        .where(Asistencia.fecha == fecha, col(Asistencia.id_alumno).in_(ids_alumno))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {(fila.id_alumno, fila.id_programa): fila for fila in filas}


def _insertar(db: Session, valores: dict) -> Optional[int]:
    """INSERT ... ON CONFLICT DO NOTHING sobre (alumno, fecha, subprograma). Devuelve el
    id nuevo, o None si otra petición simultánea ya creó la fila."""
    sentencia = (
        insert_con_conflicto(db, _asistencia)
        .values(**valores)
        .on_conflict_do_nothing(
            index_elements=[_asistencia.c.id_alumno, _asistencia.c.fecha, _asistencia.c.id_programa]
        )
        .returning(_asistencia.c.id)
    )
    return db.execute(sentencia).scalar_one_or_none()


def _cambios(fila: Optional[Asistencia], registro: RegistroAsistencia) -> dict[str, tuple]:
    """{campo: (antes, después)} de los campos enviados que cambian."""
    return {
        campo: (getattr(fila, campo) if fila else None, getattr(registro, campo))
        for campo in CAMPOS
        if campo in registro.model_fields_set and (fila is None or getattr(fila, campo) != getattr(registro, campo))
    }


def _guardado(id_alumno: int, aplicado: bool, fila: Optional[Asistencia], cambios: dict) -> RegistroGuardado:
    """Estado vigente del alumno en la clase después de procesar su registro."""
    vigente = {campo: getattr(fila, campo) if fila else None for campo in CAMPOS}
    if aplicado:
        vigente.update({campo: nuevo for campo, (_, nuevo) in cambios.items()})
    return RegistroGuardado(id_alumno=id_alumno, aplicado=aplicado, **vigente)


def guardar_asistencia(db: Session, usuario: Usuario, data: AsistenciaGuardar) -> AsistenciaGuardada:
    """Guarda la asistencia de uno o varios alumnos en una clase (CU022), todo o nada.

    Orden: actividad activa (409 `actividad_requerida`), día lectivo del periodo vigente
    (422 `fecha_no_lectiva`), `fecha_registro` y registros (422 `validacion`).

    Un registro nuevo va a la clase del aula y subprograma actuales del alumno; uno que ya
    existe conserva su aula aunque el alumno haya rotado. Si la fila guardada
    tiene una captura posterior a la que llega, gana la guardada: el registro no se
    aplica y la respuesta lo indica con `aplicado: false`. Varios cambios al mismo alumno
    y clase dentro de una actividad cuentan como un solo cambio (CU017: registros
    distintos por `id_registro`).
    """
    id_actividad = requerir_actividad_activa(db, usuario)
    if not lectiva_del_vigente(db, data.fecha):
        raise _fecha_no_lectiva()
    ahora = ahora_utc()
    fecha_registro = _fecha_registro(data.fecha_registro, ahora)
    alumnos, existentes = _validar_registros(db, usuario, data)

    auditoria = {"id_usuario": usuario.id_usuario, "fecha": ahora, "id_actividad": id_actividad}
    captura = {"id_docente": usuario.id_docente, "fecha_registro": fecha_registro}
    guardados = []
    for registro in data.registros:
        alumno = alumnos[registro.id_alumno]
        clave = (alumno.id_alumno, alumno.id_programa_actual)
        fila = existentes.get(clave)
        if fila is None:
            clase = {
                "id_alumno": alumno.id_alumno, "fecha": data.fecha, "id_programa": alumno.id_programa_actual,
                "id_colegio": alumno.id_colegio, "id_grado": alumno.id_grado,
            }
            nuevos = {campo: getattr(registro, campo) for campo in CAMPOS if campo in registro.model_fields_set}
            id_nuevo = _insertar(
                db, {**clase, **nuevos, **captura, "creado_por": usuario.id_usuario, "creado_en": ahora}
            )
            if id_nuevo is not None:
                registrar_auditoria(db, tabla=TABLA, id_registro=id_nuevo, accion=CREAR, **auditoria)
                guardados.append(_guardado(registro.id_alumno, True, None, _cambios(None, registro)))
                continue
            # Otra petición la creó entre la lectura y el INSERT: se trata como edición.
            fila = _existentes(db, data.fecha, [clave[0]])[clave]

        cambios = _cambios(fila, registro)
        if fila.fecha_registro > fecha_registro:
            guardados.append(_guardado(registro.id_alumno, False, fila, cambios))
            continue
        guardados.append(_guardado(registro.id_alumno, True, fila, cambios))
        if not cambios:
            continue
        registrar_auditoria(
            db, tabla=TABLA, id_registro=fila.id, accion=EDITAR, cambios=cambios, **auditoria
        )
        for campo, (_, nuevo) in cambios.items():
            setattr(fila, campo, nuevo)
        fila.id_docente = usuario.id_docente
        fila.fecha_registro = fecha_registro
        fila.modificado_por = usuario.id_usuario
        fila.modificado_en = ahora
        db.add(fila)
    db.commit()
    return AsistenciaGuardada(fecha=data.fecha, registros=guardados)
