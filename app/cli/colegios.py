"""cargar-colegios-iniciales: primera carga de los colegios reales del programa.

Solo actúa con CARGAR_COLEGIOS_INICIALES=true. Se activa para la primera carga y luego
se apaga: los colegios los administra la Supervisora desde la app (CU013), y una
recarga podría duplicar un colegio que ya se renombró.
"""
import unicodedata
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.cli.comun import DIR_DATOS, Resumen, id_supervisor_original, leer_json
from app.core.config import settings
from app.core.tiempo import ahora_utc
from app.models.organizacion import Colegio, ColegioGrado, ColegioPrograma, Grado, Programa

ARCHIVO_COLEGIOS = DIR_DATOS / "colegios_iniciales.json"


def nombre_comparable(nombre: str) -> str:
    """Sin tildes, sin distinguir mayúsculas y sin espacios sobrantes: 'Amashca' = 'AMASHCÁ '."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", nombre) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.casefold().split())


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
    existentes = {nombre_comparable(c.nombre) for c in db.exec(select(Colegio)).all()}
    auditoria = {"creado_por": id_supervisor, "creado_en": ahora_utc()}

    for item in datos["colegios"]:
        if nombre_comparable(item["nombre"]) in existentes:
            resumen.omitir(f"colegio {item['nombre']}: ya existe uno con ese nombre")
            continue
        _crear_colegio(db, item, datos["comunes"], grados, programas, auditoria)
        db.commit()
        existentes.add(nombre_comparable(item["nombre"]))
        resumen.creados["colegio"] += 1
        resumen.creados["colegio_grado"] += len(grados)
        resumen.creados["colegio_programa"] += len(programas)
    return resumen
