"""Pantalla de Inicio de cada rol (CU010, CU011, CU012).

Los indicadores se calculan al consultar, sin procesos programados. Antes de contar
actividades se cierran las sesiones vencidas, para no contar como activa una actividad
de una sesión que ya expiró.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.organizacion import Alumno, Colegio, DocenteColegioGrado, PeriodoAcademico, Rol, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadActiva
from app.schemas.inicio import AlertaOperativa, InicioDirectivo, InicioDocente, InicioSupervisor
from app.services.actividad import (
    ACTIVIDAD_ACTIVA,
    actividad_activa,
    cerrar_vencidas_y_confirmar,
    impedimento_para_iniciar,
)
from app.services.asignacion_service import a_cargo
from app.services.calendario import cargar_dias_habiles, periodo_de_referencia_del_anio, periodo_vigente
from app.services.registros_vencidos import Clase, EstadoRegistros, estado_de_registros
from app.services.usuario_service import DOCENTE

# Alertas operativas de CU011.
SINCRONIZACION_OLVIDADA = "sincronizacion_olvidada"
INACTIVIDAD_OPERATIVA = "inactividad_operativa"
INCUMPLIMIENTO_SEDE = "incumplimiento_sede"
# Días hábiles seguidos sin actividad a partir de los cuales se alerta.
UMBRAL_DIAS_INACTIVIDAD = 4
# Sin iniciar sesión por más de este lapso, con clases vencidas: sincronización olvidada.
SIN_SESION_SINCRONIZACION = timedelta(hours=72)


# ── Docente ─────────────────────────────────────────────────────────────────────────

def inicio_docente(db: Session, usuario: Usuario, sesion: Sesion) -> InicioDocente:
    cerrar_vencidas_y_confirmar(db, usuario.id_usuario)
    asignaciones, totales = a_cargo(db, usuario.id_docente)
    activa = actividad_activa(db, usuario.id_docente)
    # El mismo criterio y orden que `iniciar_actividad`: el botón se habilita justo
    # cuando el inicio se aceptaría, y si no, el front muestra el mensaje del motivo.
    motivo = ACTIVIDAD_ACTIVA if activa is not None else impedimento_para_iniciar(db, usuario.id_docente)
    return InicioDocente(
        asignaciones=asignaciones,
        totales=totales,
        actividad_activa=None if activa is None else ActividadActiva(id=activa.id_actividad, inicio=activa.inicio),
        sesion_expira=sesion.expira,
        puede_iniciar_actividad=motivo is None,
        motivo_no_puede_iniciar=motivo,
    )


# ── Supervisor ──────────────────────────────────────────────────────────────────────

def _de_docentes_asignados(consulta, periodo: PeriodoAcademico):
    """Restringe `consulta` (sobre `Usuario`) a los usuarios activos con rol Docente que
    tienen al menos una asignación del periodo en un colegio activo (una fila por
    asignación: agrupar o contar distintos)."""
    return (
        consulta.join(Rol, Rol.id_rol == Usuario.id_rol)
        .join(DocenteColegioGrado, DocenteColegioGrado.id_docente == Usuario.id_docente)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            col(Usuario.activo).is_(True),
            Rol.nombre == DOCENTE,
            DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
            col(Colegio.activo).is_(True),
        )
    )


def docentes_activos(db: Session, periodo: Optional[PeriodoAcademico]) -> int:
    """CU011: docentes activos con asignación en el periodo vigente; 0 sin periodo."""
    if periodo is None:
        return 0
    return db.exec(
        _de_docentes_asignados(select(func.count(func.distinct(Usuario.id_usuario))), periodo)
    ).one()


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


def _nombre(usuario: Usuario) -> str:
    return f"{usuario.nombres} {usuario.apellidos}"


def _alerta(tipo: str, mensaje: str, critica: bool = False) -> AlertaOperativa:
    return AlertaOperativa(tipo=tipo, mensaje=mensaje, critica=critica)


def alertas_inactividad(
    db: Session, docentes: list[tuple[Usuario, datetime]], periodo: PeriodoAcademico, hoy: date
) -> list[AlertaOperativa]:
    """`inactividad_operativa`: docentes que llevan UMBRAL_DIAS_INACTIVIDAD o más días
    hábiles sin actividad.

    Se cuentan los días hábiles desde el más reciente de:
    - el día siguiente a su última actividad finalizada (`actividad.fin`);
    - el primer día del periodo;
    - el día siguiente al que quedó asignado a su colegio en el periodo (el `creado_en`
      más antiguo de sus asignaciones del periodo: las filas que no cambian se conservan,
      así que una rotación o una reactivación lo reinician y un cambio de grados no);
    hasta ayer inclusive: hoy todavía puede trabajar. Dos consultas, sin importar cuántos
    docentes haya.
    """
    ultimos_fines = dict(
        db.exec(
            select(Actividad.id_docente, func.max(Actividad.fin))
            .where(col(Actividad.id_docente).in_([u.id_docente for u, _ in docentes]), col(Actividad.fin).is_not(None))
            .group_by(Actividad.id_docente)
        ).all()
    )
    calendario = cargar_dias_habiles(db, periodo.fecha_inicio, hoy)
    un_dia = timedelta(days=1)

    alertas = []
    for usuario, asignado_en in docentes:
        ultimo_fin = ultimos_fines.get(usuario.id_docente)
        desde_dia = max(
            periodo.fecha_inicio,
            fecha_lima(asignado_en) + un_dia,
            fecha_lima(ultimo_fin) + un_dia if ultimo_fin is not None else periodo.fecha_inicio,
        )
        if calendario.contar(desde_dia, hoy) >= UMBRAL_DIAS_INACTIVIDAD:
            alertas.append(_alerta(
                INACTIVIDAD_OPERATIVA,
                f"Inactividad Operativa - {_nombre(usuario)}: Sin actividad registrada en los últimos "
                f"{UMBRAL_DIAS_INACTIVIDAD} días hábiles.",
            ))
    return alertas


def alertas_sincronizacion(
    db: Session, docentes: list[tuple[Usuario, datetime]], vencidas: list[Clase], ahora: datetime
) -> list[AlertaOperativa]:
    """`sincronizacion_olvidada`: docentes con al menos una clase vencida en sus grados
    cuyo último inicio de sesión fue hace más de SIN_SESION_SINCRONIZACION, o que nunca
    iniciaron sesión."""
    con_vencidas = {clase.id_docente for clase in vencidas}
    candidatos = [u for u, _ in docentes if u.id_docente in con_vencidas]
    if not candidatos:
        return []
    ultimas = dict(
        db.exec(
            select(Sesion.id_usuario, func.max(Sesion.inicio))
            .where(col(Sesion.id_usuario).in_([u.id_usuario for u in candidatos]))
            .group_by(Sesion.id_usuario)
        ).all()
    )
    limite = ahora - SIN_SESION_SINCRONIZACION
    return [
        _alerta(
            SINCRONIZACION_OLVIDADA,
            f"Sincronización Olvidada - {_nombre(u)}: Registros vencidos, último inicio de sesión hace más "
            "de 3 días.",
        )
        for u in candidatos
        if ultimas.get(u.id_usuario) is None or ultimas[u.id_usuario] < limite
    ]


def alertas_sede(db: Session, estado: EstadoRegistros) -> list[AlertaOperativa]:
    """`incumplimiento_sede` (crítica): colegios activos con al menos una clase vencida y
    ningún registro de asistencia en el periodo vigente."""
    sin_registros = {c.id_colegio for c in estado.vencidas} - estado.colegios_con_registros
    if not sin_registros:
        return []
    nombres = db.exec(
        select(Colegio.nombre).where(col(Colegio.id_colegio).in_(sin_registros)).order_by(Colegio.nombre)
    ).all()
    return [
        _alerta(
            INCUMPLIMIENTO_SEDE,
            f"Incumplimiento de Sede - Colegio {nombre}: Sin registros obligatorios en el periodo académico actual.",
            critica=True,
        )
        for nombre in nombres
    ]


def alertas_operativas(db: Session, periodo: PeriodoAcademico, estado: EstadoRegistros) -> list[AlertaOperativa]:
    """CU011: sincronización, inactividad y sede, en ese orden; dentro de cada tipo, por
    nombre del docente o del colegio."""
    docentes = sorted(
        db.exec(
            _de_docentes_asignados(select(Usuario, func.min(DocenteColegioGrado.creado_en)), periodo)
            .group_by(Usuario.id_usuario)
        ).all(),
        key=lambda fila: (fila[0].nombres, fila[0].apellidos, fila[0].id_usuario),
    )
    if not docentes:
        return []
    return [
        *alertas_sincronizacion(db, docentes, estado.vencidas, ahora_utc()),
        *alertas_inactividad(db, docentes, periodo, hoy_lima()),
        *alertas_sede(db, estado),
    ]


def inicio_supervisor(db: Session) -> InicioSupervisor:
    cerrar_vencidas_y_confirmar(db)
    periodo = periodo_vigente(db)
    colegios = db.exec(select(func.count()).select_from(Colegio).where(col(Colegio.activo).is_(True))).one()
    con_actividad = db.exec(
        select(func.count(func.distinct(Actividad.id_docente)))
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(col(Actividad.fin).is_(None), col(Sesion.fin).is_(None), Sesion.expira > ahora_utc())
    ).one()
    estado = estado_de_registros(db)
    return InicioSupervisor(
        colegios_registrados=colegios,
        docentes_activos=docentes_activos(db, periodo),
        docentes_con_actividad_activa=con_actividad,
        registros_vencidos=len(estado.vencidas),
        registros_incompletos=len(estado.incompletas),
        alertas=[] if periodo is None else alertas_operativas(db, periodo, estado),
    )


# ── Directivo ───────────────────────────────────────────────────────────────────────

def beneficiarios_activos(db: Session) -> int:
    """CU012: alumnos activos de colegios activos."""
    return db.exec(
        select(func.count())
        .select_from(Alumno)
        .join(Colegio, Colegio.id_colegio == Alumno.id_colegio)
        .where(col(Alumno.activo).is_(True), col(Colegio.activo).is_(True))
    ).one()


def colegios_operando(db: Session) -> int:
    """CU012: colegios activos con alguna asignación en el periodo de referencia (el
    vigente o, en un receso, el próximo o el último del año: no cae a 0 entre periodos)."""
    periodo = periodo_de_referencia_del_anio(db)
    if periodo is None:
        return 0
    colegios = _asignaciones_en(periodo, DocenteColegioGrado.id_colegio).distinct().subquery()
    return db.exec(select(func.count()).select_from(colegios)).one()


def inicio_directivo(db: Session) -> InicioDirectivo:
    return InicioDirectivo(beneficiarios_activos=beneficiarios_activos(db), colegios_operando=colegios_operando(db))
