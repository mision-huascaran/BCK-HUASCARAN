"""Registro de asistencia semanal de los alumnos (CU022).

- Lectura: el Docente, de un aula (colegio + grado) de su alcance vigente; el Supervisor,
  de cualquier colegio (atajos "Ver Asistencias" de CU021).
- Escritura: solo el Docente, con una actividad activa (queda en la auditoría y cuenta
  como productividad del módulo Asistencia), sobre alumnos activos de su alcance y en
  una semana editable: del periodo vigente y ya empezada. Todo o nada.
- Una fila por (alumno, semana): se crea la primera vez y después se actualiza. Si el
  valor no cambia, no se escribe ni se audita.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import status
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, hoy_lima
from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION
from app.models.evaluacion import AsistenciaSemanal, SemanaReporte
from app.models.organizacion import Alumno, Colegio, ColegioGrado, ColegioPrograma, Usuario
from app.schemas.asistencia import (
    AlumnoAsistencia,
    AsistenciaAula,
    AsistenciaGuardada,
    AsistenciaGuardar,
    SemanaAsistencia,
)
from app.services.actividad import requerir_actividad_activa
from app.services.alumno_service import alcance_de
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import periodo_vigente
from app.services.sesiones import MARGEN_RELOJ

TABLA = "asistencia_semanal"
_asistencia = AsistenciaSemanal.__table__


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


# ── Semana ──────────────────────────────────────────────────────────────────────────

def _semana(db: Session, id_semana: int) -> SemanaReporte:
    semana = db.get(SemanaReporte, id_semana)
    if semana is None:
        raise _no_procesable("No existe la semana indicada.", "semana_inexistente")
    return semana


def semana_editable(db: Session, semana: SemanaReporte) -> bool:
    """Admite registro: pertenece al periodo vigente y ya empezó. Las futuras y las de
    otros periodos son de solo lectura; sin periodo vigente, ninguna es editable."""
    vigente = periodo_vigente(db)
    return (
        vigente is not None
        and semana.id_periodo_academico == vigente.id_periodo_academico
        and semana.fecha_inicio <= hoy_lima()
    )


# ── Consulta del aula ───────────────────────────────────────────────────────────────

def _validar_aula(db: Session, id_colegio: int, id_grado: int, id_programa: int) -> None:
    """422 si el colegio no existe o no ofrece ese grado o ese subprograma."""
    if db.get(Colegio, id_colegio) is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    if not db.exec(
        select(ColegioGrado).where(ColegioGrado.id_colegio == id_colegio, ColegioGrado.id_grado == id_grado)
    ).first():
        raise _no_procesable("El colegio no ofrece ese grado.", "grado_no_ofrecido")
    if not db.exec(
        select(ColegioPrograma).where(
            ColegioPrograma.id_colegio == id_colegio, ColegioPrograma.id_programa == id_programa
        )
    ).first():
        raise _no_procesable("El colegio no ofrece ese subprograma.", "programa_no_ofrecido")


def consultar_aula(
    db: Session, usuario: Usuario, id_colegio: int, id_grado: int, id_programa: int, id_semana: int
) -> AsistenciaAula:
    """Asistencia de una semana para los alumnos activos del aula y subprograma.

    El Docente solo ve aulas de su alcance vigente (403 `aula_fuera_de_alcance`, como en
    Alumnos); no necesita actividad para leer.
    """
    alcance = alcance_de(db, usuario)
    if alcance is not None and (id_colegio, id_grado) not in alcance:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "El colegio y grado indicados no están entre los que tiene asignados.",
            motivo="aula_fuera_de_alcance",
        )
    _validar_aula(db, id_colegio, id_grado, id_programa)
    semana = _semana(db, id_semana)

    filas = db.exec(
        select(Alumno.id_alumno, Alumno.nombres, Alumno.apellidos, AsistenciaSemanal.asistio)
        .outerjoin(
            AsistenciaSemanal,
            (AsistenciaSemanal.id_alumno == Alumno.id_alumno) & (AsistenciaSemanal.id_semana == semana.id_semana),
        )
        .where(
            Alumno.id_colegio == id_colegio,
            Alumno.id_grado == id_grado,
            Alumno.id_programa_actual == id_programa,
            col(Alumno.activo).is_(True),
        )
        .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
    ).all()
    return AsistenciaAula(
        semana=SemanaAsistencia(
            id_semana=semana.id_semana,
            numero=semana.numero_semana,
            fecha_inicio=semana.fecha_inicio,
            fecha_fin=semana.fecha_fin,
        ),
        editable=semana_editable(db, semana),
        alumnos=[
            AlumnoAsistencia(id_alumno=id_alumno, nombres=nombres, apellidos=apellidos, asistio=asistio)
            for id_alumno, nombres, apellidos, asistio in filas
        ],
    )


# ── Registro ────────────────────────────────────────────────────────────────────────

def _fecha_registro(fecha: Optional[datetime], ahora: datetime) -> datetime:
    if fecha is None:
        return ahora
    fecha = fecha.astimezone(timezone.utc)
    if fecha > ahora + MARGEN_RELOJ:
        raise _error_validacion([("fecha_registro", "No puede ser posterior a la hora actual.")])
    return fecha


def _validar_registros(db: Session, usuario: Usuario, data: AsistenciaGuardar) -> None:
    """422 `validacion` con un error por registro inválido: alumno repetido, inexistente
    o inactivo, o fuera del alcance vigente del docente. Si hay alguno, no se guarda nada."""
    alcance = alcance_de(db, usuario) or set()
    ids = {r.id_alumno for r in data.registros}
    alumnos = {a.id_alumno: a for a in db.exec(select(Alumno).where(col(Alumno.id_alumno).in_(ids))).all()}
    vistos: set[int] = set()
    errores = []
    for i, registro in enumerate(data.registros):
        campo = f"registros.{i}.id_alumno"
        alumno = alumnos.get(registro.id_alumno)
        if registro.id_alumno in vistos:
            errores.append((campo, "El alumno está repetido."))
        elif alumno is None or not alumno.activo:
            errores.append((campo, "El alumno no existe o está inactivo."))
        elif (alumno.id_colegio, alumno.id_grado) not in alcance:
            errores.append((campo, "El alumno no pertenece a un colegio y grado que tenga asignados."))
        vistos.add(registro.id_alumno)
    if errores:
        raise _error_validacion(errores)


def _insertar(db: Session, valores: dict) -> Optional[int]:
    """INSERT ... ON CONFLICT DO NOTHING sobre (alumno, semana). Devuelve el id nuevo, o
    None si otra petición simultánea ya creó la fila."""
    sentencia = (
        insert_con_conflicto(db, _asistencia)
        .values(**valores)
        .on_conflict_do_nothing(index_elements=[_asistencia.c.id_alumno, _asistencia.c.id_semana])
        .returning(_asistencia.c.id)
    )
    return db.execute(sentencia).scalar_one_or_none()


def _existentes(db: Session, id_semana: int, ids_alumno: list[int]) -> dict[int, AsistenciaSemanal]:
    """Filas ya guardadas de esos alumnos en la semana, bloqueadas hasta el commit."""
    filas = db.exec(
        select(AsistenciaSemanal)
        .where(AsistenciaSemanal.id_semana == id_semana, col(AsistenciaSemanal.id_alumno).in_(ids_alumno))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {fila.id_alumno: fila for fila in filas}


def guardar_asistencia(db: Session, usuario: Usuario, data: AsistenciaGuardar) -> AsistenciaGuardada:
    """Guarda la asistencia de uno o varios alumnos en una semana (CU022), todo o nada.

    Orden: actividad activa (409 `actividad_requerida`), semana existente (422
    `semana_inexistente`) y editable (409 `semana_no_editable`), `fecha_registro` y
    registros (422 `validacion`). Varios cambios al mismo alumno y semana dentro de una
    actividad cuentan como un solo cambio (CU017: registros distintos por `id_registro`).
    """
    id_actividad = requerir_actividad_activa(db, usuario)
    semana = _semana(db, data.id_semana)
    if not semana_editable(db, semana):
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            "La semana seleccionada no admite registro de asistencia: solo las semanas ya "
            "iniciadas del periodo vigente.",
            motivo="semana_no_editable",
        )
    ahora = ahora_utc()
    fecha_registro = _fecha_registro(data.fecha_registro, ahora)
    _validar_registros(db, usuario, data)

    existentes = _existentes(db, semana.id_semana, [r.id_alumno for r in data.registros])
    auditoria = {"id_usuario": usuario.id_usuario, "fecha": ahora, "id_actividad": id_actividad}
    for registro in data.registros:
        fila = existentes.get(registro.id_alumno)
        if fila is None:
            id_nuevo = _insertar(
                db,
                {
                    "id_alumno": registro.id_alumno,
                    "id_semana": semana.id_semana,
                    "asistio": registro.asistio,
                    "id_docente": usuario.id_docente,
                    "fecha_registro": fecha_registro,
                    "creado_por": usuario.id_usuario,
                    "creado_en": ahora,
                },
            )
            if id_nuevo is not None:
                registrar_auditoria(db, tabla=TABLA, id_registro=id_nuevo, accion=CREAR, **auditoria)
                continue
            # Otra petición la creó entre la lectura y el INSERT: se trata como edición.
            fila = _existentes(db, semana.id_semana, [registro.id_alumno])[registro.id_alumno]
        if fila.asistio == registro.asistio:
            continue
        registrar_auditoria(
            db, tabla=TABLA, id_registro=fila.id, accion=EDITAR,
            cambios={"asistio": (fila.asistio, registro.asistio)}, **auditoria,
        )
        fila.asistio = registro.asistio
        fila.id_docente = usuario.id_docente
        fila.fecha_registro = fecha_registro
        fila.modificado_por = usuario.id_usuario
        fila.modificado_en = ahora
        db.add(fila)
    db.commit()
    return AsistenciaGuardada(id_semana=semana.id_semana, registros=data.registros)
