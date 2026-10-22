"""cargar-feriados: feriados nacionales de Perú como días no lectivos del calendario
GENERAL de cada año escolar (CU027), con origen 'automatico'.

- Solo los que caen de lunes a viernes (sábados y domingos ya no son lectivos) y dentro
  de un PA del general: un feriado en un receso no se carga.
- No duplica: si la fecha ya está cubierta por un día no lectivo del calendario, no se
  inserta (si es el mismo día con otro motivo, queda un WARNING).
- No vuelve no lectiva una fecha con asistencias de un colegio que usa el general
  (CU027): ese feriado se omite con un WARNING para que el Supervisor lo revise.
- Si inserta alguno, sube la `version` del calendario: una edición abierta desde la API
  con la versión anterior recibe 409 en vez de borrar los feriados nuevos.
- Los calendarios particulares no se tocan: al crearse copian los días del general.
No se cargan días decretados ni vacaciones: esos los registra el Supervisor.
"""
from datetime import date, datetime
from typing import Optional

import holidays
from sqlmodel import Session, col, select

from app.cli.comun import Resumen, id_supervisor_original
from app.core.tiempo import ahora_utc
from app.models.evaluacion import Asistencia, Clase
from app.models.organizacion import ORIGEN_AUTOMATICO, Calendario, Colegio, DiaNoLectivo
from app.services.calendario import CalendarioResuelto, Calendarios, cargar_calendarios

PAIS = "PE"
# Fijo: sin él, la librería puede devolver los nombres en el idioma de la máquina y no
# coincidirían con los ya guardados.
IDIOMA = "es"
PREFIJO_MOTIVO = "Feriado nacional: "
VIERNES = 4
TABLA = "dia_no_lectivo"


def feriados_entre_semana(anios: set[int]) -> dict:
    """{fecha: motivo} de los feriados de Perú que caen de lunes a viernes."""
    if not anios:
        return {}
    calendario = holidays.country_holidays(PAIS, years=sorted(anios), language=IDIOMA)
    return {
        fecha: PREFIJO_MOTIVO + nombre
        for fecha, nombre in sorted(calendario.items())
        if fecha.weekday() <= VIERNES
    }


def _fechas_con_asistencias(db: Session, calendarios: Calendarios, general: CalendarioResuelto) -> set[date]:
    """Fechas del calendario con asistencias de algún colegio que usa el general."""
    particulares = {c for (a, c) in calendarios.particulares if a == general.id_anio_escolar}
    usan_general = select(Colegio.id_colegio).where(col(Colegio.id_colegio).not_in(particulares))
    return set(
        db.exec(
            select(Clase.fecha).where(
                col(Clase.id_colegio).in_(usan_general),
                Clase.fecha >= general.fecha_inicio,
                Clase.fecha <= general.fecha_fin,
                col(Clase.id_clase).in_(select(Asistencia.id_clase)),
            )
        ).all()
    )


def _omision(general: CalendarioResuelto, fecha: date, motivo: str, con_asistencias: set[date]) -> Optional[str]:
    """Por qué no se carga el feriado ("" = se omite sin aviso), o None si se carga."""
    periodo = general.periodo_de(fecha)
    if periodo is None or not periodo.es_pa:
        return ""
    existente = general.no_lectivo_de(fecha)
    if existente is not None:
        mismo_dia = existente.fecha_inicio == existente.fecha_fin == fecha
        if mismo_dia and existente.motivo != motivo:
            return f"ya existe con motivo '{existente.motivo}' (la librería dice '{motivo}'). No se modifica."
        return ""
    if fecha in con_asistencias:
        return "tiene asistencias registradas: no se vuelve no lectiva. Revisarlo en el calendario."
    return None


def _cargar_en(
    db: Session, fila: Calendario, general: CalendarioResuelto, calendarios: Calendarios,
    id_supervisor: int, ahora: datetime, resumen: Resumen,
) -> None:
    con_asistencias = _fechas_con_asistencias(db, calendarios, general)
    anios = set(range(general.fecha_inicio.year, general.fecha_fin.year + 1))
    nuevos = 0
    for fecha, motivo in feriados_entre_semana(anios).items():
        omision = _omision(general, fecha, motivo, con_asistencias)
        if omision:
            resumen.advertir(f"{TABLA} {fecha} (calendario general {fila.id_anio_escolar}): {omision}")
        if omision is not None:
            continue
        db.add(DiaNoLectivo(
            id_calendario=fila.id_calendario, motivo=motivo, fecha_inicio=fecha, fecha_fin=fecha,
            origen=ORIGEN_AUTOMATICO, creado_por=id_supervisor, creado_en=ahora,
        ))
        nuevos += 1
    if nuevos:
        fila.version += 1
        fila.modificado_por, fila.modificado_en = id_supervisor, ahora
        db.add(fila)
        resumen.creados[TABLA] += nuevos


def cargar_feriados(db: Session) -> Resumen:
    resumen = Resumen()
    id_supervisor = id_supervisor_original(db)
    if id_supervisor is None:
        resumen.advertir("No se cargan feriados: todavía no existe el Supervisor original")
        return resumen

    generales = db.exec(select(Calendario).where(col(Calendario.id_colegio).is_(None))).all()
    if not generales:
        resumen.omitir("feriados: no hay ningún calendario general en la BD")
        return resumen

    calendarios = cargar_calendarios(db)
    ahora = ahora_utc()
    for fila in generales:
        general = calendarios.generales.get(fila.id_anio_escolar)
        if general is not None:
            _cargar_en(db, fila, general, calendarios, id_supervisor, ahora, resumen)
    db.commit()
    return resumen
