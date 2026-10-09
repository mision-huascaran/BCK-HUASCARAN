"""cargar-colegios-iniciales: primera carga de los colegios reales del programa.

Solo actúa con CARGAR_COLEGIOS_INICIALES=true. Se activa para la primera carga y luego
se apaga: los colegios los administra la Supervisora desde la app (CU013), y una
recarga podría duplicar un colegio que ya se renombró.

Un colegio que ya existe con el mismo nombre (sin distinguir mayúsculas, tildes ni
espacios) no se vuelve a crear: se le completan los grados y programas que le falten,
sin quitar ninguno.
"""
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.cli.comun import DIR_DATOS, Resumen, id_supervisor_original, leer_json, log
from app.core.config import settings
from app.core.tiempo import ahora_utc
from app.models.organizacion import Colegio, ColegioGrado, ColegioPrograma, Grado, Programa
from app.services.colegio_service import completar_oferta, nombre_comparable

ARCHIVO_COLEGIOS = DIR_DATOS / "colegios_iniciales.json"


def _crear_colegio(
    db: Session, datos: dict, comunes: dict, grados: list[Grado], programas: list[Programa], auditoria: dict
) -> Colegio:
    colegio = Colegio(nombre=datos["nombre"], provincia=datos["provincia"], **comunes, **auditoria)
    db.add(colegio)
    db.flush()
    for grado in grados:
        db.add(ColegioGrado(id_colegio=colegio.id_colegio, id_grado=grado.id_grado, **auditoria))
    for programa in programas:
        db.add(ColegioPrograma(id_colegio=colegio.id_colegio, id_programa=programa.id_programa, **auditoria))
    return colegio


def _completar_existente(
    db: Session, colegio: Colegio, grados: list[Grado], programas: list[Programa], auditoria: dict, resumen: Resumen
) -> None:
    """Le agrega al colegio existente los grados y programas que le falten."""
    nuevos_grados, nuevos_programas = completar_oferta(db, colegio, grados, programas, auditoria)
    if not nuevos_grados and not nuevos_programas:
        resumen.omitir(f"colegio {colegio.nombre}: ya existe y tiene todos sus grados y programas")
        return
    db.commit()
    log.info(
        "Colegio %s ya existía: se le agregaron grados [%s] y programas [%s]",
        colegio.nombre,
        ", ".join(g.nombre for g in nuevos_grados),
        ", ".join(p.nombre for p in nuevos_programas),
    )
    resumen.actualizados["colegio"] += 1
    resumen.creados["colegio_grado"] += len(nuevos_grados)
    resumen.creados["colegio_programa"] += len(nuevos_programas)


def cargar_colegios_iniciales(
    db: Session, archivo: Path = ARCHIVO_COLEGIOS, habilitado: Optional[bool] = None
) -> Resumen:
    resumen = Resumen()
    habilitado = settings.CARGAR_COLEGIOS_INICIALES if habilitado is None else habilitado
    if not habilitado:
        resumen.omitir("colegios iniciales: CARGAR_COLEGIOS_INICIALES no es true")
        return resumen

    datos = leer_json(archivo)
    if datos is None:
        resumen.error(f"No se pudieron leer los colegios iniciales de {archivo}")
        return resumen
    id_supervisor = id_supervisor_original(db)
    if id_supervisor is None:
        resumen.advertir("No se cargan colegios iniciales: todavía no existe el Supervisor original")
        return resumen

    grados = db.exec(select(Grado).order_by(Grado.id_grado)).all()
    programas = db.exec(select(Programa).order_by(Programa.id_programa)).all()
    existentes = {nombre_comparable(c.nombre): c for c in db.exec(select(Colegio)).all()}
    auditoria = {"creado_por": id_supervisor, "creado_en": ahora_utc()}

    for item in datos["colegios"]:
        existente = existentes.get(nombre_comparable(item["nombre"]))
        if existente is not None:
            _completar_existente(db, existente, grados, programas, auditoria, resumen)
            continue
        colegio = _crear_colegio(db, item, datos["comunes"], grados, programas, auditoria)
        db.commit()
        existentes[nombre_comparable(item["nombre"])] = colegio
        resumen.creados["colegio"] += 1
        resumen.creados["colegio_grado"] += len(grados)
        resumen.creados["colegio_programa"] += len(programas)
    return resumen
