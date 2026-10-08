"""cargar-catalogos: catálogos fijos del diseño v3, desde app/datos/catalogos.json.

- rol, ciclo_ebr, grado y programa ya existen en las BD actuales con ids SERIAL creados
  por el seed: se buscan por nombre (get-or-create), sin forzar ids.
- nivel_razkids, nivel_general y nivel_rubrica usan ids explícitos (el id de
  nivel_razkids ES el orden del nivel); después se ajusta su secuencia.
- Nunca se sobrescribe una fila existente: si difiere del archivo, WARNING y se deja.
"""
from typing import Optional

from sqlmodel import Session, SQLModel, select

from app.cli.comun import DIR_DATOS, Resumen, ajustar_secuencia, leer_json
from app.models.evaluacion import NivelEsperadoPorGrado, NivelGeneral, NivelRazkids, NivelRubrica
from app.models.organizacion import CicloEbr, Grado, Programa, Rol

ARCHIVO_CATALOGOS = DIR_DATOS / "catalogos.json"


def _por_nombre(db: Session, modelo: type[SQLModel], nombre: str, resumen: Resumen, **extra):
    """Busca una fila por `nombre`; si no existe, la crea con los campos extra."""
    fila = db.exec(select(modelo).where(modelo.nombre == nombre)).first()
    if fila is None:
        fila = modelo(nombre=nombre, **extra)
        db.add(fila)
        db.flush()
        resumen.creados[modelo.__tablename__] += 1
    return fila


def _cargar_grados(db: Session, grados: list[dict], ciclos: dict[str, CicloEbr], resumen: Resumen) -> None:
    for grado in grados:
        ciclo = ciclos[grado["ciclo"]]
        fila = _por_nombre(db, Grado, grado["nombre"], resumen, id_ciclo=ciclo.id_ciclo)
        if fila.id_ciclo != ciclo.id_ciclo:
            resumen.advertir(
                f"grado {grado['nombre']}: en la BD tiene id_ciclo={fila.id_ciclo}, el archivo "
                f"dice ciclo {grado['ciclo']} (id {ciclo.id_ciclo}). No se modifica."
            )


def _cargar_por_id(
    db: Session, modelo: type[SQLModel], filas: list[dict], resumen: Resumen
) -> None:
    """Inserta las filas de id explícito que falten; avisa si una existente difiere."""
    pk = modelo.__table__.primary_key.columns.values()[0].name
    for datos in filas:
        existente = db.get(modelo, datos[pk])
        if existente is None:
            db.add(modelo(**datos))
            resumen.creados[modelo.__tablename__] += 1
            continue
        en_bd = {k: getattr(existente, k) for k, v in datos.items() if getattr(existente, k) != v}
        if en_bd:
            en_archivo = {k: datos[k] for k in en_bd}
            resumen.advertir(
                f"{modelo.__tablename__} id={datos[pk]}: la BD tiene {en_bd}, el archivo "
                f"{en_archivo}. No se modifica."
            )
    db.flush()
    ajustar_secuencia(db, getattr(modelo, pk))


def _filas_rubrica(datos: list[dict], programas: dict[str, Programa]) -> list[dict]:
    return [
        {
            "id_nivel_rubrica": n["id"],
            "id_programa": programas[n["programa"]].id_programa,
            "dimension": n["dimension"],
            "orden": n["orden"],
            "nombre_nivel": n["nombre"],
        }
        for n in datos
    ]


def _cargar_niveles_esperados(db: Session, datos: list[dict], resumen: Resumen) -> None:
    for item in datos:
        grado = db.exec(select(Grado).where(Grado.nombre == item["grado"])).first()
        nivel = db.exec(select(NivelRazkids).where(NivelRazkids.letra == item["letra"])).first()
        if grado is None or nivel is None:
            resumen.error(
                f"nivel_esperado_por_grado: no existe el grado {item['grado']} o el nivel "
                f"{item['letra']}. Se omite."
            )
            continue
        existente = db.get(NivelEsperadoPorGrado, grado.id_grado)
        if existente is None:
            db.add(NivelEsperadoPorGrado(id_grado=grado.id_grado, id_nivel_rk_esperado=nivel.id_nivel_rk))
            resumen.creados[NivelEsperadoPorGrado.__tablename__] += 1
        elif existente.id_nivel_rk_esperado != nivel.id_nivel_rk:
            resumen.advertir(
                f"nivel_esperado_por_grado {item['grado']}: la BD tiene id_nivel_rk="
                f"{existente.id_nivel_rk_esperado}, el archivo {item['letra']} "
                f"(id {nivel.id_nivel_rk}). No se modifica."
            )


def cargar_catalogos(db: Session, datos: Optional[dict] = None) -> Resumen:
    """Carga todos los catálogos fijos. Idempotente: se ejecuta en cada arranque."""
    resumen = Resumen()
    datos = datos if datos is not None else leer_json(ARCHIVO_CATALOGOS)
    if datos is None:
        resumen.error(f"No se pudieron leer los catálogos de {ARCHIVO_CATALOGOS}")
        return resumen

    for nombre in datos["roles"]:
        _por_nombre(db, Rol, nombre, resumen)
    ciclos = {nombre: _por_nombre(db, CicloEbr, nombre, resumen) for nombre in datos["ciclos"]}
    _cargar_grados(db, datos["grados"], ciclos, resumen)
    programas = {nombre: _por_nombre(db, Programa, nombre, resumen) for nombre in datos["programas"]}

    niveles_rk = [{"id_nivel_rk": n["id"], "letra": n["letra"]} for n in datos["niveles_razkids"]]
    _cargar_por_id(db, NivelRazkids, niveles_rk, resumen)
    niveles_generales = [
        {"id_nivel_general": n["id"], "nombre_nivel": n["nombre"], "orden": n["orden"]}
        for n in datos["niveles_generales"]
    ]
    _cargar_por_id(db, NivelGeneral, niveles_generales, resumen)
    _cargar_por_id(db, NivelRubrica, _filas_rubrica(datos["niveles_rubrica"], programas), resumen)
    _cargar_niveles_esperados(db, datos["niveles_esperados_por_grado"], resumen)

    db.commit()
    return resumen


def roles_por_nombre(db: Session) -> dict[str, Rol]:
    return {rol.nombre: rol for rol in db.exec(select(Rol)).all()}
