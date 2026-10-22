"""Programación de clases y reprogramaciones (CU013).

- Listado: el Supervisor, de cualquier colegio; el Docente, de los colegio y grado de
  su alcance de lectura.
- Reprogramar: solo el Supervisor. Mueve una clase habitual a otra fecha lectiva del
  mismo PA, sin otra clase del grupo ese día y mientras no tenga asistencias. Si la
  clase todavía no tenía fila, se crea. Queda en el historial y en la auditoría.
"""
from datetime import date
from typing import Optional

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio, error_de_campo
from app.core.paginacion import TAMANO_PAGINA, Paginado, pagina
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.evaluacion import TIPO_HABITUAL, Clase, ClaseReprogramacion
from app.models.organizacion import Colegio, Seccion, Usuario
from app.schemas.clase import ClaseItem, ReprogramacionCrear, ReprogramacionItem
from app.schemas.comun import ItemCatalogo
from app.services.actividad_consulta import exigir_rango_valido
from app.services.alumno_service import alcance_de
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import Calendarios, cargar_calendarios
from app.services.pertenencia import fecha_legible
from app.services.programacion import (
    ClaseProgramada,
    FiltroGrupos,
    clase_del_dia,
    clases_programadas,
    con_asistencias,
)

TABLA = "clase"
CAMPO_FECHA_NUEVA = "fecha_nueva"
MENSAJE_CON_ASISTENCIAS = "Esta clase ya tiene asistencias registradas y no puede reprogramarse."
MENSAJE_MISMA_FECHA = "Selecciona una fecha distinta de la fecha actual de la clase."
MENSAJE_RECESO = "La nueva fecha corresponde a un periodo de receso."
MENSAJE_NO_LECTIVA = "La nueva fecha no es un día lectivo del calendario del colegio."
MENSAJE_OTRO_PA = "La nueva fecha debe pertenecer al mismo periodo académico de la clase."
MENSAJE_OCUPADA = "El grupo ya tiene una clase el {fecha}."
MENSAJE_SIN_MOTIVO = "Ingresa el motivo de la reprogramación."


def _no_encontrado(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_404_NOT_FOUND, detail, motivo=motivo)


def _conflicto(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_409_CONFLICT, detail, motivo=motivo)


def _concurrente() -> ErrorNegocio:
    return _conflicto(
        "La clase fue modificada por otro usuario. Vuelve a consultarla e inténtalo de nuevo.",
        "modificacion_concurrente",
    )


def a_item(clase: ClaseProgramada) -> ClaseItem:
    return ClaseItem(
        id_clase=clase.id_clase, id_grado=clase.id_grado, id_seccion=clase.id_seccion,
        fecha_original=clase.fecha_original, fecha=clase.fecha, tipo=clase.tipo, reprogramada=clase.reprogramada,
        tiene_asistencias=clase.tiene_asistencias, id_periodo_academico=clase.id_periodo_academico,
        version=clase.version,
    )


# ── Listado ─────────────────────────────────────────────────────────────────────────

def colegio_existente(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_encontrado("El colegio no existe.", "colegio_no_encontrado")
    return colegio


def grados_visibles(db: Session, usuario: Usuario, id_colegio: int, id_grado: Optional[int]) -> Optional[set[int]]:
    """Grados del colegio que el usuario puede leer: None = todos (Supervisor). 403 si el
    Docente pide un colegio o grado fuera de su alcance de lectura."""
    alcance = alcance_de(db, usuario, lectura=True)
    if alcance is None:
        return None
    grados = {g for c, g in alcance if c == id_colegio}
    if not grados or (id_grado is not None and id_grado not in grados):
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "El colegio y grado indicados no están entre los que tiene asignados.",
            motivo="aula_fuera_de_alcance",
        )
    return grados


def _rango_por_defecto(calendarios: Calendarios, id_colegio: int) -> Optional[tuple[date, date]]:
    """El año del calendario del colegio que contiene hoy."""
    calendario = calendarios.calendario_en(id_colegio, hoy_lima())
    return (calendario.fecha_inicio, calendario.fecha_fin) if calendario is not None else None


