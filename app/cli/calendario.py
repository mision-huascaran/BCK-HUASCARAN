"""cargar-calendario: año escolar, calendario general y semanas desde un JSON.

Desde CU027 el calendario académico lo gestiona el Supervisor en la API. Este comando
solo lo siembra:
- el año se busca por nombre (sus fechas sí las manda el archivo) y se aseguran sus
  periodos académicos PA1 a PA4 (`periodo_academico`, sin fechas);
- si el año no tiene calendario general, se crea con los PA del archivo y sus recesos
  (`recesos`; si el archivo no los trae, se derivan como en la migración). Si ya existe y
  difiere del archivo, NO se modifica (un despliegue no debe pisar lo que guardó un
  Supervisor): queda un WARNING;
- semana_reporte se genera de lunes a viernes en todo el rango del año escolar (toda
  semana con al menos un día hábil dentro del año), sin depender de los PA, con
  numero_semana correlativo en el año. Si eso deja fuera de rango semanas que ya tienen
  tablas de lectura, ERROR y no se toca nada del año. Las semanas sin tablas que quedan
  fuera se eliminan: son datos generados.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlmodel import Session, col, select

from app.cli.comun import DIR_DATOS, Resumen, id_supervisor_original, leer_json
from app.core.tiempo import ahora_utc
from app.models.evaluacion import LecturaSemanal, SemanaReporte
from app.models.organizacion import AnioEscolar, Calendario, PeriodoAcademico
from app.schemas.calendario import PeriodoEntrada
from app.services.calendario import NUMEROS_PA, calendario_general, cargar_calendarios
from app.services.calendario_gestion import (
    asegurar_periodos_academicos,
    derivar_recesos,
    errores_de_periodos,
    insertar_calendario,
    resolver_propuesta,
)

ARCHIVO_CALENDARIO = DIR_DATOS / "calendario_2026.json"
LUNES, VIERNES = 0, 4
UNA_SEMANA = timedelta(days=7)


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
    inicio: date
    fin: date
    periodos: tuple[PeriodoArchivo, ...]
    recesos: tuple[RecesoArchivo, ...] = ()


@dataclass(frozen=True)
class SemanaDeseada:
    lunes: date
    viernes: date
    numero_semana: int


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
            inicio=date.fromisoformat(anio["fecha_inicio"]),
            fin=date.fromisoformat(anio["fecha_fin"]),
            periodos=tuple(sorted(periodos, key=lambda p: p.inicio)),
            recesos=tuple(sorted(recesos, key=lambda r: r.numero)),
        )
    except (KeyError, TypeError, ValueError) as e:
        return None, [f"formato inválido del calendario: {e!r}"]
    return calendario, validar_calendario(calendario)


def _errores_periodo(p: PeriodoArchivo, cal: CalendarioArchivo) -> list[str]:
    errores = []
    if p.inicio > p.fin:
        errores.append(f"periodo {p.numero}: empieza ({p.inicio}) después de terminar ({p.fin})")
    if p.inicio.weekday() != LUNES:
        errores.append(f"periodo {p.numero}: {p.inicio} no es lunes")
    if p.fin.weekday() != VIERNES:
        errores.append(f"periodo {p.numero}: {p.fin} no es viernes")
    if p.inicio < cal.inicio or p.fin > cal.fin:
        errores.append(f"periodo {p.numero}: {p.inicio}..{p.fin} sale del año {cal.inicio}..{cal.fin}")
    return errores


def validar_calendario(cal: CalendarioArchivo) -> list[str]:
    errores = []
    if cal.inicio > cal.fin:
        errores.append(f"año {cal.nombre}: empieza ({cal.inicio}) después de terminar ({cal.fin})")
    numeros = [p.numero for p in cal.periodos]
    if sorted(numeros) != list(NUMEROS_PA):
        errores.append(f"año {cal.nombre}: debe tener los periodos 1 a 4, una vez cada uno (tiene {numeros})")
    for p in cal.periodos:
        errores += _errores_periodo(p, cal)
    # Ordenados por inicio: entre un PA y el siguiente debe quedar al menos un día de receso.
    for anterior, siguiente in zip(cal.periodos, cal.periodos[1:]):
        if siguiente.inicio <= anterior.fin + timedelta(days=1):
            errores.append(
                f"los periodos {anterior.numero} y {siguiente.numero} se solapan o no dejan días para el receso"
            )
    if errores:
        return errores
    # Los 8 periodos (con los recesos del archivo o derivados) en orden y sin solaparse.
    return [f"año {cal.nombre}: {campo}: {mensaje}" for campo, mensaje in errores_de_periodos(periodos_del_archivo(cal))]


def periodos_del_archivo(cal: CalendarioArchivo) -> list[PeriodoEntrada]:
    """Los 8 periodos del calendario general: los PA del archivo y sus recesos (los del
    archivo o, si no trae ninguno, derivados de los huecos entre PA)."""
    if not cal.recesos:
        return derivar_recesos([(p.inicio, p.fin) for p in cal.periodos], cal.fin)
    return [
        *(PeriodoEntrada(tipo="PA", numero=p.numero, fecha_inicio=p.inicio, fecha_fin=p.fin) for p in cal.periodos),
        *(PeriodoEntrada(tipo="PR", numero=r.numero, fecha_inicio=r.inicio, fecha_fin=r.fin) for r in cal.recesos),
    ]


def semanas_deseadas(inicio: date, fin: date) -> list[SemanaDeseada]:
    """Semanas de lunes a viernes con al menos un día hábil en [inicio, fin] (el año
    escolar), numeradas 1..N."""
    lunes = inicio - timedelta(days=inicio.weekday() - LUNES)
    if lunes + timedelta(days=VIERNES) < inicio:
        lunes += UNA_SEMANA
    semanas = []
    while lunes <= fin:
        semanas.append(SemanaDeseada(lunes, lunes + timedelta(days=VIERNES), len(semanas) + 1))
        lunes += UNA_SEMANA
    return semanas


# ── Escritura ───────────────────────────────────────────────────────────────────────


def _semanas_con_registros(db: Session, ids_semana: list[int]) -> set[int]:
    if not ids_semana:
        return set()
    return set(db.exec(select(LecturaSemanal.id_semana).where(col(LecturaSemanal.id_semana).in_(ids_semana))).all())


def _conflictos(
    semanas_bd: list[SemanaReporte], con_registros: set[int], deseadas: dict[date, SemanaDeseada]
) -> list[str]:
    """Semanas con tablas de lectura que el año dejaría fuera de rango."""
    return [
        f"semana {semana.id_semana} ({semana.fecha_inicio}..{semana.fecha_fin}) tiene registros y quedaría "
        "fuera de rango"
        for semana in semanas_bd
        if semana.id_semana in con_registros and semana.fecha_inicio not in deseadas
    ]


def _cargar_anio(db: Session, cal: CalendarioArchivo, auditoria: dict, resumen: Resumen) -> AnioEscolar:
    anio = db.exec(select(AnioEscolar).where(AnioEscolar.nombre == cal.nombre)).first()
    if anio is None:
        anio = AnioEscolar(nombre=cal.nombre, fecha_inicio=cal.inicio, fecha_fin=cal.fin, **auditoria)
        db.add(anio)
        db.flush()
        resumen.creados["año_escolar"] += 1
    elif (anio.fecha_inicio, anio.fecha_fin) != (cal.inicio, cal.fin):
        resumen.advertir(
            f"año {cal.nombre}: fechas {anio.fecha_inicio}..{anio.fecha_fin} -> {cal.inicio}..{cal.fin} "
            "(el archivo de calendario manda)"
        )
        anio.fecha_inicio, anio.fecha_fin = cal.inicio, cal.fin
        anio.modificado_por, anio.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
        resumen.actualizados["año_escolar"] += 1
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


def _sincronizar_semanas(
    db: Session,
    anio: AnioEscolar,
    semanas_bd: list[SemanaReporte],
    deseadas: dict[date, SemanaDeseada],
    auditoria: dict,
    resumen: Resumen,
) -> None:
    pendientes = dict(deseadas)
    for semana in semanas_bd:
        deseada = pendientes.pop(semana.fecha_inicio, None)
        if deseada is None:
            # Sin registros (lo garantiza _conflictos): era una semana generada que ya no aplica.
            db.delete(semana)
            resumen.eliminados["semana_reporte"] += 1
            continue
        valores = (deseada.viernes, deseada.numero_semana)
        if (semana.fecha_fin, semana.numero_semana) != valores:
            semana.fecha_fin, semana.numero_semana = valores
            semana.modificado_por, semana.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
            resumen.actualizados["semana_reporte"] += 1
    for deseada in pendientes.values():
        db.add(
            SemanaReporte(
                id_anio_escolar=anio.id_anio_escolar,
                numero_semana=deseada.numero_semana,
                fecha_inicio=deseada.lunes,
                fecha_fin=deseada.viernes,
                **auditoria,
            )
        )
        resumen.creados["semana_reporte"] += 1


def _conflictos_de_semanas(
    db: Session, anio: Optional[AnioEscolar], deseadas: dict[date, SemanaDeseada]
) -> tuple[list[SemanaReporte], list[str]]:
    if anio is None:
        return [], []
    semanas_bd = list(db.exec(select(SemanaReporte).where(SemanaReporte.id_anio_escolar == anio.id_anio_escolar)).all())
    con_registros = _semanas_con_registros(db, [s.id_semana for s in semanas_bd])
    return semanas_bd, _conflictos(semanas_bd, con_registros, deseadas)


def _escribir(
    db: Session, cal: CalendarioArchivo, semanas: tuple[list[SemanaReporte], dict[date, SemanaDeseada]],
    auditoria: dict, resumen: Resumen,
) -> None:
    anio = _cargar_anio(db, cal, auditoria, resumen)
    _cargar_periodos(db, cal, anio, auditoria, resumen)
    _cargar_calendario_general(db, cal, anio, auditoria, resumen)
    _sincronizar_semanas(db, anio, *semanas, auditoria, resumen)
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
    deseadas = {s.lunes: s for s in semanas_deseadas(cal.inicio, cal.fin)}
    semanas_bd, conflictos = _conflictos_de_semanas(db, anio, deseadas)
    if conflictos:
        for mensaje in conflictos:
            resumen.error(f"Calendario {cal.nombre} no se aplica: {mensaje}")
        return resumen

    auditoria: dict[str, int | datetime] = {"creado_por": id_supervisor, "creado_en": ahora_utc()}
    _escribir(db, cal, (semanas_bd, deseadas), auditoria, resumen)
    return resumen
