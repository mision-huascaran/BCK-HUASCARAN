"""cargar-calendario: año escolar, periodos, cortes diagnósticos y semanas desde un JSON.

El archivo es la fuente de verdad del calendario (no hay pantalla para editarlo):
- el año se busca por nombre, los periodos por (año, número) y hay una
  evaluacion_diagnostica (corte) por periodo;
- si un año o periodo existe con otras fechas, se actualizan y se registra el cambio,
  SALVO que eso deje fuera de rango semanas que ya tienen registros (asistencia, rúbrica
  o reporte): en ese caso ERROR y no se toca nada de ese calendario;
- semana_reporte se genera de lunes a viernes solo dentro de los periodos, con
  numero_semana correlativo en el año. Las semanas sin registros que quedan fuera del
  calendario se eliminan: son datos generados por este comando, no capturados.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from sqlmodel import Session, col, select

from app.cli.comun import DIR_DATOS, Resumen, id_supervisor_original, leer_json
from app.core.tiempo import ahora_utc
from app.models.evaluacion import (
    AsistenciaSemanal,
    EvaluacionDiagnostica,
    ReporteSemanalAlumno,
    RubricaRegistroSemanal,
    SemanaReporte,
)
from app.models.organizacion import AnioEscolar, PeriodoAcademico

ARCHIVO_CALENDARIO = DIR_DATOS / "calendario_2026.json"
LUNES, VIERNES = 0, 4


@dataclass(frozen=True)
class PeriodoArchivo:
    numero: int
    inicio: date
    fin: date
    corte: date


@dataclass(frozen=True)
class CalendarioArchivo:
    nombre: str
    inicio: date
    fin: date
    periodos: tuple[PeriodoArchivo, ...]


@dataclass(frozen=True)
class SemanaDeseada:
    numero_periodo: int
    lunes: date
    viernes: date
    numero_semana: int


# ── Lectura y validación (sin BD) ───────────────────────────────────────────────────


def leer_calendario(datos: dict) -> tuple[Optional[CalendarioArchivo], list[str]]:
    """Convierte el JSON en un calendario. Devuelve (None, errores) si no se puede leer."""
    try:
        anio = datos["anio"]
        periodos = [
            PeriodoArchivo(
                numero=int(p["numero"]),
                inicio=date.fromisoformat(p["fecha_inicio"]),
                fin=date.fromisoformat(p["fecha_fin"]),
                corte=date.fromisoformat(p["corte_diagnostico"]),
            )
            for p in datos["periodos"]
        ]
        calendario = CalendarioArchivo(
            nombre=str(anio["nombre"]),
            inicio=date.fromisoformat(anio["fecha_inicio"]),
            fin=date.fromisoformat(anio["fecha_fin"]),
            periodos=tuple(sorted(periodos, key=lambda p: p.inicio)),
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
    if not p.inicio <= p.corte <= p.fin:
        errores.append(f"periodo {p.numero}: el corte {p.corte} no cae dentro del periodo")
    return errores


def validar_calendario(cal: CalendarioArchivo) -> list[str]:
    errores = []
    if cal.inicio > cal.fin:
        errores.append(f"año {cal.nombre}: empieza ({cal.inicio}) después de terminar ({cal.fin})")
    if not cal.periodos:
        errores.append(f"año {cal.nombre}: no tiene periodos")
    numeros = [p.numero for p in cal.periodos]
    if len(set(numeros)) != len(numeros):
        errores.append(f"año {cal.nombre}: números de periodo repetidos {numeros}")
    for p in cal.periodos:
        errores += _errores_periodo(p, cal)
    # Ordenados por inicio: basta comparar cada periodo con el anterior.
    for anterior, siguiente in zip(cal.periodos, cal.periodos[1:]):
        if siguiente.inicio <= anterior.fin:
            errores.append(f"los periodos {anterior.numero} y {siguiente.numero} se solapan")
    return errores


def semanas_deseadas(cal: CalendarioArchivo) -> list[SemanaDeseada]:
    """Semanas de lunes a viernes dentro de cada periodo, numeradas 1..N en el año."""
    semanas = []
    for p in cal.periodos:
        lunes = p.inicio
        while lunes + timedelta(days=VIERNES) <= p.fin:
            semanas.append((p.numero, lunes, lunes + timedelta(days=VIERNES)))
            lunes += timedelta(days=7)
    return [SemanaDeseada(n, lunes, viernes, i) for i, (n, lunes, viernes) in enumerate(semanas, 1)]


# ── Escritura ───────────────────────────────────────────────────────────────────────


def _semanas_con_registros(db: Session, ids_semana: list[int]) -> set[int]:
    if not ids_semana:
        return set()
    con_registros = set()
    for modelo in (AsistenciaSemanal, RubricaRegistroSemanal, ReporteSemanalAlumno):
        con_registros.update(
            db.exec(select(modelo.id_semana).where(col(modelo.id_semana).in_(ids_semana))).all()
        )
    return con_registros


def _conflictos(
    semanas_bd: list[SemanaReporte],
    con_registros: set[int],
    numero_de_periodo: dict[int, int],
    deseadas: dict[date, SemanaDeseada],
) -> list[str]:
    """Semanas con registros que el calendario nuevo movería o dejaría fuera de rango."""
    conflictos = []
    for semana in semanas_bd:
        if semana.id_semana not in con_registros:
            continue
        deseada = deseadas.get(semana.fecha_inicio)
        numero_actual = numero_de_periodo.get(semana.id_periodo_academico)
        if deseada is None or deseada.numero_periodo != numero_actual or deseada.viernes != semana.fecha_fin:
            conflictos.append(
                f"semana {semana.id_semana} ({semana.fecha_inicio}..{semana.fecha_fin}) tiene "
                "registros y quedaría fuera de rango o en otro periodo"
            )
    return conflictos


def _ajustar_fechas(fila, inicio: date, fin: date, descripcion: str, resumen: Resumen) -> bool:
    if (fila.fecha_inicio, fila.fecha_fin) == (inicio, fin):
        return False
    resumen.advertir(
        f"{descripcion}: fechas {fila.fecha_inicio}..{fila.fecha_fin} -> {inicio}..{fin} "
        "(el archivo de calendario manda)"
    )
    fila.fecha_inicio, fila.fecha_fin = inicio, fin
    resumen.actualizados[fila.__tablename__] += 1
    return True


def _cargar_anio(db: Session, cal: CalendarioArchivo, auditoria: dict, resumen: Resumen) -> AnioEscolar:
    anio = db.exec(select(AnioEscolar).where(AnioEscolar.nombre == cal.nombre)).first()
    if anio is None:
        anio = AnioEscolar(nombre=cal.nombre, fecha_inicio=cal.inicio, fecha_fin=cal.fin, **auditoria)
        db.add(anio)
        db.flush()
        resumen.creados["año_escolar"] += 1
    elif _ajustar_fechas(anio, cal.inicio, cal.fin, f"año {cal.nombre}", resumen):
        anio.modificado_por, anio.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
    return anio


def _cargar_periodos(
    db: Session, cal: CalendarioArchivo, anio: AnioEscolar, auditoria: dict, resumen: Resumen
) -> dict[int, PeriodoAcademico]:
    existentes = {
        p.numero: p
        for p in db.exec(
            select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar)
        ).all()
    }
    for numero in sorted(set(existentes) - {p.numero for p in cal.periodos}):
        resumen.advertir(f"año {cal.nombre}: el periodo {numero} existe en la BD pero no en el archivo")
    for p in cal.periodos:
        periodo = existentes.get(p.numero)
        if periodo is None:
            periodo = PeriodoAcademico(
                id_anio_escolar=anio.id_anio_escolar, numero=p.numero,
                fecha_inicio=p.inicio, fecha_fin=p.fin, **auditoria,
            )
            db.add(periodo)
            resumen.creados["periodo_academico"] += 1
            existentes[p.numero] = periodo
        elif _ajustar_fechas(periodo, p.inicio, p.fin, f"periodo {cal.nombre}-{p.numero}", resumen):
            periodo.modificado_por, periodo.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
    db.flush()
    return existentes


def _cargar_cortes(
    db: Session, cal: CalendarioArchivo, periodos: dict[int, PeriodoAcademico], auditoria: dict, resumen: Resumen
) -> None:
    for p in cal.periodos:
        id_periodo = periodos[p.numero].id_periodo_academico
        corte = db.exec(
            select(EvaluacionDiagnostica).where(EvaluacionDiagnostica.id_periodo_academico == id_periodo)
        ).first()
        if corte is None:
            db.add(EvaluacionDiagnostica(id_periodo_academico=id_periodo, fecha_realizacion=p.corte, **auditoria))
            resumen.creados["evaluacion_diagnostica"] += 1
        elif corte.fecha_realizacion != p.corte:
            resumen.advertir(
                f"corte del periodo {cal.nombre}-{p.numero}: {corte.fecha_realizacion} -> {p.corte} "
                "(el archivo de calendario manda)"
            )
            corte.fecha_realizacion = p.corte
            corte.modificado_por, corte.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
            resumen.actualizados["evaluacion_diagnostica"] += 1


def _sincronizar_semanas(
    db: Session,
    anio: AnioEscolar,
    semanas_bd: list[SemanaReporte],
    deseadas: dict[date, SemanaDeseada],
    periodos: dict[int, PeriodoAcademico],
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
        valores = (periodos[deseada.numero_periodo].id_periodo_academico, deseada.viernes, deseada.numero_semana)
        if (semana.id_periodo_academico, semana.fecha_fin, semana.numero_semana) != valores:
            semana.id_periodo_academico, semana.fecha_fin, semana.numero_semana = valores
            semana.modificado_por, semana.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
            resumen.actualizados["semana_reporte"] += 1
    for deseada in pendientes.values():
        db.add(
            SemanaReporte(
                id_anio_escolar=anio.id_anio_escolar,
                id_periodo_academico=periodos[deseada.numero_periodo].id_periodo_academico,
                numero_semana=deseada.numero_semana,
                fecha_inicio=deseada.lunes,
                fecha_fin=deseada.viernes,
                **auditoria,
            )
        )
        resumen.creados["semana_reporte"] += 1


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

    deseadas = {s.lunes: s for s in semanas_deseadas(cal)}
    anio = db.exec(select(AnioEscolar).where(AnioEscolar.nombre == cal.nombre)).first()
    semanas_bd: list[SemanaReporte] = []
    if anio is not None:
        semanas_bd = db.exec(select(SemanaReporte).where(SemanaReporte.id_anio_escolar == anio.id_anio_escolar)).all()
        numero_de_periodo = {
            p.id_periodo_academico: p.numero
            for p in db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar))
        }
        con_registros = _semanas_con_registros(db, [s.id_semana for s in semanas_bd])
        conflictos = _conflictos(semanas_bd, con_registros, numero_de_periodo, deseadas)
        if conflictos:
            for mensaje in conflictos:
                resumen.error(f"Calendario {cal.nombre} no se aplica: {mensaje}")
            return resumen

    auditoria = {"creado_por": id_supervisor, "creado_en": ahora_utc()}
    anio = _cargar_anio(db, cal, auditoria, resumen)
    periodos = _cargar_periodos(db, cal, anio, auditoria, resumen)
    _cargar_cortes(db, cal, periodos, auditoria, resumen)
    _sincronizar_semanas(db, anio, semanas_bd, deseadas, periodos, auditoria, resumen)
    db.commit()
    return resumen
