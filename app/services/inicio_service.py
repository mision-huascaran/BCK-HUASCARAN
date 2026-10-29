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

from app.core.texto import comparable, comparable_sql
from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.organizacion import Alumno, Colegio, DocenteColegioGrado, PermisoCorreccion, Rol, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadActiva
from app.schemas.inicio import AlertaOperativa, InicioDirectivo, InicioDocente, InicioSupervisor, RegistrosPorModulo
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
from app.services.asignaciones_habilitadas import asignacion_habilitada_sql, habilitantes
from app.services.calendario import (
    Calendarios,
    PeriodoCalendario,
    cargar_calendarios,
    en_pa_de_su_colegio,
    pa_actual_de_colegios,
)
from app.services.permiso_service import vigentes_del_docente
from app.services.registros_vencidos import EstadoRegistros, RegistroObligatorio, estado_de_registros, por_modulo
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


def _de_docentes_activos(consulta):
    """Restringe `consulta` (sobre `Usuario`) a los usuarios activos con rol Docente."""
    return consulta.join(Rol, Rol.id_rol == Usuario.id_rol).where(col(Usuario.activo).is_(True), Rol.nombre == DOCENTE)


def _de_docentes_asignados(consulta, condicion):
    """Restringe `consulta` (sobre `Usuario`) a los usuarios activos con rol Docente con
    alguna asignación que cumple `condicion`, en un colegio activo (una fila por
    asignación: agrupar o contar distintos)."""
    return (
        _de_docentes_activos(consulta)
        .join(DocenteColegioGrado, DocenteColegioGrado.id_docente == Usuario.id_docente)
        .join(Colegio, Colegio.id_colegio == DocenteColegioGrado.id_colegio)
        .where(condicion, col(Colegio.activo).is_(True))
    )


def _con_permiso_vigente(consulta, ids_colegio: list[int]):
    """Restringe `consulta` (sobre `Usuario`) a los Docentes activos con un permiso de
    corrección vigente ahora (no revocado, inicio <= ahora < fin) en uno de esos colegios,
    si está activo."""
    ahora = ahora_utc()
    return (
        _de_docentes_activos(consulta)
        .join(PermisoCorreccion, PermisoCorreccion.id_docente == Usuario.id_docente)
        .join(Colegio, Colegio.id_colegio == PermisoCorreccion.id_colegio)
        .where(
            col(PermisoCorreccion.revocado_en).is_(None),
            PermisoCorreccion.inicio <= ahora,
            PermisoCorreccion.fin > ahora,
            col(PermisoCorreccion.id_colegio).in_(ids_colegio),
            col(Colegio.activo).is_(True),
        )
    )


def _en_receso_hoy(calendarios: Calendarios, ids_colegio: list[int], hoy: date) -> list[int]:
    """Los colegios cuyo calendario hoy está en un PR."""
    en_receso = []
    for id_colegio in ids_colegio:
        periodo = calendarios.periodo_de(id_colegio, hoy)
        if periodo is not None and not periodo.es_pa:
            en_receso.append(id_colegio)
    return en_receso


def _ids_colegios_activos(db: Session) -> list[int]:
    return list(db.exec(select(Colegio.id_colegio).where(col(Colegio.activo).is_(True))).all())


def docentes_activos(db: Session, calendarios: Calendarios) -> int:
    """CU011 (REGLA CONFIRMADA I1): docentes con cuenta activa y, según el calendario de
    cada colegio, alguna asignación habilitada hoy (en un PA, la de ese PA; en un PR, una
    retroactiva del PA anterior o una del PA inmediatamente siguiente) o, en un colegio
    que hoy está en un PR, un permiso de corrección vigente. Cada docente cuenta una vez."""
    hoy = hoy_lima()
    ids_colegio = _ids_colegios_activos(db)
    condicion = asignacion_habilitada_sql(habilitantes(calendarios, ids_colegio, hoy))
    asignados = _de_docentes_asignados(select(Usuario.id_usuario), condicion)
    con_permiso = _con_permiso_vigente(select(Usuario.id_usuario), _en_receso_hoy(calendarios, ids_colegio, hoy))
    return db.exec(select(func.count()).select_from(asignados.union(con_permiso).subquery())).one()


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