def listar_clases(
    db: Session,
    usuario: Usuario,
    filtro: FiltroGrupos,
    rango: tuple[Optional[date], Optional[date]],
    page: Optional[int],
) -> list[ClaseItem] | Paginado[ClaseItem]:
    """Clases del colegio (por fecha vigente, grado y sección) en [desde, hasta]; por
    defecto, el año del calendario del colegio que contiene hoy. Con `page`, paginado de
    40; sin él, el arreglo completo (para elegir la clase de referencia, CU024)."""
    colegio_existente(db, filtro.id_colegio)
    grados = grados_visibles(db, usuario, filtro.id_colegio, filtro.id_grado)
    desde, hasta = rango
    exigir_rango_valido(desde, hasta)
    calendarios = cargar_calendarios(db, ids_colegio=[filtro.id_colegio])
    defecto = _rango_por_defecto(calendarios, filtro.id_colegio)
    desde, hasta = desde or (defecto and defecto[0]), hasta or (defecto and defecto[1])
    if desde is None or hasta is None:
        clases = []
    else:
        clases = clases_programadas(db, filtro, desde, hasta, calendarios)
    items = [a_item(c) for c in clases if grados is None or c.id_grado in grados]
    if page is None:
        return items
    inicio = (page - 1) * TAMANO_PAGINA
    return pagina(items[inicio:inicio + TAMANO_PAGINA], len(items), page)


# ── Reprogramar ─────────────────────────────────────────────────────────────────────

def _error_fecha_nueva(mensaje: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_422_UNPROCESSABLE_CONTENT, mensaje, motivo=motivo,
        extra={"errores": [{"campo": CAMPO_FECHA_NUEVA, "mensaje": mensaje}]},
    )


def _seccion_del_grupo(db: Session, id_colegio: int, id_grado: int, id_seccion: int) -> None:
    seccion = db.get(Seccion, id_seccion)
    if seccion is None or (seccion.id_colegio, seccion.id_grado) != (id_colegio, id_grado):
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "La sección no pertenece a ese colegio y grado.",
            motivo="seccion_inexistente",
        )


