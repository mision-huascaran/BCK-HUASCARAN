"""cargar-feriados: feriados nacionales de Perú en dia_no_laborable, por cada año escolar.

Solo se cargan los que caen de lunes a viernes (sábados y domingos ya se excluyen por
cálculo). No se cargan días decretados ni vacaciones: el cálculo de días hábiles
considerará los días de lunes a viernes, dentro de algún periodo académico y que no
estén en dia_no_laborable.
"""
import holidays
from sqlmodel import Session, select

from app.cli.comun import Resumen, id_supervisor_original
from app.core.tiempo import ahora_utc
from app.models.organizacion import AnioEscolar, DiaNoLaborable

PAIS = "PE"
# Fijo: sin él, la librería puede devolver los nombres en el idioma de la máquina y no
# coincidirían con los ya guardados.
IDIOMA = "es"
PREFIJO_MOTIVO = "Feriado nacional: "
VIERNES = 4


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


def cargar_feriados(db: Session) -> Resumen:
    resumen = Resumen()
    id_supervisor = id_supervisor_original(db)
    if id_supervisor is None:
        resumen.advertir("No se cargan feriados: todavía no existe el Supervisor original")
        return resumen

    anios = set()
    for anio in db.exec(select(AnioEscolar)).all():
        anios.update(range(anio.fecha_inicio.year, anio.fecha_fin.year + 1))
    if not anios:
        resumen.omitir("feriados: no hay ningún año escolar en la BD")
        return resumen

    ahora = ahora_utc()
    for fecha, motivo in feriados_entre_semana(anios).items():
        existente = db.get(DiaNoLaborable, fecha)
        if existente is None:
            db.add(DiaNoLaborable(fecha=fecha, motivo=motivo, creado_por=id_supervisor, creado_en=ahora))
            resumen.creados["dia_no_laborable"] += 1
        elif existente.motivo != motivo:
            resumen.advertir(
                f"dia_no_laborable {fecha}: ya existe con motivo '{existente.motivo}' "
                f"(la librería dice '{motivo}'). No se modifica."
            )
    db.commit()
    return resumen