def dias_computables_sin_actividad(dias_por_colegio: list[list[date]], desde: date, hasta: date) -> int:
    """Función pura de la Inactividad Operativa: días computables (lectivos de un PA con
    asignación habilitada, ya resueltos por colegio) en [desde, hasta]. Un día de varios
    colegios cuenta una vez."""
    return len({dia for dias in dias_por_colegio for dia in dias if desde <= dia <= hasta})


def _dias_computables(
    calendarios: Calendarios, colegios: set[int], pa_actual: PaPorColegio, desde: date, hasta: date
) -> list[list[date]]:
    """Los días lectivos de cada colegio en el PA actual (sin PR, días no lectivos ni
    fechas fuera del PA: no se arrastran días de un PA anterior)."""
    return [
        calendarios.dias_lectivos(c, max(desde, pa_actual[c].fecha_inicio), min(hasta, pa_actual[c].fecha_fin))
        for c in colegios
    ]


def _con_actividad_en_curso(db: Session, ids_docente: list[int]) -> set[int]:
    return set(db.exec(
        select(Actividad.id_docente)
        .join(Sesion, Sesion.id_sesion == Actividad.id_sesion)
        .where(
            col(Actividad.id_docente).in_(ids_docente), col(Actividad.fin).is_(None), col(Sesion.fin).is_(None),
            Sesion.expira > ahora_utc(),
        )
    ).all())


def alertas_inactividad(
    db: Session,
    docentes: list[tuple[Usuario, datetime]],
    pa_actual: PaPorColegio,
    calendarios: Calendarios,
    hoy: date,
) -> list[AlertaOperativa]:
    """`inactividad_operativa`: docentes que acumulan UMBRAL_DIAS_INACTIVIDAD o más días
    hábiles computables seguidos sin iniciar una actividad.

    Día computable: lectivo (lunes a viernes, sin días no lectivos) dentro del PA actual
    de un colegio con asignación habilitada del docente, cada uno con su calendario; un
    día de varios colegios cuenta una vez. Los PR no cuentan: las retroactivas y los
    permisos no generan obligación. Se cuenta desde el más reciente de:
    - el día siguiente al último inicio de una actividad;
    - el primer día del PA actual (no se arrastran días de un PA anterior);
    - el día siguiente al de su habilitación (el `creado_en` más antiguo de sus
      asignaciones del periodo; una rotación o una reactivación lo reinician);
    hasta ayer inclusive (hoy todavía puede trabajar). No se muestra si tiene una
    actividad en curso. Tres consultas, sin importar cuántos docentes haya.
    """
    ids = [u.id_docente for u, _ in docentes]
    ultimos_inicios = dict(
        db.exec(
            select(Actividad.id_docente, func.max(Actividad.inicio))
            .where(col(Actividad.id_docente).in_(ids))
            .group_by(Actividad.id_docente)
        ).all()
    )
    en_curso = _con_actividad_en_curso(db, ids)
    colegios = _colegios_por_docente(db, ids, pa_actual)
    un_dia = timedelta(days=1)

    alertas = []
    for usuario, asignado_en in docentes:
        if usuario.id_docente in en_curso:
            continue
        ultimo_inicio = ultimos_inicios.get(usuario.id_docente)
        desde = fecha_lima(asignado_en) + un_dia
        if ultimo_inicio is not None:
            desde = max(desde, fecha_lima(ultimo_inicio) + un_dia)
        ayer = hoy - un_dia
        dias = dias_computables_sin_actividad(
            _dias_computables(calendarios, colegios[usuario.id_docente], pa_actual, desde, ayer), desde, ayer
        )
        if dias >= UMBRAL_DIAS_INACTIVIDAD:
            alertas.append(_alerta(
                INACTIVIDAD_OPERATIVA,
                f"Inactividad Operativa - {_nombre(usuario)}: Sin actividad registrada en los últimos "
                f"{UMBRAL_DIAS_INACTIVIDAD} días hábiles.",
            ))
    return alertas