def _clase_guardada(db: Session, filtro: FiltroGrupos, fecha_original: date) -> Optional[Clase]:
    """La fila de la habitual con esa fecha programada, bloqueada hasta el commit."""
    return db.exec(
        select(Clase)
        .where(*filtro.de(Clase), Clase.tipo == TIPO_HABITUAL, Clase.fecha_original == fecha_original)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _habitual_calculada(
    db: Session, filtro: FiltroGrupos, fecha: date, calendarios: Calendarios
) -> ClaseProgramada:
    del_dia = clase_del_dia(db, filtro, fecha, calendarios).clase
    if del_dia is None or del_dia.tipo != TIPO_HABITUAL or del_dia.fecha_original != fecha:
        raise _no_encontrado("No hay una clase programada para esa fecha.", "clase_no_programada")
    return del_dia


def _validar_fecha_nueva(
    db: Session, filtro: FiltroGrupos, clase: ClaseProgramada, fecha_nueva: date, calendarios: Calendarios
) -> None:
    """Distinta de la vigente, lectiva, del mismo PA y sin otra clase del grupo ese día
    (programada o guardada). Puede caer en otro día de la semana."""
    if fecha_nueva == clase.fecha:
        raise _error_fecha_nueva(MENSAJE_MISMA_FECHA, "fecha_sin_cambio")
    periodo = calendarios.periodo_de(filtro.id_colegio, fecha_nueva)
    if periodo is not None and not periodo.es_pa:
        raise _error_fecha_nueva(MENSAJE_RECESO, "fecha_en_receso")
    if not calendarios.es_lectivo(filtro.id_colegio, fecha_nueva):
        raise _error_fecha_nueva(MENSAJE_NO_LECTIVA, "fecha_no_lectiva")
    if periodo.id_periodo_academico != clase.id_periodo_academico:
        raise _error_fecha_nueva(MENSAJE_OTRO_PA, "fecha_de_otro_periodo")
    ese_dia = clase_del_dia(db, filtro, fecha_nueva, calendarios).clase
    # Habituales sin fila no tienen id: se identifican por tipo y fecha programada.
    if ese_dia is not None and (ese_dia.tipo, ese_dia.fecha_original) != (clase.tipo, clase.fecha_original):
        raise _error_fecha_nueva(MENSAJE_OCUPADA.format(fecha=fecha_legible(fecha_nueva)), "fecha_ocupada")


def _exigir_version(data: ReprogramacionCrear, clase: Optional[Clase]) -> None:
    if "version" in data.model_fields_set and data.version != (clase.version if clase is not None else None):
        raise _concurrente()


def _nueva_fila(base: ClaseProgramada, filtro: FiltroGrupos, actor: Usuario, ahora) -> Clase:
    return Clase(
        id_colegio=filtro.id_colegio, id_grado=base.id_grado, id_seccion=base.id_seccion,
        id_periodo_academico=base.id_periodo_academico, tipo=TIPO_HABITUAL, fecha_original=base.fecha_original,
        fecha=base.fecha, creado_por=actor.id_usuario, creado_en=ahora,
    )


def reprogramar(db: Session, id_colegio: int, data: ReprogramacionCrear, actor: Usuario) -> ClaseItem:
    """Traslada una clase habitual (CU013). Errores: 404 `colegio_no_encontrado` y
    `clase_no_programada`, 409 `clase_con_asistencias` y `modificacion_concurrente`, 422
    `seccion_inexistente`, `validacion` (motivo vacío) y los de la fecha nueva
    (`fecha_sin_cambio`, `fecha_en_receso`, `fecha_no_lectiva`, `fecha_de_otro_periodo`,
    `fecha_ocupada`)."""
    colegio_existente(db, id_colegio)
    _seccion_del_grupo(db, id_colegio, data.id_grado, data.id_seccion)
    motivo = data.motivo.strip()
    if not motivo:
        raise error_de_campo("motivo", MENSAJE_SIN_MOTIVO)
    filtro = FiltroGrupos(id_colegio, data.id_grado, data.id_seccion)
    calendarios = cargar_calendarios(db, ids_colegio=[id_colegio])
    fila = _clase_guardada(db, filtro, data.fecha_original)
    _exigir_version(data, fila)
    if fila is not None and con_asistencias(db, [fila.id_clase]):
        raise _conflicto(MENSAJE_CON_ASISTENCIAS, "clase_con_asistencias")
    actual = _actual(db, filtro, data.fecha_original, fila, calendarios)
    _validar_fecha_nueva(db, filtro, actual, data.fecha_nueva, calendarios)

    ahora = ahora_utc()
    accion = EDITAR if fila is not None else CREAR
    fila = fila or _nueva_fila(actual, filtro, actor, ahora)
    db.add(fila)
    db.flush()
    db.add(ClaseReprogramacion(
        id_clase=fila.id_clase, fecha_anterior=fila.fecha, fecha_nueva=data.fecha_nueva, motivo=motivo,
        id_usuario=actor.id_usuario, fecha=ahora,
    ))
    registrar_auditoria(
        db, tabla=TABLA, id_registro=fila.id_clase, accion=accion, cambios={"fecha": (fila.fecha, data.fecha_nueva)},
        id_usuario=actor.id_usuario, fecha=ahora,
    )
    fila.fecha, fila.modificado_por, fila.modificado_en = data.fecha_nueva, actor.id_usuario, ahora
    if accion == EDITAR:
        fila.version += 1
    db.add(fila)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _concurrente() from None
    db.refresh(fila)
    return a_item(ClaseProgramada(
        fila.id_clase, fila.id_grado, fila.id_seccion, fila.fecha_original, fila.fecha, fila.tipo, True, False,
        fila.id_periodo_academico, fila.version,
    ))


def _actual(
    db: Session, filtro: FiltroGrupos, fecha_original: date, fila: Optional[Clase], calendarios: Calendarios
) -> ClaseProgramada:
    if fila is None:
        return _habitual_calculada(db, filtro, fecha_original, calendarios)
    return ClaseProgramada(
        fila.id_clase, fila.id_grado, fila.id_seccion, fila.fecha_original, fila.fecha, fila.tipo,
        fila.fecha != fila.fecha_original, False, fila.id_periodo_academico, fila.version,
    )


def historial(db: Session, usuario: Usuario, id_clase: int) -> list[ReprogramacionItem]:
    """Traslados de la clase, del más antiguo al más reciente."""
    clase = db.get(Clase, id_clase)
    if clase is None:
        raise _no_encontrado("La clase no existe.", "clase_no_encontrada")
    grados_visibles(db, usuario, clase.id_colegio, clase.id_grado)
    filas = db.exec(
        select(ClaseReprogramacion, Usuario)
        .join(Usuario, Usuario.id_usuario == ClaseReprogramacion.id_usuario)
        .where(ClaseReprogramacion.id_clase == id_clase)
        .order_by(col(ClaseReprogramacion.fecha), col(ClaseReprogramacion.id))
    ).all()
    return [
        ReprogramacionItem(
            id=r.id, fecha_anterior=r.fecha_anterior, fecha_nueva=r.fecha_nueva, motivo=r.motivo,
            usuario=ItemCatalogo(id=u.id_usuario, nombre=f"{u.nombres} {u.apellidos}"), fecha=r.fecha,
        )
        for r, u in filas
    ]
