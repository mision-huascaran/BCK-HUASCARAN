"""Pantalla de Inicio de cada rol (CU010, CU011, CU012).

Los indicadores se calculan al consultar, sin procesos programados. Antes de contar
actividades se cierran las sesiones vencidas, para no contar como activa una actividad
de una sesión que ya expiró.

"Periodo vigente" es siempre el PA en que hoy está cada colegio según su calendario
(CU027): no se presume un único periodo para todos. Los calendarios se cargan una sola
vez por petición.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.organizacion import Alumno, Colegio, DocenteColegioGrado, Rol, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadActiva
from app.schemas.inicio import AlertaOperativa, InicioDirectivo, InicioDocente, InicioSupervisor
from app.services.actividad import (
    ACTIVIDAD_ACTIVA,
    MENSAJE_ACTIVIDAD_ACTIVA,
    MENSAJE_SIN_HABILITACIONES,
    SIN_ASIGNACIONES,
    actividad_activa,
    cerrar_vencidas_y_confirmar,
    impedimento_para_iniciar,
)
from app.services.asignacion_service import a_cargo
from app.services.calendario import (
    Calendarios,
    PeriodoCalendario,
    anio_de_referencia,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
)
from app.services.permiso_service import vigentes_del_docente
from app.services.registros_vencidos import Clase, EstadoRegistros, estado_de_registros
from app.services.salud_sistema import salud_sistema
from app.services.sincronizacion import pendientes_del_usuario
from app.services.usuario_service import DOCENTE

MENSAJES_NO_PUEDE_INICIAR = {
    ACTIVIDAD_ACTIVA: MENSAJE_ACTIVIDAD_ACTIVA,
    SIN_ASIGNACIONES: MENSAJE_SIN_HABILITACIONES,
}

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
    asignaciones, totales = a_cargo(db, usuario)
    activa = actividad_activa(db, usuario.id_docente)
    # El mismo criterio y orden que `iniciar_actividad`: el botón se habilita justo
    # cuando el inicio se aceptaría, y si no, el front muestra el mensaje del motivo.
    motivo = ACTIVIDAD_ACTIVA if activa is not None else impedimento_para_iniciar(db, usuario)
    return InicioDocente(
        asignaciones=asignaciones,
        totales=totales,
        actividad_activa=None if activa is None else ActividadActiva(id=activa.id_actividad, inicio=activa.inicio),
        sesion_expira=sesion.expira,
        puede_iniciar_actividad=motivo is None,
        motivo_no_puede_iniciar=motivo,
        motivo_no_puede=MENSAJES_NO_PUEDE_INICIAR.get(motivo),
        permisos_vigentes=vigentes_del_docente(db, usuario),
        registros_offline_pendientes=pendientes_del_usuario(db, usuario.id_usuario),
    )


# ── Supervisor ──────────────────────────────────────────────────────────────────────

PaPorColegio = dict[int, PeriodoCalendario]


def _asignacion_en_pa_de_su_colegio(pa_por_colegio: PaPorColegio):
    return en_pa_de_su_colegio(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_periodo_academico, pa_por_colegio)


def _de_docentes_asignados(consulta, pa_actual: PaPorColegio):
    """Restringe `consulta` (sobre `Usuario`) a los usuarios activos con rol Docente que
    tienen al menos una asignación del PA actual de su colegio, en un colegio activo (una
    fila por asignación: agrupar o contar distintos)."""
    return (
        consulta.join(Rol, Rol.id_rol == Usuario.id_rol)
        .join(DocenteColegioGrado, DocenteColegioGrado.id_docente == Usuario.id_docente)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(
            col(Usuario.activo).is_(True),
            Rol.nombre == DOCENTE,
            _asignacion_en_pa_de_su_colegio(pa_actual),
            col(Colegio.activo).is_(True),
        )
    )


def docentes_activos(db: Session, pa_actual: PaPorColegio) -> int:
    """CU011: docentes activos con asignación del PA actual de su colegio; 0 si ningún
    colegio está en un PA."""
    if not pa_actual:
        return 0
    return db.exec(
        _de_docentes_asignados(select(func.count(func.distinct(Usuario.id_usuario))), pa_actual)
    ).one()


def _nombre(usuario: Usuario) -> str:
    return f"{usuario.nombres} {usuario.apellidos}"


def _alerta(tipo: str, mensaje: str, critica: bool = False) -> AlertaOperativa:
    return AlertaOperativa(tipo=tipo, mensaje=mensaje, critica=critica)


def _colegios_por_docente(db: Session, ids_docente: list[int], pa_actual: PaPorColegio) -> dict[int, set[int]]:
    """Colegios de cada docente con asignación del PA actual de ese colegio."""
    por_docente: dict[int, set[int]] = defaultdict(set)
    for id_docente, id_colegio in db.exec(
        select(DocenteColegioGrado.id_docente, DocenteColegioGrado.id_colegio).where(
            col(DocenteColegioGrado.id_docente).in_(ids_docente), _asignacion_en_pa_de_su_colegio(pa_actual)
        )
    ).all():
        por_docente[id_docente].add(id_colegio)
    return por_docente


def _dias_habiles_sin_actividad(
    calendarios: Calendarios, colegios: set[int], pa_actual: PaPorColegio, desde: date, hoy: date
) -> int:
    """Días lectivos en [desde, ayer] dentro del PA actual de alguno de sus colegios, cada
    uno con su calendario. Un día lectivo en varios colegios cuenta una sola vez."""
    dias: set[date] = set()
    for id_colegio in colegios:
        pa = pa_actual[id_colegio]
        dias.update(calendarios.dias_lectivos(id_colegio, max(desde, pa.fecha_inicio), hoy - timedelta(days=1)))
    return len(dias)


def alertas_inactividad(
    db: Session,
    docentes: list[tuple[Usuario, datetime]],
    pa_actual: PaPorColegio,
    calendarios: Calendarios,
    hoy: date,
) -> list[AlertaOperativa]:
    """`inactividad_operativa`: docentes que llevan UMBRAL_DIAS_INACTIVIDAD o más días
    hábiles sin actividad.

    Se cuentan los días lectivos (calendario de cada colegio) desde el más reciente de:
    - el día siguiente a su última actividad finalizada (`actividad.fin`);
    - el primer día del PA actual de su colegio;
    - el día siguiente al que quedó asignado a su colegio en el periodo (el `creado_en`
      más antiguo de sus asignaciones del periodo: las filas que no cambian se conservan,
      así que una rotación o una reactivación lo reinician y un cambio de grados no);
    hasta ayer inclusive: hoy todavía puede trabajar. Tres consultas, sin importar cuántos
    docentes haya. Las reglas nuevas de CU011 (inactividad computable) llegan en T10.
    """
    ids = [u.id_docente for u, _ in docentes]
    ultimos_fines = dict(
        db.exec(
            select(Actividad.id_docente, func.max(Actividad.fin))
            .where(col(Actividad.id_docente).in_(ids), col(Actividad.fin).is_not(None))
            .group_by(Actividad.id_docente)
        ).all()
    )
    colegios = _colegios_por_docente(db, ids, pa_actual)
    un_dia = timedelta(days=1)

    alertas = []
    for usuario, asignado_en in docentes:
        ultimo_fin = ultimos_fines.get(usuario.id_docente)
        desde = fecha_lima(asignado_en) + un_dia
        if ultimo_fin is not None:
            desde = max(desde, fecha_lima(ultimo_fin) + un_dia)
        dias = _dias_habiles_sin_actividad(calendarios, colegios[usuario.id_docente], pa_actual, desde, hoy)
        if dias >= UMBRAL_DIAS_INACTIVIDAD:
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
    ningún registro de asistencia en su PA actual."""
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