def alertas_sincronizacion(
    db: Session, docentes: list[tuple[Usuario, datetime]], vencidas: list[RegistroObligatorio], ahora: datetime
) -> list[AlertaOperativa]:
    """`sincronizacion_olvidada`: docentes con al menos un registro vencido (de cualquiera
    de los cuatro módulos) cuyo último inicio de sesión fue hace más de
    SIN_SESION_SINCRONIZACION, o que nunca iniciaron sesión."""
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


def nombre_de_sede(nombre: str) -> str:
    """"Colegio Shilla" (CU011). Si el nombre ya empieza con la palabra "colegio" (sin
    distinguir mayúsculas ni tildes), queda tal cual: "Colegio de Prueba", no "Colegio
    Colegio de Prueba"."""
    palabras = comparable(nombre).split(maxsplit=1)
    return nombre if palabras and palabras[0] == "colegio" else f"Colegio {nombre}"


def alertas_sede(db: Session, estado: EstadoRegistros) -> list[AlertaOperativa]:
    """`incumplimiento_sede` (crítica): colegios activos con registros vencidos y ningún
    registro obligatorio consolidado (de ninguno de los cuatro módulos) en su PA actual."""
    sin_registros = {c.id_colegio for c in estado.vencidas} - estado.colegios_con_registros
    if not sin_registros:
        return []
    nombres = db.exec(
        select(Colegio.nombre).where(col(Colegio.id_colegio).in_(sin_registros))
        .order_by(comparable_sql(Colegio.nombre), Colegio.id_colegio)
    ).all()
    return [
        _alerta(
            INCUMPLIMIENTO_SEDE,
            f"Incumplimiento de Sede - {nombre_de_sede(nombre)}: Sin registros obligatorios en el periodo académico "
            "actual.",
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
            _de_docentes_asignados(
                select(Usuario, func.min(DocenteColegioGrado.creado_en)), _asignacion_en_pa_de_su_colegio(pa_actual)
            )
            .group_by(Usuario.id_usuario)
        ).all(),
        key=lambda fila: (comparable(fila[0].nombres), comparable(fila[0].apellidos), fila[0].id_usuario),
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
        docentes_activos=docentes_activos(db, calendarios),
        docentes_con_actividad_activa=con_actividad,
        registros_vencidos=len(estado.vencidas),
        registros_incompletos=len(estado.incompletas),
        vencidos_por_modulo=RegistrosPorModulo(**por_modulo(estado.vencidas)),
        incompletos_por_modulo=RegistrosPorModulo(**por_modulo(estado.incompletas)),
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


def colegios_operando(db: Session) -> int:
    """CU012: colegios activos (cada uno una vez) con un docente activo y una asignación
    que hoy cuenta según su calendario: en un PA, la de ese PA; en un PR intermedio, una
    retroactiva habilitada del PA anterior o una del PA siguiente; si el año ya cerró
    (PR4 o después), las del último PA."""
    calendarios = cargar_calendarios(db)
    condicion = asignacion_habilitada_sql(
        habilitantes(calendarios, _ids_colegios_activos(db), hoy_lima(), cierre_usa_ultimo_pa=True)
    )
    colegios = _de_docentes_asignados(select(DocenteColegioGrado.id_colegio).select_from(Usuario), condicion).distinct()
    return db.exec(select(func.count()).select_from(colegios.subquery())).one()


def inicio_directivo(db: Session) -> InicioDirectivo:
    return InicioDirectivo(
        beneficiarios_activos=beneficiarios_activos(db),
        colegios_operando=colegios_operando(db),
        salud_sistema=salud_sistema(db),
    )
