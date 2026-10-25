"""cargar-calendario: año escolar, calendario general y semanas desde un JSON.

Desde CU027 el calendario académico lo gestiona el Supervisor en la API. Este comando
solo lo siembra (corre en cada arranque con `inicializar`):
- el año se busca por nombre (`anio.nombre`) y se aseguran sus periodos académicos PA1
  a PA4 (`periodo_academico`, sin fechas);
- si el año no tiene calendario general, se crea con los PA del archivo y sus recesos
  (`recesos`; si el archivo no los trae, se derivan como en la migración y el PR4 llega
  hasta `anio.fecha_fin`). Si ya existe y difiere del archivo, NO se modifica (un
  despliegue no debe pisar lo que guardó un Supervisor): queda un WARNING;
- las fechas del año NO las manda el archivo: son las de su calendario general (inicio
  del PA1 a fin del PR4) y, con ellas, sus semanas (`anio_escolar.sincronizar_anio`, el
  mismo servicio que usa la API). Así los años existentes quedan alineados al arrancar.
  Si eso deja fuera de rango semanas que ya tienen tablas de lectura, ERROR y no se toca
  nada del año.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.cli.comun import DIR_DATOS, Resumen, id_supervisor_original, leer_json
from app.core.tiempo import ahora_utc
from app.models.organizacion import AnioEscolar, Calendario, PeriodoAcademico
from app.schemas.calendario import PeriodoEntrada
from app.services.anio_escolar import (
    LUNES,
    VIERNES,
    rango_del_calendario,
    semanas_deseadas,
    semanas_en_conflicto,
    sincronizar_anio,
)
from app.services.calendario import NUMEROS_PA, calendario_general, cargar_calendarios
from app.services.calendario_gestion import (
    asegurar_periodos_academicos,
    derivar_recesos,
    errores_de_duracion,
    errores_de_periodos,
    insertar_calendario,
    resolver_propuesta,
)

ARCHIVO_CALENDARIO = DIR_DATOS / "calendario_2026.json"
__all__ = ["cargar_calendario", "leer_calendario", "semanas_deseadas", "validar_calendario"]


@dataclass(frozen=True)
class PeriodoArchivo:
    numero: int
    inicio: date
    fin: date


@dataclass(frozen=True)
class RecesoArchivo:
    numero: int
    inicio: date
    fin: date


@dataclass(frozen=True)
class CalendarioArchivo:
    nombre: str
    periodos: tuple[PeriodoArchivo, ...]
    recesos: tuple[RecesoArchivo, ...] = ()
    # Solo sin `recesos`: hasta dónde llega el PR4 derivado (`anio.fecha_fin`). Las fechas
    # del año no salen del archivo.
    fin_pr4: Optional[date] = None


# ── Lectura y validación (sin BD) ───────────────────────────────────────────────────


def _rango(datos: dict) -> tuple[date, date]:
    return date.fromisoformat(datos["fecha_inicio"]), date.fromisoformat(datos["fecha_fin"])


def leer_calendario(datos: dict) -> tuple[Optional[CalendarioArchivo], list[str]]:
    """Convierte el JSON en un calendario. Devuelve (None, errores) si no se puede leer."""
    try:
        anio = datos["anio"]
        periodos = [
            PeriodoArchivo(int(p["numero"]), *_rango(p))
            for p in datos["periodos"]
        ]
        recesos = [RecesoArchivo(int(r["numero"]), *_rango(r)) for r in datos.get("recesos", [])]
        calendario = CalendarioArchivo(
            nombre=str(anio["nombre"]),
            periodos=tuple(sorted(periodos, key=lambda p: p.inicio)),
            recesos=tuple(sorted(recesos, key=lambda r: r.numero)),
            fin_pr4=date.fromisoformat(anio["fecha_fin"]) if anio.get("fecha_fin") else None,
        )
    except (KeyError, TypeError, ValueError) as e:
        return None, [f"formato inválido del calendario: {e!r}"]
    return calendario, validar_calendario(calendario)


def _errores_periodo(p: PeriodoArchivo) -> list[str]:
    errores = []
    if p.inicio > p.fin:
        errores.append(f"periodo {p.numero}: empieza ({p.inicio}) después de terminar ({p.fin})")
    if p.inicio.weekday() != LUNES:
        errores.append(f"periodo {p.numero}: {p.inicio} no es lunes")
    if p.fin.weekday() != VIERNES:
        errores.append(f"periodo {p.numero}: {p.fin} no es viernes")
    return errores


def validar_calendario(cal: CalendarioArchivo) -> list[str]:
    errores = []
    numeros = [p.numero for p in cal.periodos]
    if sorted(numeros) != list(NUMEROS_PA):
        errores.append(f"año {cal.nombre}: debe tener los periodos 1 a 4, una vez cada uno (tiene {numeros})")
    if not cal.recesos and cal.fin_pr4 is None:
        errores.append(f"año {cal.nombre}: sin `recesos`, `anio.fecha_fin` indica hasta dónde llega el PR4")
    for p in cal.periodos:
        errores += _errores_periodo(p)
    # Ordenados por inicio: entre un PA y el siguiente debe quedar al menos un día de receso.
    for anterior, siguiente in zip(cal.periodos, cal.periodos[1:]):
        if siguiente.inicio <= anterior.fin + timedelta(days=1):
            errores.append(
                f"los periodos {anterior.numero} y {siguiente.numero} se solapan o no dejan días para el receso"
            )
    if errores:
        return errores
    # Los 8 periodos (con los recesos del archivo o derivados) en orden, sin solaparse y
    # en no más de 12 meses (como en la API).
    periodos = periodos_del_archivo(cal)
    errores = errores_de_periodos(periodos) or errores_de_duracion(periodos)
    return [f"año {cal.nombre}: {campo}: {mensaje}" for campo, mensaje in errores]


def periodos_del_archivo(cal: CalendarioArchivo) -> list[PeriodoEntrada]:
    """Los 8 periodos del calendario general: los PA del archivo y sus recesos (los del
    archivo o, si no trae ninguno, derivados de los huecos entre PA)."""
    if not cal.recesos:
        return derivar_recesos([(p.inicio, p.fin) for p in cal.periodos], cal.fin_pr4)
    return [
        *(PeriodoEntrada(tipo="PA", numero=p.numero, fecha_inicio=p.inicio, fecha_fin=p.fin) for p in cal.periodos),
        *(PeriodoEntrada(tipo="PR", numero=r.numero, fecha_inicio=r.inicio, fecha_fin=r.fin) for r in cal.recesos),
    ]


# ── Escritura ───────────────────────────────────────────────────────────────────────


def _cargar_anio(
    db: Session, cal: CalendarioArchivo, rango: tuple[date, date], auditoria: dict, resumen: Resumen
) -> AnioEscolar:
    """El año por nombre; si no existe, se crea con el rango del calendario general."""
    anio = db.exec(select(AnioEscolar).where(AnioEscolar.nombre == cal.nombre)).first()
    if anio is None:
        anio = AnioEscolar(nombre=cal.nombre, fecha_inicio=rango[0], fecha_fin=rango[1], **auditoria)
        db.add(anio)
        db.flush()
        resumen.creados["año_escolar"] += 1
    return anio


def _cargar_periodos(
    db: Session, cal: CalendarioArchivo, anio: AnioEscolar, auditoria: dict, resumen: Resumen
) -> None:
    """PA1 a PA4 del año (identidad, sin fechas), creando los que falten."""
    antes = {p.numero for p in db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar))}
    for numero in sorted(antes - set(NUMEROS_PA)):
        resumen.advertir(f"año {cal.nombre}: el periodo {numero} existe en la BD pero no en el archivo")
    ids = asegurar_periodos_academicos(db, anio.id_anio_escolar, auditoria["creado_por"], auditoria["creado_en"])
    resumen.creados["periodo_academico"] += len(set(ids) - antes)


def _mismas_fechas(existente: list, propuestos: list[PeriodoEntrada]) -> bool:
    actuales = {(p.tipo, p.numero, p.fecha_inicio, p.fecha_fin) for p in existente}
    return actuales == {(p.tipo, p.numero, p.fecha_inicio, p.fecha_fin) for p in propuestos}


def _cargar_calendario_general(
    db: Session, cal: CalendarioArchivo, anio: AnioEscolar, auditoria: dict, resumen: Resumen
) -> None:
    """Crea el calendario general del año si no existe; si existe, solo avisa si difiere."""
    propuestos = periodos_del_archivo(cal)
    existente = calendario_general(db, anio.id_anio_escolar)
    if existente is None:
        borrador = Calendario(id_calendario=0, id_anio_escolar=anio.id_anio_escolar, version=1)
        propuesta = resolver_propuesta(borrador, propuestos, [], {}, {})
        insertar_calendario(db, anio.id_anio_escolar, None, propuesta, auditoria["creado_por"], auditoria["creado_en"])
        resumen.creados["calendario"] += 1
        return
    general = cargar_calendarios(db, anio.id_anio_escolar, ids_colegio=[]).generales.get(anio.id_anio_escolar)
    if general is None or not _mismas_fechas(list(general.periodos), propuestos):
        resumen.advertir(
            f"calendario general {cal.nombre}: difiere del archivo y no se modifica "
            "(desde CU027 lo gestiona el Supervisor en la API)"
        )


def _rango_del_general(db: Session, anio: Optional[AnioEscolar], cal: CalendarioArchivo) -> tuple[date, date]:
    """Fechas que tendrá el año: las del general que ya existe o, si no, las del archivo."""
    if anio is not None:
        general = cargar_calendarios(db, anio.id_anio_escolar, ids_colegio=[]).generales.get(anio.id_anio_escolar)
        if general is not None:
            return rango_del_calendario(general.periodos)
    return rango_del_calendario(periodos_del_archivo(cal))


def _sincronizar(
    db: Session, anio: AnioEscolar, rango: tuple[date, date], auditoria: dict, resumen: Resumen
) -> None:
    cambios = sincronizar_anio(db, anio, *rango, auditoria["creado_por"], auditoria["creado_en"])
    if cambios.fechas and resumen.creados["año_escolar"] == 0:
        resumen.actualizados["año_escolar"] += 1
    resumen.creados["semana_reporte"] += cambios.semanas["creadas"]
    resumen.actualizados["semana_reporte"] += cambios.semanas["actualizadas"]
    resumen.eliminados["semana_reporte"] += cambios.semanas["eliminadas"]


def _escribir(db: Session, cal: CalendarioArchivo, rango: tuple[date, date], auditoria: dict, resumen: Resumen) -> None:
    anio = _cargar_anio(db, cal, rango, auditoria, resumen)
    _cargar_periodos(db, cal, anio, auditoria, resumen)
    _cargar_calendario_general(db, cal, anio, auditoria, resumen)
    _sincronizar(db, anio, rango, auditoria, resumen)
    db.commit()


def cargar_calendario(db: Session, archivo: Path = ARCHIVO_CALENDARIO) -> Resumen:
    resumen = Resumen()
    datos = leer_json(archivo)
    if datos is None:
        resumen.error(f"No se pudo leer el calendario {archivo}")
        return resumen
    cal, errores = leer_calendario(datos)
    if errores:
        for mensaje in errores:
            resumen.error(f"Calendario {archivo} inválido, no se escribe nada: {mensaje}")
        return resumen

    id_supervisor = id_supervisor_original(db)
    if id_supervisor is None:
        resumen.advertir("No se carga el calendario: todavía no existe el Supervisor original")
        return resumen

    anio = db.exec(select(AnioEscolar).where(AnioEscolar.nombre == cal.nombre)).first()
    rango = _rango_del_general(db, anio, cal)
    conflictos = semanas_en_conflicto(db, anio, *rango)
    if conflictos:
        for tabla in conflictos:
            resumen.error(
                f"Calendario {cal.nombre} no se aplica: la semana {tabla.id_semana} tiene registros y quedaría "
                "fuera de rango"
            )
        return resumen

    auditoria: dict[str, int | datetime] = {"creado_por": id_supervisor, "creado_en": ahora_utc()}
    _escribir(db, cal, rango, auditoria, resumen)
    return resumen