def alertas_operativas(
    db: Session, pa_actual: PaPorColegio, calendarios: Calendarios, estado: EstadoRegistros
) -> list[AlertaOperativa]:
    """CU011: sincronización, inactividad y sede, en ese orden; dentro de cada tipo, por
    nombre del docente o del colegio."""
    if not pa_actual:
        return []
    docentes = sorted(
        db.exec(
            _de_docentes_asignados(select(Usuario, func.min(DocenteColegioGrado.creado_en)), pa_actual)
            .group_by(Usuario.id_usuario)
        ).all(),
        key=lambda fila: (fila[0].nombres, fila[0].apellidos, fila[0].id_usuario),
    )
    if not docentes:
        return []
    return [
        *alertas_sincronizacion(db, docentes, estado.vencidas, ahora_utc()),
        *alertas_inactividad(db, docentes, pa_actual, calendarios, hoy_lima()),
        *alertas_sede(db, estado),
    ]


def inicio_supervisor(db: Session) -> InicioSupervisor:
    cerrar_vencidas_y_confirmar(db)
    calendarios = cargar_calendarios(db)
    pa_actual = pa_actual_de_colegios(db, solo_activos=True, calendarios=calendarios)
    colegios = db.exec(select(func.count()).select_from(Colegio).where(col(Colegio.activo).is_(True))).one()
    con_actividad = db.exec(
        select(func.count(func.distinct(Actividad.id_docente)))
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(col(Actividad.fin).is_(None), col(Sesion.fin).is_(None), Sesion.expira > ahora_utc())
    ).one()
    estado = estado_de_registros(db, calendarios)
    return InicioSupervisor(
        colegios_registrados=colegios,
        docentes_activos=docentes_activos(db, pa_actual),
        docentes_con_actividad_activa=con_actividad,
        registros_vencidos=len(estado.vencidas),
        registros_incompletos=len(estado.incompletas),
        alertas=alertas_operativas(db, pa_actual, calendarios, estado),
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


def pa_de_referencia_por_colegio(db: Session) -> PaPorColegio:
    """{colegio activo: PA de referencia del año en curso según su calendario}: el que
    contiene hoy o, en un receso, el próximo o el último del año (no cae a 0 entre
    periodos). Los colegios sin calendario para ese año no aparecen."""
    anio = anio_de_referencia(db)
    if anio is None:
        return {}
    calendarios = cargar_calendarios(db, anio.id_anio_escolar)
    hoy = hoy_lima()
    referencia = {}
    for id_colegio in db.exec(select(Colegio.id_colegio).where(col(Colegio.activo).is_(True))).all():
        calendario = calendarios.aplicable(id_colegio, anio.id_anio_escolar)
        pa = calendario.pa_de_referencia(hoy) if calendario is not None else None
        if pa is not None:
            referencia[id_colegio] = pa
    return referencia


def colegios_operando(db: Session) -> int:
    """CU012: colegios activos con alguna asignación del PA de referencia de su
    calendario (ver `pa_de_referencia_por_colegio`)."""
    referencia = pa_de_referencia_por_colegio(db)
    if not referencia:
        return 0
    colegios = select(DocenteColegioGrado.id_colegio).where(_asignacion_en_pa_de_su_colegio(referencia)).distinct()
    return db.exec(select(func.count()).select_from(colegios.subquery())).one()


def inicio_directivo(db: Session) -> InicioDirectivo:
    return InicioDirectivo(
        beneficiarios_activos=beneficiarios_activos(db),
        colegios_operando=colegios_operando(db),
        salud_sistema=salud_sistema(db),
    )
