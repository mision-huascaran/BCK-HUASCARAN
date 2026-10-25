"""Asistencia por clase (CU022).

La clase es colegio × grado × sección en una fecha (app/services/programacion.py).

- Lectura: el Docente, de los grupos de su alcance de lectura; el Supervisor, de todos y
  siempre en solo lectura. La fecha se valida con el calendario del colegio: futura, en
  un receso, no lectiva o fuera de todo PA → 422 con el mensaje exacto.
- Lista: la congelada de la clase si ya existe; si no, los alumnos que pertenecían al
  grupo en esa fecha. Al guardar la primera asistencia se congela (`clase_alumno`) y ya
  no cambia con la pertenencia; un alumno hoy inactivo sigue en ella (`inactivo: true`).
- Escritura (solo Docente): cada registro es una operación (`procesar_operacion`), con
  la habilitación de Asistencias del PA de la fecha evaluada a la hora real del
  registro, dentro de una actividad suya. La primera escritura crea la clase (habitual
  en su fecha programada; adicional solo con `adicional: true` y si ese día no hay
  clase) y congela su lista. Con dos dispositivos gana la captura más reciente
  (`fecha_registro`): la `version` es informativa y no produce 409.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from fastapi import status
from sqlmodel import Session, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.evaluacion import TIPO_ADICIONAL, TIPO_HABITUAL, Asistencia, Clase, ClaseAlumno
from app.models.organizacion import Alumno, Seccion, Usuario
from app.schemas.asistencia import (
    AlumnoAsistencia,
    AsistenciaClase,
    AsistenciaGuardada,
    AsistenciaGuardar,
    ClaseAsistencia,
    Escritura,
    RegistroAsistencia,
)
from app.services import claves_registro
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import Calendarios, PeriodoCalendario, cargar_calendarios
from app.services.clase_service import colegio_existente, grados_visibles
from app.services.escritura import Destino, MensajesEscritura, escritura
from app.services.habilitacion import habilitacion_escritura
from app.services.pertenencia import fecha_legible, lista_del_grupo
from app.services.programacion import (
    ClaseDelDia,
    ClaseProgramada,
    FiltroGrupos,
    NuevaClase,
    asegurar_fila,
    clase_del_dia,
    congelar_lista,
    fila_de_programada,
)
from app.services.sincronizacion import (
    MENSAJE_ACTIVIDAD_REQUERIDA,
    ContextoOperacion,
    Operacion,
    OperacionDescartada,
    RechazoOperacion,
    procesar_operacion,
)

TABLA = "asistencia"
MODULO = "asistencias"
CAMPOS = ("estado", "observacion")
_asistencia = Asistencia.__table__

MENSAJE_FUTURA = "No se pueden registrar asistencias para fechas futuras."
MENSAJE_RECESO = "La fecha seleccionada corresponde a un periodo de receso."
MENSAJE_NO_LECTIVO = "Día no lectivo: {motivo}. No se permite registrar asistencias para esta fecha."
MENSAJE_FUERA_DE_PA = "La fecha seleccionada no pertenece a un periodo académico configurado."
MOTIVO_FIN_DE_SEMANA = "fin de semana"
ETIQUETAS = {TIPO_HABITUAL: "Clase programada", TIPO_ADICIONAL: "Clase adicional"}
ETIQUETA_REPROGRAMADA = "Clase reprogramada"
AVISO_REPROGRAMADA = "Esta clase estaba programada para el {original} y fue trasladada al {vigente}."
AVISO_SIN_CLASE = "No hay una clase programada para esta fecha."
AVISO_TRASLADADA = (
    "La clase prevista para esta fecha fue trasladada al {vigente}. Registrar una clase adicional no "
    "completa la asistencia de la clase trasladada."
)
MENSAJE_SIN_HABILITACION = (
    "No tienes una habilitación vigente para registrar o corregir asistencias en la fecha seleccionada."
)
MENSAJE_ACTIVIDAD = MENSAJE_ACTIVIDAD_REQUERIDA
MENSAJE_SOLO_LECTURA = "El Supervisor consulta las asistencias en solo lectura."
MENSAJE_FUERA_DE_LA_CLASE = "El alumno no está en la lista de esta clase."
MENSAJE_SIN_CLASE = "No hay una clase programada para esta fecha. Para registrarla, márcala como clase adicional."


# ── Fecha y grupo ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FechaInvalida:
    detail: str
    motivo: str


def problema_de_fecha(calendarios: Calendarios, id_colegio: int, fecha: date) -> Optional[FechaInvalida]:
    """Por qué no se puede registrar asistencia en la fecha (None si se puede)."""
    if fecha > hoy_lima():
        return FechaInvalida(MENSAJE_FUTURA, "fecha_futura")
    calendario = calendarios.calendario_en(id_colegio, fecha)
    periodo = calendario.periodo_de(fecha) if calendario is not None else None
    if periodo is None:
        return FechaInvalida(MENSAJE_FUERA_DE_PA, "fecha_fuera_de_periodo")
    if not periodo.es_pa:
        return FechaInvalida(MENSAJE_RECESO, "fecha_en_receso")
    if not calendario.es_lectivo(fecha):
        no_lectivo = calendario.no_lectivo_de(fecha)
        motivo = no_lectivo.motivo if no_lectivo is not None else MOTIVO_FIN_DE_SEMANA
        return FechaInvalida(MENSAJE_NO_LECTIVO.format(motivo=motivo), "fecha_no_lectiva")
    return None


def exigir_seccion(db: Session, filtro: FiltroGrupos) -> None:
    seccion = db.get(Seccion, filtro.id_seccion)
    if seccion is None or (seccion.id_colegio, seccion.id_grado) != (filtro.id_colegio, filtro.id_grado):
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "La sección no pertenece a ese colegio y grado.",
            motivo="seccion_inexistente",
        )


# ── La clase ────────────────────────────────────────────────────────────────────────

def _aviso(del_dia: ClaseDelDia) -> Optional[str]:
    clase = del_dia.clase
    avisos = []
    if clase is None:
        avisos.append(AVISO_SIN_CLASE)
    elif clase.reprogramada:
        avisos.append(AVISO_REPROGRAMADA.format(
            original=fecha_legible(clase.fecha_original), vigente=fecha_legible(clase.fecha)
        ))
    if del_dia.trasladada_a is not None and (clase is None or clase.tipo == TIPO_ADICIONAL):
        avisos.append(AVISO_TRASLADADA.format(vigente=fecha_legible(del_dia.trasladada_a)))
    return " ".join(avisos) or None


def info_de_clase(del_dia: ClaseDelDia, fecha: date) -> ClaseAsistencia:
    clase = del_dia.clase
    if clase is None:
        return ClaseAsistencia(fecha_original=fecha, fecha=fecha, aviso=_aviso(del_dia))
    etiqueta = ETIQUETA_REPROGRAMADA if clase.reprogramada else ETIQUETAS[clase.tipo]
    return ClaseAsistencia(
        id_clase=clase.id_clase, tipo=clase.tipo, etiqueta=etiqueta, fecha_original=clase.fecha_original,
        fecha=clase.fecha, aviso=_aviso(del_dia),
    )


def _lista(db: Session, filtro: FiltroGrupos, clase: Optional[ClaseProgramada], fecha: date) -> list[tuple[Alumno, int]]:
    """La lista congelada de la clase si existe; si no, la del grupo en la fecha."""
    fila = db.get(Clase, clase.id_clase) if clase is not None and clase.id_clase is not None else None
    if fila is None or fila.lista_congelada_en is None:
        return lista_del_grupo(db, filtro.id_colegio, filtro.id_grado, filtro.id_seccion, fecha)
    return list(
        db.exec(
            select(Alumno, ClaseAlumno.id_programa)
            .join(ClaseAlumno, ClaseAlumno.id_alumno == Alumno.id_alumno)
            .where(ClaseAlumno.id_clase == fila.id_clase)
            .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
        ).all()
    )


def _alumnos(db: Session, lista: list[tuple[Alumno, int]], id_clase: Optional[int]) -> list[AlumnoAsistencia]:
    registros = {}
    if id_clase is not None:
        registros = {a.id_alumno: a for a in db.exec(select(Asistencia).where(Asistencia.id_clase == id_clase)).all()}
    salida = []
    for alumno, id_programa in lista:
        fila = registros.get(alumno.id_alumno)
        salida.append(AlumnoAsistencia(
            id_alumno=alumno.id_alumno, nombre=f"{alumno.nombres} {alumno.apellidos}", id_programa=id_programa,
            inactivo=not alumno.activo, estado=fila.estado if fila else None,
            observacion=fila.observacion if fila else None, version=fila.version if fila else None,
        ))
    return salida


# ── Escritura permitida ─────────────────────────────────────────────────────────────

MENSAJES_ESCRITURA = MensajesEscritura(MENSAJE_SOLO_LECTURA, MENSAJE_SIN_HABILITACION, MENSAJE_ACTIVIDAD)


def _escritura(db: Session, usuario: Usuario, filtro: FiltroGrupos, periodo: PeriodoCalendario) -> Escritura:
    return escritura(db, usuario, Destino(MODULO, filtro.id_colegio, filtro.id_grado, periodo), MENSAJES_ESCRITURA)


# ── Consulta ────────────────────────────────────────────────────────────────────────

def _no_procesable(fecha_invalida: FechaInvalida) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, fecha_invalida.detail, motivo=fecha_invalida.motivo)


def consultar_clase(db: Session, usuario: Usuario, filtro: FiltroGrupos, fecha: date) -> AsistenciaClase:
    """Asistencia de la clase del grupo en la fecha (CU022).

    Errores: 403 `aula_fuera_de_alcance` (Docente), 404 `colegio_no_encontrado`, 422
    `seccion_inexistente` y los de la fecha: `fecha_futura`, `fecha_en_receso`,
    `fecha_no_lectiva`, `fecha_fuera_de_periodo`."""
    colegio_existente(db, filtro.id_colegio)
    grados_visibles(db, usuario, filtro.id_colegio, filtro.id_grado)
    exigir_seccion(db, filtro)
    calendarios = cargar_calendarios(db, ids_colegio=[filtro.id_colegio])
    invalida = problema_de_fecha(calendarios, filtro.id_colegio, fecha)
    if invalida is not None:
        raise _no_procesable(invalida)
    del_dia = clase_del_dia(db, filtro, fecha, calendarios)
    clase = del_dia.clase
    lista = _lista(db, filtro, clase, fecha)
    return AsistenciaClase(
        clase=info_de_clase(del_dia, fecha),
        alumnos=_alumnos(db, lista, clase.id_clase if clase is not None else None),
        escritura=_escritura(db, usuario, filtro, calendarios.pa_de(filtro.id_colegio, fecha)),
    )


# ── Registro ────────────────────────────────────────────────────────────────────────

@dataclass
class _Destino:
    """La clase a la que van los registros del lote."""

    filtro: FiltroGrupos
    fecha: date
    periodo: Optional[PeriodoCalendario]
    del_dia: ClaseDelDia
    adicional: bool
    fecha_invalida: Optional[FechaInvalida]


def _destino(db: Session, data: AsistenciaGuardar) -> _Destino:
    if data.id_clase is not None:
        fila = db.get(Clase, data.id_clase)
        if fila is None:
            raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "La clase no existe.", motivo="clase_no_encontrada")
        filtro, fecha = FiltroGrupos(fila.id_colegio, fila.id_grado, fila.id_seccion), fila.fecha
    else:
        filtro, fecha = FiltroGrupos(data.id_colegio, data.id_grado, data.id_seccion), data.fecha
    calendarios = cargar_calendarios(db, ids_colegio=[filtro.id_colegio])
    return _Destino(
        filtro, fecha, calendarios.pa_de(filtro.id_colegio, fecha), clase_del_dia(db, filtro, fecha, calendarios),
        data.adicional, problema_de_fecha(calendarios, filtro.id_colegio, fecha),
    )


def _crear_clase(db: Session, destino: _Destino, usuario: Usuario) -> Clase:
    """La fila de la clase del destino; si no existe, la crea (habitual en su fecha
    programada o adicional)."""
    clase = destino.del_dia.clase
    if clase is not None:
        return fila_de_programada(db, destino.filtro, clase, usuario.id_usuario)
    if not destino.adicional:
        raise RechazoOperacion("sin_clase_programada", MENSAJE_SIN_CLASE)
    nueva = NuevaClase(destino.fecha, destino.fecha, TIPO_ADICIONAL, destino.periodo.id_periodo_academico)
    return asegurar_fila(db, destino.filtro, nueva, usuario.id_usuario)


def _registro(fila: Asistencia) -> dict[str, Any]:
    return {
        "id_clase": fila.id_clase, "id_alumno": fila.id_alumno, "estado": fila.estado,
        "observacion": fila.observacion, "version": fila.version, "fecha_registro": fila.fecha_registro.isoformat(),
    }


def _fila_de(db: Session, id_clase: int, id_alumno: int) -> Optional[Asistencia]:
    return db.exec(
        select(Asistencia)
        .where(Asistencia.id_clase == id_clase, Asistencia.id_alumno == id_alumno)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


@dataclass(frozen=True)
class _Captura:
    usuario: Usuario
    contexto: ContextoOperacion
    id_permiso: Optional[int]


def _insertar(db: Session, clase: Clase, registro: RegistroAsistencia, captura: _Captura) -> Optional[Asistencia]:
    """Crea la fila; None si otra petición simultánea la creó antes."""
    ahora = ahora_utc()
    valores = {campo: getattr(registro, campo) for campo in CAMPOS if campo in registro.model_fields_set}
    id_nuevo = db.execute(
        insert_con_conflicto(db, _asistencia)
        .values(
            id_clase=clase.id_clase, id_alumno=registro.id_alumno, **valores,
            id_docente=captura.usuario.id_docente, id_actividad=captura.contexto.id_actividad,
            id_permiso=captura.id_permiso, fecha_registro=captura.contexto.en, version=1,
            creado_por=captura.usuario.id_usuario, creado_en=ahora,
        )
        .on_conflict_do_nothing()
        .returning(_asistencia.c.id)
    ).scalar_one_or_none()
    if id_nuevo is None:
        return None
    registrar_auditoria(
        db, tabla=TABLA, id_registro=id_nuevo, accion=CREAR, id_usuario=captura.usuario.id_usuario, fecha=ahora,
        id_actividad=captura.contexto.id_actividad, id_permiso=captura.id_permiso,
    )
    return db.get(Asistencia, id_nuevo)


def _actualizar(db: Session, fila: Asistencia, registro: RegistroAsistencia, captura: _Captura) -> Asistencia:
    """Aplica los campos enviados, salvo que la fila tenga una captura posterior (gana la
    más reciente: se descarta y se devuelve la vigente)."""
    if fila.fecha_registro > captura.contexto.en:
        raise OperacionDescartada("registro_mas_reciente", _registro(fila))
    cambios = {
        campo: (getattr(fila, campo), getattr(registro, campo))
        for campo in CAMPOS
        if campo in registro.model_fields_set and getattr(fila, campo) != getattr(registro, campo)
    }
    if not cambios:
        return fila
    ahora = ahora_utc()
    registrar_auditoria(
        db, tabla=TABLA, id_registro=fila.id, accion=EDITAR, cambios=cambios, id_usuario=captura.usuario.id_usuario,
        fecha=ahora, id_actividad=captura.contexto.id_actividad, id_permiso=captura.id_permiso,
    )
    for campo, (_, nuevo) in cambios.items():
        setattr(fila, campo, nuevo)
    fila.id_docente, fila.id_actividad, fila.id_permiso = (
        captura.usuario.id_docente, captura.contexto.id_actividad, captura.id_permiso
    )
    fila.fecha_registro, fila.version = captura.contexto.en, fila.version + 1
    fila.modificado_por, fila.modificado_en = captura.usuario.id_usuario, ahora
    db.add(fila)
    db.flush()
    return fila


def _aplicador(db: Session, usuario: Usuario, destino: _Destino, registro: RegistroAsistencia):
    """La función que `procesar_operacion` ejecuta para un registro."""

    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        if destino.fecha_invalida is not None:
            raise RechazoOperacion(destino.fecha_invalida.motivo, destino.fecha_invalida.detail)
        filtro = destino.filtro
        habilitacion = habilitacion_escritura(
            db, usuario, filtro.id_colegio, filtro.id_grado, destino.periodo.id_periodo_academico, MODULO,
            en=contexto.en,
        )
        if not habilitacion.permitido:
            raise RechazoOperacion("sin_habilitacion", MENSAJE_SIN_HABILITACION)
        clase = _crear_clase(db, destino, usuario)
        congelar_lista(db, clase)
        if db.get(ClaseAlumno, (clase.id_clase, registro.id_alumno)) is None:
            raise RechazoOperacion("alumno_fuera_de_la_clase", MENSAJE_FUERA_DE_LA_CLASE)
        captura = _Captura(usuario, contexto, habilitacion.id_permiso)
        fila = _fila_de(db, clase.id_clase, registro.id_alumno)
        if fila is None:
            fila = _insertar(db, clase, registro, captura) or _fila_de(db, clase.id_clase, registro.id_alumno)
        else:
            fila = _actualizar(db, fila, registro, captura)
        return _registro(fila)

    return aplicar


def _id_entidad(destino: _Destino, id_alumno: int) -> str:
    filtro = destino.filtro
    return f"{filtro.id_colegio}-{filtro.id_grado}-{filtro.id_seccion}-{destino.fecha.isoformat()}:{id_alumno}"


def _clave(destino: _Destino, id_alumno: int) -> str:
    filtro = destino.filtro
    return claves_registro.asistencia(filtro.id_colegio, filtro.id_grado, filtro.id_seccion, destino.fecha, id_alumno)


def guardar_asistencia(db: Session, usuario: Usuario, data: AsistenciaGuardar) -> AsistenciaGuardada:
    """Registra la asistencia de uno o varios alumnos de una clase (CU022). Cada registro
    es una operación con su resultado (aceptada o rechazada); un lote puede tener de las
    dos. Errores del lote completo: 403 `aula_fuera_de_alcance`, 404
    `clase_no_encontrada` y `colegio_no_encontrado`, 422 `seccion_inexistente` y `validacion`."""
    destino = _destino(db, data)
    colegio_existente(db, destino.filtro.id_colegio)
    grados_visibles(db, usuario, destino.filtro.id_colegio, destino.filtro.id_grado)
    exigir_seccion(db, destino.filtro)
    resultados = [
        procesar_operacion(
            db, usuario, registro, _aplicador(db, usuario, destino, registro),
            Operacion(MODULO, TABLA, _id_entidad(destino, registro.id_alumno),
                      clave_registro=_clave(destino, registro.id_alumno)),
        )
        for registro in data.registros
    ]
    db.commit()
    calendarios = cargar_calendarios(db, ids_colegio=[destino.filtro.id_colegio])
    del_dia = clase_del_dia(db, destino.filtro, destino.fecha, calendarios)
    return AsistenciaGuardada(clase=info_de_clase(del_dia, destino.fecha), resultados=resultados)
