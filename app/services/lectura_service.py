"""Seguimiento de Lectura (CU023 a CU026).

Una tabla por grupo (colegio × grado × sección), subprograma y semana (lunes a viernes),
atada a una clase de referencia del grupo en esa semana:

- Por defecto, la habitual o reprogramada cuya fecha vigente cae en la semana; una
  adicional no la reemplaza sola, pero el Docente puede elegirla mientras la tabla no
  tenga datos de lectura ni esté "No realizada". El `mes` de la tabla es el de su clase.
- Al crear la tabla, si la clase no tiene fila se crea y se congela su lista en la misma
  transacción; hay una fila por cada alumno de esa lista con el subprograma de la tabla.
- La asistencia de la clase de referencia manda: presente o tardanza habilitan los datos
  de lectura; ausente deja la fila en "No aplica"; sin asistencia, "Pendiente".
- "No realizada" (CU026): para toda la tabla, con motivo, solo si ninguna fila tiene
  datos de lectura; bloquea los datos de lectura y las observaciones.

Lectura: el Docente en su alcance de lectura; el Supervisor, de todo y en solo lectura.
Cada escritura es una operación (`procesar_operacion`) del Docente, con actividad y
habilitación del módulo `lectura` para el PA de la clase de referencia, evaluada a la
hora real. Lectura lleva control de versión: una `version` distinta de la guardada se
rechaza (`modificacion_concurrente`) sin sobrescribir.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import and_, case, delete, func, insert, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio, error_de_campo
from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.evaluacion import (
    ESTADOS_ASISTIO,
    LECTURA_NO_REALIZADA,
    LECTURA_NORMAL,
    MAXIMOS_LSN,
    TIPO_HABITUAL,
    Asistencia,
    Clase,
    ClaseAlumno,
    LecturaSemanal,
    LecturaSemanalAlumno,
    SemanaReporte,
)
from app.models.organizacion import Alumno, AnioEscolar, Programa, Usuario
from app.schemas.asistencia import Escritura
from app.schemas.catalogos import SemanaResponse
from app.schemas.lectura import (
    ClaseReferencia,
    ClaseReferenciaCambiar,
    FilaLectura,
    FilaLecturaGuardar,
    NoRealizadaMarcar,
    TablaLectura,
    TablaLecturaCrear,
)
from app.schemas.sincronizacion import ResultadoOperacion
from app.services.asistencia_service import exigir_seccion
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import cargar_calendarios, en_pa_de_su_colegio, rango_del_mes
from app.services.clase_service import colegio_existente, grados_visibles
from app.services.escritura import Destino, MensajesEscritura, escritura
from app.services.habilitacion import habilitacion_escritura
from app.services.nivel_academico import nivel_academico
from app.services.programacion import (
    ClaseProgramada,
    FiltroGrupos,
    clases_programadas,
    congelar_lista,
    fila_de_programada,
)
from app.services.sincronizacion import (
    OPERACION_AJENA,
    RECHAZADA,
    ContextoOperacion,
    Operacion,
    RechazoOperacion,
    procesar_operacion,
)

MODULO = "lectura"
TABLA = "lectura_semanal"
TABLA_FILA = "lectura_semanal_alumno"
CAMPOS_LSN = ("lsn_titulo", "lsn_nota", "lsn_maximo")
CAMPOS_LECTURA = (*CAMPOS_LSN, "cantidad_lsl")
CAMPOS_FILA = (*CAMPOS_LECTURA, "observacion")
AUSENTE = "ausente"

COMPLETADA = "completada"
PENDIENTE = "pendiente"
NO_APLICA = "no_aplica"

# Motivos de rechazo.
SEMANA_SIN_CLASE = "semana_sin_clase"
SEMANA_FUTURA = "semana_futura"
CLASE_NO_REALIZADA = "clase_no_realizada"
CLASE_FUERA_DE_LA_SEMANA = "clase_fuera_de_la_semana"
CLASE_REFERENCIA_REQUERIDA = "clase_referencia_requerida"
CLASE_YA_USADA = "clase_ya_usada"
TABLA_EXISTENTE = "tabla_existente"
TABLA_CON_DATOS = "tabla_con_datos"
SIN_ALUMNOS = "sin_alumnos"
SIN_HABILITACION = "sin_habilitacion"
SIN_ASISTENCIA = "sin_asistencia"
LSN_INCOMPLETO = "lsn_incompleto"
LSN_INVALIDO = "lsn_invalido"
ALUMNO_FUERA_DE_LA_TABLA = "alumno_fuera_de_la_tabla"
NO_REALIZADA = "lectura_no_realizada"
MODIFICACION_CONCURRENTE = "modificacion_concurrente"
# Los rechazos que se responden 409 (el resto, 422).
CONFLICTOS = frozenset({
    TABLA_EXISTENTE, TABLA_CON_DATOS, CLASE_YA_USADA, NO_REALIZADA, MODIFICACION_CONCURRENTE, OPERACION_AJENA,
})

MENSAJE_TABLA_NO_EXISTE = "No existe una tabla de lectura semanal para los filtros seleccionados."
MENSAJE_TABLA_NO_ENCONTRADA = "La tabla de lectura semanal no existe."
MENSAJE_SEMANA_NO_ENCONTRADA = "La semana no existe en el año escolar seleccionado."
MENSAJE_ANIO_NO_ENCONTRADO = "El año escolar no existe."
MENSAJE_PROGRAMA_INEXISTENTE = "El subprograma no existe."
MENSAJE_SEMANA_SIN_CLASE = "El grupo no tiene clases en la semana y mes seleccionados: no hay lectura que registrar."
MENSAJE_TABLA_EXISTENTE = "Ya existe una tabla de lectura semanal para los filtros seleccionados."
MENSAJE_SIN_ALUMNOS = "No hay estudiantes que pertenecieran a este grupo en la fecha de la clase seleccionada."
MENSAJE_SEMANA_FUTURA = "No es posible adelantar registros. Seleccione la semana actual o una anterior."
MENSAJE_CLASE_NO_REALIZADA = "La clase de referencia todavía no se ha realizado."
MENSAJE_CLASE_FUERA = "La clase seleccionada no es de este grupo o de esta semana."
MENSAJE_CLASE_REQUERIDA = "La semana no tiene una clase programada: selecciona la clase de referencia."
MENSAJE_CLASE_YA_USADA = "La clase seleccionada ya es la clase de referencia de otra tabla de lectura de este subprograma."
MENSAJE_CAMBIO_CON_DATOS = (
    "No es posible cambiar la clase de referencia porque ya existen registros de lectura guardados para esta tabla."
)
MENSAJE_TABLA_CONCURRENTE = (
    "Esta tabla de lectura semanal fue modificada desde que la consultaste. Revisa la versión actual antes de "
    "guardar nuevamente."
)
MENSAJE_FILA_CONCURRENTE = (
    "Este registro de lectura fue modificado desde que lo consultaste. Revisa la versión actual antes de guardar "
    "nuevamente."
)
MENSAJE_SIN_HABILITACION_CREAR = "No tienes una habilitación vigente para crear la tabla de lectura de esta semana."
MENSAJE_SIN_ACTIVIDAD_CREAR = "Actividad no iniciada. Inicie su jornada para registrar información."
MENSAJE_SIN_ASISTENCIA = (
    "El estudiante debe tener asistencia Presente o Tardanza en la clase de referencia para registrar su lectura."
)
MENSAJE_LSN_INCOMPLETO = "Para registrar un LSN, completa el título, la nota obtenida y el puntaje máximo."
MENSAJE_LSN_INVALIDO = "Selecciona un máximo de 5 o 10 e ingresa una nota entre 0 y ese máximo."
MENSAJE_ALUMNO_FUERA = "El estudiante no corresponde a la clase de referencia de esta tabla semanal."
MENSAJE_SIN_HABILITACION_FILAS = (
    "No tienes una habilitación vigente para registrar o corregir la lectura de esta semana."
)
MENSAJE_SIN_ACTIVIDAD_FILAS = "Debes iniciar una actividad de trabajo para registrar o modificar la lectura semanal."
MENSAJE_NO_REALIZADA = "La lectura semanal de esta tabla fue marcada como no realizada."
MENSAJE_MOTIVO_OBLIGATORIO = (
    "Es obligatorio registrar una justificación para indicar por qué no se realizó la lectura semanal."
)
MENSAJE_NO_REALIZADA_CON_DATOS = (
    "No es posible marcar la lectura semanal como no realizada porque ya existen registros de lectura guardados "
    "para esta tabla."
)
MENSAJE_SIN_HABILITACION_NO_REALIZADA = (
    "No tienes una habilitación vigente para marcar la lectura semanal como no realizada en la semana seleccionada."
)
MENSAJE_SIN_ACTIVIDAD_NO_REALIZADA = (
    "Debes iniciar una actividad de trabajo para marcar la lectura semanal como no realizada."
)
MENSAJE_SOLO_LECTURA = "El Supervisor consulta la lectura semanal en solo lectura."

MENSAJES_ESCRITURA = MensajesEscritura(MENSAJE_SOLO_LECTURA, MENSAJE_SIN_HABILITACION_FILAS, MENSAJE_SIN_ACTIVIDAD_FILAS)

_tabla = LecturaSemanal.__table__
_fila = LecturaSemanalAlumno.__table__


# ── Cálculos (también los usan el detalle del alumno y la Salud del Sistema) ─────────

def tiene_lsn(fila: LecturaSemanalAlumno) -> bool:
    """Un LSN guardado siempre está completo (todo o nada)."""
    return fila.lsn_titulo is not None


def total_libros(fila: LecturaSemanalAlumno) -> int:
    """(1 si hay LSN) + LSL; un LSL vacío suma 0."""
    return int(tiene_lsn(fila)) + (fila.cantidad_lsl or 0)


def tiene_datos_de_lectura(fila: LecturaSemanalAlumno) -> bool:
    """LSN o LSL guardados (la observación no cuenta)."""
    return any(getattr(fila, campo) is not None for campo in CAMPOS_LECTURA)


def estado_fila(estado_tabla: str, asistencia: Optional[str], fila: LecturaSemanalAlumno) -> str:
    """`no_aplica` si la tabla no se realizó o el alumno faltó; `completada` si asistió y
    tiene el LSL (el LSN es completo o vacío); si no, `pendiente`."""
    if estado_tabla == LECTURA_NO_REALIZADA or asistencia == AUSENTE:
        return NO_APLICA
    if asistencia in ESTADOS_ASISTIO and fila.cantidad_lsl is not None:
        return COMPLETADA
    return PENDIENTE


def revisar_asistencia(asistencia: Optional[str], fila: LecturaSemanalAlumno) -> bool:
    """Tiene datos de lectura guardados, pero hoy la asistencia es ausente (no se borra
    nada: el Docente revisa)."""
    return asistencia == AUSENTE and tiene_datos_de_lectura(fila)


def total_libros_sql() -> ColumnElement[int]:
    return case((col(LecturaSemanalAlumno.lsn_titulo).is_not(None), 1), else_=0) + func.coalesce(
        LecturaSemanalAlumno.cantidad_lsl, 0
    )


def _asistencia_de_la_referencia():
    return (Asistencia.id_clase == LecturaSemanal.id_clase_referencia) & (
        Asistencia.id_alumno == LecturaSemanalAlumno.id_alumno
    )


def filas_confirmadas(db: Session, pa_por_colegio: dict) -> int:
    """Salud del Sistema (CU012): filas `completada` o `no_aplica` de las tablas cuya
    clase de referencia es del PA vigente de su colegio."""
    asistio = col(Asistencia.estado).in_(ESTADOS_ASISTIO)
    confirmada = or_(
        LecturaSemanal.estado == LECTURA_NO_REALIZADA,
        Asistencia.estado == AUSENTE,
        and_(asistio, col(LecturaSemanalAlumno.cantidad_lsl).is_not(None)),
    )
    return db.exec(
        select(func.count())
        .select_from(LecturaSemanalAlumno)
        .join(LecturaSemanal, LecturaSemanal.id == LecturaSemanalAlumno.id_lectura_semanal)
        .join(Clase, Clase.id_clase == LecturaSemanal.id_clase_referencia)
        .outerjoin(Asistencia, _asistencia_de_la_referencia())
        .where(confirmada, en_pa_de_su_colegio(Clase.id_colegio, Clase.id_periodo_academico, pa_por_colegio))
    ).one()


# ── Filtros, semana y clase de referencia ───────────────────────────────────────────

@dataclass(frozen=True)
class FiltroLectura:
    id_colegio: int
    id_programa: int
    id_grado: int
    id_seccion: int
    id_anio_escolar: int
    mes: int
    id_semana: int

    @property
    def grupo(self) -> FiltroGrupos:
        return FiltroGrupos(self.id_colegio, self.id_grado, self.id_seccion)

    def columnas(self) -> dict[str, int]:
        return dict(self.__dict__)

    def clave(self) -> str:
        return "-".join(str(v) for v in self.__dict__.values())

    @classmethod
    def de(cls, origen) -> "FiltroLectura":
        return cls(**{campo: getattr(origen, campo) for campo in cls.__dataclass_fields__})


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def _no_encontrado(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_404_NOT_FOUND, detail, motivo=motivo)


def _validar_grupo(db: Session, usuario: Usuario, grupo: FiltroGrupos) -> None:
    colegio_existente(db, grupo.id_colegio)
    grados_visibles(db, usuario, grupo.id_colegio, grupo.id_grado)
    exigir_seccion(db, grupo)


def _validar_filtro(db: Session, usuario: Usuario, filtro: FiltroLectura) -> SemanaReporte:
    """403 `aula_fuera_de_alcance`, 404 `colegio_no_encontrado` y `semana_no_encontrada`,
    422 `seccion_inexistente` y `programa_inexistente`. Devuelve la semana."""
    _validar_grupo(db, usuario, filtro.grupo)
    if db.get(Programa, filtro.id_programa) is None:
        raise _no_procesable(MENSAJE_PROGRAMA_INEXISTENTE, "programa_inexistente")
    semana = db.get(SemanaReporte, filtro.id_semana)
    if semana is None or semana.id_anio_escolar != filtro.id_anio_escolar:
        raise _no_encontrado(MENSAJE_SEMANA_NO_ENCONTRADA, "semana_no_encontrada")
    return semana


def _clases_de_la_semana(db: Session, filtro: FiltroLectura, semana: SemanaReporte) -> list[ClaseProgramada]:
    """Clases del grupo en la semana con fecha en el mes de la tabla."""
    clases = clases_programadas(db, filtro.grupo, semana.fecha_inicio, semana.fecha_fin)
    return [c for c in clases if c.fecha.month == filtro.mes]


def _sugerida(clases: list[ClaseProgramada]) -> Optional[ClaseProgramada]:
    """La habitual o reprogramada de la semana (una adicional no la reemplaza sola)."""
    return next((c for c in clases if c.tipo == TIPO_HABITUAL), None)


def _coincide(clase: ClaseProgramada, id_clase: Optional[int], fecha: Optional[date]) -> bool:
    return clase.id_clase == id_clase if id_clase is not None else clase.fecha == fecha


def _elegida(clases: list[ClaseProgramada], id_clase: Optional[int], fecha: Optional[date]) -> ClaseProgramada:
    if not clases:
        raise RechazoOperacion(SEMANA_SIN_CLASE, MENSAJE_SEMANA_SIN_CLASE)
    if id_clase is None and fecha is None:
        sugerida = _sugerida(clases)
        if sugerida is None:
            raise RechazoOperacion(CLASE_REFERENCIA_REQUERIDA, MENSAJE_CLASE_REQUERIDA)
        return sugerida
    elegida = next((c for c in clases if _coincide(c, id_clase, fecha)), None)
    if elegida is None:
        raise RechazoOperacion(CLASE_FUERA_DE_LA_SEMANA, MENSAJE_CLASE_FUERA)
    return elegida


def _referencia(clase: ClaseProgramada | Clase) -> ClaseReferencia:
    return ClaseReferencia(
        id_clase=clase.id_clase, fecha=clase.fecha, tipo=clase.tipo,
        reprogramada=clase.tipo == TIPO_HABITUAL and clase.fecha != clase.fecha_original,
    )


def _tabla_por_filtro(db: Session, filtro: FiltroLectura) -> Optional[LecturaSemanal]:
    return db.exec(
        select(LecturaSemanal).where(*(getattr(LecturaSemanal, c) == v for c, v in filtro.columnas().items()))
    ).first()


def _filtro_de(tabla: LecturaSemanal) -> FiltroLectura:
    return FiltroLectura.de(tabla)


# ── Consulta ────────────────────────────────────────────────────────────────────────

def _escritura(db: Session, usuario: Usuario, grupo: FiltroGrupos, fecha: date) -> Escritura:
    periodo = cargar_calendarios(db, ids_colegio=[grupo.id_colegio]).pa_de(grupo.id_colegio, fecha)
    if periodo is None:
        return Escritura(permitida=False, motivo=MENSAJE_SIN_HABILITACION_FILAS)
    return escritura(db, usuario, Destino(MODULO, grupo.id_colegio, grupo.id_grado, periodo), MENSAJES_ESCRITURA)


def _filas(
    db: Session, tabla: LecturaSemanal, id_alumno: Optional[int] = None
) -> list[tuple[LecturaSemanalAlumno, Alumno, Optional[str]]]:
    consulta = (
        select(LecturaSemanalAlumno, Alumno, Asistencia.estado)
        .join(LecturaSemanal, LecturaSemanal.id == LecturaSemanalAlumno.id_lectura_semanal)
        .join(Alumno, Alumno.id_alumno == LecturaSemanalAlumno.id_alumno)
        .outerjoin(Asistencia, _asistencia_de_la_referencia())
        .where(LecturaSemanalAlumno.id_lectura_semanal == tabla.id)
        .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
    )
    if id_alumno is not None:
        consulta = consulta.where(LecturaSemanalAlumno.id_alumno == id_alumno)
    return list(db.exec(consulta).all())


def _fila_salida(
    db: Session, tabla: LecturaSemanal, fecha_clase: date, fila: tuple[LecturaSemanalAlumno, Alumno, Optional[str]]
) -> FilaLectura:
    registro, alumno, asistencia = fila
    return FilaLectura(
        id_alumno=alumno.id_alumno, nombre=f"{alumno.nombres} {alumno.apellidos}", inactivo=not alumno.activo,
        asistencia=asistencia, nivel_academico=nivel_academico(db, alumno.id_alumno, fecha_clase),
        **{campo: getattr(registro, campo) for campo in CAMPOS_FILA},
        total_libros=total_libros(registro), estado_fila=estado_fila(tabla.estado, asistencia, registro),
        revisar_asistencia=revisar_asistencia(asistencia, registro), version=registro.version,
    )


def _respuesta(db: Session, usuario: Usuario, tabla: LecturaSemanal) -> TablaLectura:
    clase = db.get(Clase, tabla.id_clase_referencia)
    filtro = _filtro_de(tabla)
    return TablaLectura(
        id=tabla.id, version=tabla.version, estado=tabla.estado, motivo_no_realizada=tabla.motivo_no_realizada,
        **filtro.columnas(), clase_referencia=_referencia(clase),
        filas=[_fila_salida(db, tabla, clase.fecha, fila) for fila in _filas(db, tabla)],
        escritura=_escritura(db, usuario, filtro.grupo, clase.fecha),
    )


def _escritura_para_crear(
    db: Session, usuario: Usuario, filtro: FiltroLectura, semana: SemanaReporte, clase: ClaseProgramada
) -> Escritura:
    puede = _escritura(db, usuario, filtro.grupo, clase.fecha)
    if puede.permitida and semana.fecha_inicio > hoy_lima():
        return Escritura(permitida=False, motivo=MENSAJE_SEMANA_FUTURA)
    return puede


def consultar_tabla(db: Session, usuario: Usuario, filtro: FiltroLectura) -> TablaLectura:
    """La tabla de los filtros (CU023, CU024). Si no existe: 404 `tabla_no_existe`, con
    la clase de referencia sugerida y si el usuario podría crearla; si la semana no tiene
    clases del grupo en ese mes, 422 `semana_sin_clase`."""
    semana = _validar_filtro(db, usuario, filtro)
    tabla = _tabla_por_filtro(db, filtro)
    if tabla is not None:
        return _respuesta(db, usuario, tabla)
    clases = _clases_de_la_semana(db, filtro, semana)
    if not clases:
        raise _no_procesable(MENSAJE_SEMANA_SIN_CLASE, SEMANA_SIN_CLASE)
    sugerida = _sugerida(clases)
    puede = _escritura_para_crear(db, usuario, filtro, semana, sugerida or clases[0])
    raise ErrorNegocio(
        status.HTTP_404_NOT_FOUND, MENSAJE_TABLA_NO_EXISTE, motivo="tabla_no_existe",
        extra={
            "clase_referencia_sugerida": _referencia(sugerida).model_dump(mode="json") if sugerida else None,
            "escritura": puede.model_dump(mode="json"),
        },
    )


def semanas_con_clase(
    db: Session, usuario: Usuario, grupo: FiltroGrupos, id_anio_escolar: int, mes: int
) -> list[SemanaResponse]:
    """Semanas del año con alguna clase del grupo (programada o guardada) en ese mes."""
    _validar_grupo(db, usuario, grupo)
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise _no_encontrado(MENSAJE_ANIO_NO_ENCONTRADO, "anio_no_encontrado")
    rango = rango_del_mes(anio, mes)
    if rango is None:
        return []
    fechas = {c.fecha for c in clases_programadas(db, grupo, *rango)}
    semanas = db.exec(
        select(SemanaReporte)
        .where(SemanaReporte.id_anio_escolar == id_anio_escolar, SemanaReporte.fecha_inicio <= rango[1],
               SemanaReporte.fecha_fin >= rango[0])
        .order_by(SemanaReporte.fecha_inicio)
    ).all()
    hoy = hoy_lima()
    return [
        SemanaResponse(
            id=s.id_semana, id_semana=s.id_semana, numero_semana=s.numero_semana, fecha_inicio=s.fecha_inicio,
            fecha_fin=s.fecha_fin, cerrada=s.fecha_fin < hoy,
        )
        for s in semanas
        if any(s.fecha_inicio <= f <= s.fecha_fin for f in fechas)
    ]


# ── Escrituras: piezas comunes ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Captura:
    usuario: Usuario
    contexto: ContextoOperacion
    id_permiso: Optional[int]


def _firma_nueva(captura: _Captura, ahora: datetime) -> dict[str, Any]:
    return {
        "id_docente": captura.usuario.id_docente, "id_actividad": captura.contexto.id_actividad,
        "id_permiso": captura.id_permiso, "fecha_registro": captura.contexto.en, "version": 1,
        "creado_por": captura.usuario.id_usuario, "creado_en": ahora,
    }


def _firmar(registro: LecturaSemanal | LecturaSemanalAlumno, captura: _Captura, ahora: datetime) -> None:
    """Quién, con qué y cuándo hizo el último cambio; sube la versión."""
    registro.id_docente = captura.usuario.id_docente
    registro.id_actividad, registro.id_permiso = captura.contexto.id_actividad, captura.id_permiso
    registro.fecha_registro, registro.version = captura.contexto.en, registro.version + 1
    registro.modificado_por, registro.modificado_en = captura.usuario.id_usuario, ahora


def _guardar_cambios(
    db: Session, registro: LecturaSemanal | LecturaSemanalAlumno, cambios: dict[str, tuple], captura: _Captura
) -> None:
    """Aplica y audita los cambios (campo -> (anterior, nuevo)) del registro."""
    ahora = ahora_utc()
    registrar_auditoria(
        db, tabla=registro.__tablename__, id_registro=registro.id, accion=EDITAR, cambios=cambios,
        id_usuario=captura.usuario.id_usuario, fecha=ahora, id_actividad=captura.contexto.id_actividad,
        id_permiso=captura.id_permiso,
    )
    for campo, (_, nuevo) in cambios.items():
        setattr(registro, campo, nuevo)
    _firmar(registro, captura, ahora)
    db.add(registro)
    db.flush()


def _exigir_habilitacion(
    db: Session, usuario: Usuario, grupo: FiltroGrupos, id_periodo: int, en: datetime, mensaje: str
) -> Optional[int]:
    """El permiso con que se escribe (None si es con una asignación); si no hay
    habilitación del módulo para ese PA en el instante `en`, rechazo con el mensaje del CU."""
    habilitacion = habilitacion_escritura(db, usuario, grupo.id_colegio, grupo.id_grado, id_periodo, MODULO, en=en)
    if not habilitacion.permitido:
        raise RechazoOperacion(SIN_HABILITACION, mensaje)
    return habilitacion.id_permiso


def _tabla_bloqueada(db: Session, id_tabla: int) -> LecturaSemanal:
    return db.exec(
        select(LecturaSemanal)
        .where(LecturaSemanal.id == id_tabla)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _exigir_version_tabla(tabla: LecturaSemanal, version: Optional[int]) -> None:
    if version != tabla.version:
        raise RechazoOperacion(
            MODIFICACION_CONCURRENTE, MENSAJE_TABLA_CONCURRENTE, {"id": tabla.id, "version": tabla.version}
        )


def _tiene_datos(db: Session, id_tabla: int) -> bool:
    con_datos = or_(*(col(getattr(LecturaSemanalAlumno, campo)).is_not(None) for campo in CAMPOS_LECTURA))
    return db.exec(
        select(LecturaSemanalAlumno.id).where(LecturaSemanalAlumno.id_lectura_semanal == id_tabla, con_datos).limit(1)
    ).first() is not None


def _clase_con_lista(db: Session, grupo: FiltroGrupos, elegida: ClaseProgramada, id_usuario: int) -> Clase:
    """La fila de la clase elegida (creada si hace falta) con su lista congelada."""
    clase = fila_de_programada(db, grupo, elegida, id_usuario)
    congelar_lista(db, clase)
    return clase


def _alumnos_de_la_lista(db: Session, id_clase: int, id_programa: int) -> list[int]:
    return list(db.exec(
        select(ClaseAlumno.id_alumno).where(ClaseAlumno.id_clase == id_clase, ClaseAlumno.id_programa == id_programa)
    ).all())


def _insertar_filas(db: Session, id_tabla: int, ids_alumno: list[int], captura: _Captura, ahora: datetime) -> None:
    """Filas vacías para esos alumnos. No se auditan: no tienen datos todavía."""
    if ids_alumno:
        firma = _firma_nueva(captura, ahora)
        db.execute(insert(_fila).values([
            {"id_lectura_semanal": id_tabla, "id_alumno": id_alumno, **firma} for id_alumno in ids_alumno
        ]))


def _ejecutar(
    db: Session, usuario: Usuario, item, fn_aplicar, operacion: Operacion
) -> TablaLectura:
    """Procesa una operación sobre una tabla y responde la tabla actualizada; un rechazo
    se responde como error HTTP (409 los conflictos, 422 el resto) con su `motivo`."""
    resultado = procesar_operacion(db, usuario, item, fn_aplicar, operacion)
    db.commit()
    if resultado.estado == RECHAZADA:
        codigo = status.HTTP_409_CONFLICT if resultado.motivo in CONFLICTOS else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise ErrorNegocio(
            codigo, resultado.detail or "", motivo=resultado.motivo,
            extra={"id_operacion": str(resultado.id_operacion), **(resultado.registro or {})},
        )
    return _respuesta(db, usuario, db.get(LecturaSemanal, resultado.registro["id"]))


def _tabla_visible(db: Session, usuario: Usuario, id_tabla: int) -> LecturaSemanal:
    """404 `tabla_no_encontrada`; 403 `aula_fuera_de_alcance` fuera del alcance del Docente."""
    tabla = db.get(LecturaSemanal, id_tabla)
    if tabla is None:
        raise _no_encontrado(MENSAJE_TABLA_NO_ENCONTRADA, "tabla_no_encontrada")
    grados_visibles(db, usuario, tabla.id_colegio, tabla.id_grado)
    return tabla


# ── Crear la tabla (CU023) ──────────────────────────────────────────────────────────

def _insertar_tabla(db: Session, filtro: FiltroLectura, elegida: ClaseProgramada, captura: _Captura) -> int:
    clase = _clase_con_lista(db, filtro.grupo, elegida, captura.usuario.id_usuario)
    alumnos = _alumnos_de_la_lista(db, clase.id_clase, filtro.id_programa)
    if not alumnos:
        raise RechazoOperacion(SIN_ALUMNOS, MENSAJE_SIN_ALUMNOS)
    ahora = ahora_utc()
    id_tabla = db.execute(
        insert_con_conflicto(db, _tabla)
        .values(**filtro.columnas(), id_clase_referencia=clase.id_clase, estado=LECTURA_NORMAL,
                **_firma_nueva(captura, ahora))
        .on_conflict_do_nothing()
        .returning(_tabla.c.id)
    ).scalar_one_or_none()
    if id_tabla is None:
        # La creó otra petición simultánea.
        existente = _tabla_por_filtro(db, filtro)
        raise RechazoOperacion(TABLA_EXISTENTE, MENSAJE_TABLA_EXISTENTE, {"id": existente.id} if existente else None)
    _insertar_filas(db, id_tabla, alumnos, captura, ahora)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=id_tabla, accion=CREAR, id_usuario=captura.usuario.id_usuario, fecha=ahora,
        id_actividad=captura.contexto.id_actividad, id_permiso=captura.id_permiso,
    )
    return id_tabla


def _creador(db: Session, usuario: Usuario, filtro: FiltroLectura, semana: SemanaReporte, data: TablaLecturaCrear):
    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        dia = fecha_lima(contexto.en)
        if semana.fecha_inicio > dia:
            raise RechazoOperacion(SEMANA_FUTURA, MENSAJE_SEMANA_FUTURA)
        clases = _clases_de_la_semana(db, filtro, semana)
        elegida = _elegida(clases, data.id_clase_referencia, data.fecha_clase_referencia)
        if elegida.fecha > dia:
            raise RechazoOperacion(CLASE_NO_REALIZADA, MENSAJE_CLASE_NO_REALIZADA)
        id_permiso = _exigir_habilitacion(
            db, usuario, filtro.grupo, elegida.id_periodo_academico, contexto.en, MENSAJE_SIN_HABILITACION_CREAR
        )
        existente = _tabla_por_filtro(db, filtro)
        if existente is not None:
            raise RechazoOperacion(TABLA_EXISTENTE, MENSAJE_TABLA_EXISTENTE, {"id": existente.id})
        return {"id": _insertar_tabla(db, filtro, elegida, _Captura(usuario, contexto, id_permiso))}

    return aplicar


def crear_tabla(db: Session, usuario: Usuario, data: TablaLecturaCrear) -> TablaLectura:
    """Crea la tabla de los filtros (CU023). Idempotente por `id_operacion`; una tabla ya
    existente con otro `id_operacion` es 409 `tabla_existente` (con su `id`)."""
    filtro = FiltroLectura.de(data)
    semana = _validar_filtro(db, usuario, filtro)
    operacion = Operacion(MODULO, TABLA, filtro.clave(), MENSAJE_SIN_ACTIVIDAD_CREAR)
    return _ejecutar(db, usuario, data, _creador(db, usuario, filtro, semana, data), operacion)


# ── Cambiar la clase de referencia (CU024) ──────────────────────────────────────────

def _exigir_clase_libre(db: Session, id_clase: int, tabla: LecturaSemanal) -> None:
    """REGLA PROVISIONAL L1: una tabla por clase de referencia y subprograma."""
    otra = db.exec(select(LecturaSemanal.id).where(
        LecturaSemanal.id_clase_referencia == id_clase, LecturaSemanal.id_programa == tabla.id_programa,
        LecturaSemanal.id != tabla.id,
    )).first()
    if otra is not None:
        raise RechazoOperacion(CLASE_YA_USADA, MENSAJE_CLASE_YA_USADA)


def _regenerar_filas(db: Session, tabla: LecturaSemanal, id_clase: int, captura: _Captura) -> None:
    """Deja una fila por alumno de la lista de la nueva clase: conserva las de quienes
    siguen (sin datos de lectura, a lo sumo una observación), quita las demás y agrega
    las que faltan."""
    nuevos = set(_alumnos_de_la_lista(db, id_clase, tabla.id_programa))
    if not nuevos:
        raise RechazoOperacion(SIN_ALUMNOS, MENSAJE_SIN_ALUMNOS)
    actuales = set(db.exec(
        select(LecturaSemanalAlumno.id_alumno).where(LecturaSemanalAlumno.id_lectura_semanal == tabla.id)
    ).all())
    salen = actuales - nuevos
    if salen:
        db.exec(delete(LecturaSemanalAlumno).where(
            col(LecturaSemanalAlumno.id_lectura_semanal) == tabla.id, col(LecturaSemanalAlumno.id_alumno).in_(salen)
        ))
    _insertar_filas(db, tabla.id, sorted(nuevos - actuales), captura, ahora_utc())


def _cambiador(db: Session, usuario: Usuario, id_tabla: int, data: ClaseReferenciaCambiar):
    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        tabla = _tabla_bloqueada(db, id_tabla)
        _exigir_version_tabla(tabla, data.version)
        if tabla.estado == LECTURA_NO_REALIZADA:
            raise RechazoOperacion(NO_REALIZADA, MENSAJE_NO_REALIZADA)
        if _tiene_datos(db, tabla.id):
            raise RechazoOperacion(TABLA_CON_DATOS, MENSAJE_CAMBIO_CON_DATOS)
        filtro = _filtro_de(tabla)
        clases = _clases_de_la_semana(db, filtro, db.get(SemanaReporte, tabla.id_semana))
        elegida = _elegida(clases, data.id_clase, data.fecha)
        if elegida.id_clase == tabla.id_clase_referencia:
            return {"id": tabla.id}
        if elegida.fecha > fecha_lima(contexto.en):
            raise RechazoOperacion(CLASE_NO_REALIZADA, MENSAJE_CLASE_NO_REALIZADA)
        captura = _Captura(usuario, contexto, _exigir_habilitacion(
            db, usuario, filtro.grupo, elegida.id_periodo_academico, contexto.en, MENSAJE_SIN_HABILITACION_FILAS
        ))
        clase = _clase_con_lista(db, filtro.grupo, elegida, usuario.id_usuario)
        _exigir_clase_libre(db, clase.id_clase, tabla)
        _regenerar_filas(db, tabla, clase.id_clase, captura)
        _guardar_cambios(db, tabla, {"id_clase_referencia": (tabla.id_clase_referencia, clase.id_clase)}, captura)
        return {"id": tabla.id}

    return aplicar


def cambiar_clase_referencia(
    db: Session, usuario: Usuario, id_tabla: int, data: ClaseReferenciaCambiar
) -> TablaLectura:
    """Cambia la clase de referencia (CU024) a otra del grupo en la misma semana y mes,
    solo sin datos de lectura y si la tabla no está "No realizada"."""
    _tabla_visible(db, usuario, id_tabla)
    operacion = Operacion(MODULO, TABLA, str(id_tabla), MENSAJE_SIN_ACTIVIDAD_FILAS)
    return _ejecutar(db, usuario, data, _cambiador(db, usuario, id_tabla, data), operacion)


# ── No realizada (CU026) ────────────────────────────────────────────────────────────

def _marcador(db: Session, usuario: Usuario, id_tabla: int, data: NoRealizadaMarcar, motivo: str):
    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        tabla = _tabla_bloqueada(db, id_tabla)
        clase = db.get(Clase, tabla.id_clase_referencia)
        id_permiso = _exigir_habilitacion(
            db, usuario, _filtro_de(tabla).grupo, clase.id_periodo_academico, contexto.en,
            MENSAJE_SIN_HABILITACION_NO_REALIZADA,
        )
        _exigir_version_tabla(tabla, data.version)
        if _tiene_datos(db, tabla.id):
            raise RechazoOperacion(TABLA_CON_DATOS, MENSAJE_NO_REALIZADA_CON_DATOS)
        nuevos = {"estado": LECTURA_NO_REALIZADA, "motivo_no_realizada": motivo}
        cambios = {c: (getattr(tabla, c), v) for c, v in nuevos.items() if getattr(tabla, c) != v}
        if cambios:
            _guardar_cambios(db, tabla, cambios, _Captura(usuario, contexto, id_permiso))
        return {"id": tabla.id}

    return aplicar


def marcar_no_realizada(db: Session, usuario: Usuario, id_tabla: int, data: NoRealizadaMarcar) -> TablaLectura:
    """Marca toda la tabla como "No realizada" con su motivo (CU026). 422 si falta el
    motivo; 409 `tabla_con_datos` si alguna fila tiene datos de lectura."""
    _tabla_visible(db, usuario, id_tabla)
    motivo = (data.motivo or "").strip()
    if not motivo:
        raise error_de_campo("motivo", MENSAJE_MOTIVO_OBLIGATORIO)
    operacion = Operacion(MODULO, TABLA, str(id_tabla), MENSAJE_SIN_ACTIVIDAD_NO_REALIZADA)
    return _ejecutar(db, usuario, data, _marcador(db, usuario, id_tabla, data, motivo), operacion)


# ── Filas (CU024, CU025) ────────────────────────────────────────────────────────────

def _normalizado(valor: Any) -> Any:
    """Un texto con solo espacios cuenta como vacío."""
    if isinstance(valor, str):
        return valor.strip() or None
    return valor


def _cambios_de(fila: LecturaSemanalAlumno, item: FilaLecturaGuardar) -> dict[str, tuple]:
    enviados = {campo: _normalizado(getattr(item, campo)) for campo in CAMPOS_FILA if campo in item.model_fields_set}
    return {campo: (getattr(fila, campo), nuevo) for campo, nuevo in enviados.items() if getattr(fila, campo) != nuevo}


def _validar_lsn(valores: dict[str, Any]) -> None:
    lsn = [valores[campo] for campo in CAMPOS_LSN]
    if all(v is None for v in lsn):
        return
    if any(v is None for v in lsn):
        raise RechazoOperacion(LSN_INCOMPLETO, MENSAJE_LSN_INCOMPLETO)
    if valores["lsn_maximo"] not in MAXIMOS_LSN or not 0 <= valores["lsn_nota"] <= valores["lsn_maximo"]:
        raise RechazoOperacion(LSN_INVALIDO, MENSAJE_LSN_INVALIDO)


def _registro_fila(db: Session, tabla: LecturaSemanal, fecha_clase: date, id_alumno: int) -> dict[str, Any]:
    return _fila_salida(db, tabla, fecha_clase, _filas(db, tabla, id_alumno)[0]).model_dump(mode="json")


def _validar_cambios(
    db: Session, tabla: LecturaSemanal, clase: Clase, fila: LecturaSemanalAlumno, item: FilaLecturaGuardar,
    cambios: dict[str, tuple],
) -> None:
    if tabla.estado == LECTURA_NO_REALIZADA:
        raise RechazoOperacion(NO_REALIZADA, MENSAJE_NO_REALIZADA)
    if item.version != fila.version:
        raise RechazoOperacion(
            MODIFICACION_CONCURRENTE, MENSAJE_FILA_CONCURRENTE, _registro_fila(db, tabla, clase.fecha, fila.id_alumno)
        )
    resultado = {campo: cambios[campo][1] if campo in cambios else getattr(fila, campo) for campo in CAMPOS_LECTURA}
    _validar_lsn(resultado)
    agrega_lectura = any(c in cambios for c in CAMPOS_LECTURA) and any(v is not None for v in resultado.values())
    if agrega_lectura and _asistencia(db, clase.id_clase, fila.id_alumno) not in ESTADOS_ASISTIO:
        raise RechazoOperacion(SIN_ASISTENCIA, MENSAJE_SIN_ASISTENCIA)


def _asistencia(db: Session, id_clase: int, id_alumno: int) -> Optional[str]:
    return db.exec(
        select(Asistencia.estado).where(Asistencia.id_clase == id_clase, Asistencia.id_alumno == id_alumno)
    ).first()


def _fila_bloqueada(db: Session, id_tabla: int, id_alumno: int) -> Optional[LecturaSemanalAlumno]:
    return db.exec(
        select(LecturaSemanalAlumno)
        .where(LecturaSemanalAlumno.id_lectura_semanal == id_tabla, LecturaSemanalAlumno.id_alumno == id_alumno)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _editor(db: Session, usuario: Usuario, id_tabla: int, item: FilaLecturaGuardar):
    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        tabla = _tabla_bloqueada(db, id_tabla)
        clase = db.get(Clase, tabla.id_clase_referencia)
        id_permiso = _exigir_habilitacion(
            db, usuario, _filtro_de(tabla).grupo, clase.id_periodo_academico, contexto.en,
            MENSAJE_SIN_HABILITACION_FILAS,
        )
        fila = _fila_bloqueada(db, id_tabla, item.id_alumno)
        if fila is None:
            raise RechazoOperacion(ALUMNO_FUERA_DE_LA_TABLA, MENSAJE_ALUMNO_FUERA)
        cambios = _cambios_de(fila, item)
        if cambios:
            _validar_cambios(db, tabla, clase, fila, item, cambios)
            _guardar_cambios(db, fila, cambios, _Captura(usuario, contexto, id_permiso))
        return _registro_fila(db, tabla, clase.fecha, fila.id_alumno)

    return aplicar


def guardar_filas(
    db: Session, usuario: Usuario, id_tabla: int, items: list[FilaLecturaGuardar]
) -> list[ResultadoOperacion]:
    """Registra o corrige filas de la tabla (CU024, CU025). Cada ítem es una operación con
    su resultado; un lote puede tener aceptadas y rechazadas. Un ítem sin cambios no
    escribe ni audita."""
    _tabla_visible(db, usuario, id_tabla)
    resultados = [
        procesar_operacion(
            db, usuario, item, _editor(db, usuario, id_tabla, item),
            Operacion(MODULO, TABLA_FILA, f"{id_tabla}:{item.id_alumno}", MENSAJE_SIN_ACTIVIDAD_FILAS),
        )
        for item in items
    ]
    db.commit()
    return resultados
