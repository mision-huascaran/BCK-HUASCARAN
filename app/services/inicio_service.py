"""Pantalla de Inicio de cada rol (CU010, CU011, CU012).

Los indicadores se calculan al consultar, sin procesos programados. Antes de contar
actividades se cierran las sesiones vencidas, para no contar como activa una actividad
de una sesión que ya expiró.
"""
from datetime import date, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima, limites_lima
from app.models.organizacion import Alumno, Colegio, DocenteColegioGrado, PeriodoAcademico, Rol, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadActiva
from app.schemas.inicio import AlertaInactividad, InicioDirectivo, InicioDocente, InicioSupervisor
from app.services.actividad import actividad_activa, cerrar_vencidas_y_confirmar
from app.services.asignacion_service import mis_asignaciones
from app.services.calendario import cargar_dias_habiles, periodo_vigente
from app.services.usuario_service import DOCENTE

# CU011: días hábiles seguidos sin iniciar actividad a partir de los cuales se alerta.
UMBRAL_DIAS_INACTIVIDAD = 4


# ── Docente ─────────────────────────────────────────────────────────────────────────

def inicio_docente(db: Session, usuario: Usuario, sesion: Sesion) -> InicioDocente:
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    asignaciones = mis_asignaciones(db, usuario.id_docente) if usuario.id_docente is not None else []
    activa = actividad_activa(db, usuario.id_docente)
    return InicioDocente(
        asignaciones=asignaciones,
        actividad_activa=None if activa is None else ActividadActiva(id=activa.id_actividad, inicio=activa.inicio),
        sesion_expira=sesion.expira,
        puede_iniciar_actividad=activa is None and bool(asignaciones),
    )


# ── Supervisor ──────────────────────────────────────────────────────────────────────

def _docentes_activos():
    """Usuarios activos con rol Docente."""
    return (
        select(Usuario)
        .join(Rol, Rol.id_rol == Usuario.id_rol)
        .where(col(Usuario.activo).is_(True), Rol.nombre == DOCENTE)
    )


def _asignaciones_en(periodo: PeriodoAcademico, columna):
    """`columna` de las asignaciones del periodo en colegios activos."""
    return (
        select(columna)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
            col(Colegio.activo).is_(True),
        )
    )


def alertas_inactividad(db: Session, periodo: PeriodoAcademico, hoy: date) -> list[AlertaInactividad]:
    """CU011: docentes activos con asignación vigente que llevan UMBRAL_DIAS_INACTIVIDAD o
    más días hábiles sin iniciar actividad.

    Referencia: el día (en Lima) de su último inicio de actividad dentro del periodo o, si
    no tiene ninguno, el día anterior al inicio del periodo (así el primer día cuenta). Se
    cuentan los días hábiles desde el día siguiente a la referencia hasta ayer: hoy todavía
    puede iniciar. Tres consultas en total, sin importar cuántos docentes haya.
    """
    docentes = db.exec(
        _docentes_activos().where(col(Usuario.id_docente).in_(_asignaciones_en(periodo, DocenteColegioGrado.id_docente)))
    ).all()
    if not docentes:
        return []
    desde, hasta = limites_lima(periodo.fecha_inicio, periodo.fecha_fin)
    ultimos_inicios = dict(
        db.exec(
            select(Actividad.id_docente, func.max(Actividad.inicio))
            .where(
                col(Actividad.id_docente).in_([u.id_docente for u in docentes]),
                Actividad.inicio >= desde,
                Actividad.inicio < hasta,
            )
            .group_by(Actividad.id_docente)
        ).all()
    )
    sin_actividad = periodo.fecha_inicio - timedelta(days=1)
    calendario = cargar_dias_habiles(db, periodo.fecha_inicio, hoy)

    alertas = []
    for usuario in docentes:
        ultimo = ultimos_inicios.get(usuario.id_docente)
        referencia = sin_actividad if ultimo is None else fecha_lima(ultimo)
        dias = calendario.contar(referencia + timedelta(days=1), hoy)
        if dias >= UMBRAL_DIAS_INACTIVIDAD:
            nombre = f"{usuario.nombres} {usuario.apellidos}"
            alertas.append(
                AlertaInactividad(
                    id_docente=usuario.id_docente,
                    docente=nombre,
                    dias_habiles=dias,
                    mensaje=f"Atención: El docente {nombre} lleva {dias} días hábiles sin iniciar actividad.",
                )
            )
    alertas.sort(key=lambda a: (-a.dias_habiles, a.docente))
    return alertas


def inicio_supervisor(db: Session) -> InicioSupervisor:
    cerrar_vencidas_y_confirmar(db)
    colegios = db.exec(select(func.count()).select_from(Colegio).where(col(Colegio.activo).is_(True))).one()
    docentes = db.exec(select(func.count()).select_from(_docentes_activos().subquery())).one()
    con_actividad = db.exec(
        select(func.count(func.distinct(Actividad.id_docente)))
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(col(Actividad.fin).is_(None), col(Sesion.fin).is_(None), Sesion.expira > ahora_utc())
    ).one()
    periodo = periodo_vigente(db)
    return InicioSupervisor(
        colegios_registrados=colegios,
        docentes_activos=docentes,
        docentes_con_actividad_activa=con_actividad,
        alertas=[] if periodo is None else alertas_inactividad(db, periodo, hoy_lima()),
    )


# ── Directivo ───────────────────────────────────────────────────────────────────────

def inicio_directivo(db: Session) -> InicioDirectivo:
    beneficiarios = db.exec(select(func.count()).select_from(Alumno).where(col(Alumno.activo).is_(True))).one()
    periodo = periodo_vigente(db)
    operando = 0
    if periodo is not None:
        colegios = _asignaciones_en(periodo, DocenteColegioGrado.id_colegio).distinct().subquery()
        operando = db.exec(select(func.count()).select_from(colegios)).one()
    return InicioDirectivo(beneficiarios_activos=beneficiarios, colegios_operando=operando)
