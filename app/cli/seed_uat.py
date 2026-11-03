"""Carga de datos UAT"""
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from sqlmodel import Session, col, select

from app.cli.catalogos import roles_por_nombre
from app.cli.comun import Resumen, id_supervisor_original
from app.cli.seed_demo import APELLIDOS, NOMBRES, _crear_alumno, _seccion_unica
from app.cli.seed_e2e import (
    CuentaE2E, _anio_escolar, _asegurar_asignaciones,
    _asegurar_colegio, _asegurar_cuenta,
)
from app.core.correo import normalizar_correo
from app.core.politica_contrasena import errores_politica_contrasena
from app.models.organizacion import Alumno, Grado, Programa, Usuario
from app.services.calendario import (
    cargar_calendarios, periodos_academicos_del_anio,
)

ARCHIVO_UAT = Path(__file__).resolve().parents[1] / "datos" / "uat_demo.json"
PROGRAMAS = ("Alfabetización", "Comprensión Lectora")
# Los correos y las contraseñas se suministran como secretos del entorno UAT.
CUENTAS = (
    ("UAT_DOCENTE_1", "Docente", "Docente UAT 1", "88001001"),
    ("UAT_DOCENTE_2", "Docente", "Docente UAT 2", "88001002"),
    ("UAT_DOCENTE_3", "Docente", "Docente UAT 3", "88001003"),
    ("UAT_SUPERVISOR", "Supervisor", "Supervisor UAT", "88001004"),
    ("UAT_DIRECTIVO", "Directivo", "Directivo UAT", "88001005"),
)


def cargar_datos_uat(archivo: Path = ARCHIVO_UAT) -> dict:
    with archivo.open(encoding="utf-8") as manejador:
        return json.load(manejador)


def entorno_uat_habilitado(entorno: Optional[Mapping[str, str]] = None) -> bool:
    entorno = os.environ if entorno is None else entorno
    return (
        entorno.get("ENTORNO", "").strip().lower() == "uat"
        and entorno.get("SEED_UAT", "").strip().lower() == "true"
    )


def validar_datos(datos: dict) -> None:
    """Comprueba el formato antes de escribir en BD."""
    if datos.get("anio") != 2026:
        raise ValueError("uat_demo.json debe indicar el año 2026")
    colegios = datos.get("colegios")
    if not isinstance(colegios, list) or not colegios:
        raise ValueError("Se requiere al menos un colegio")
    nombres_colegio = set()
    for colegio in colegios:
        nombre = colegio["nombre"].strip()
        if not nombre or nombre.casefold() in nombres_colegio or "Demo UAT" not in nombre:
            raise ValueError(f"Colegio duplicado o sin identificador Demo UAT: {nombre}")
        nombres_colegio.add(nombre.casefold())
        grupos = colegio.get("grados", [])
        if not grupos:
            raise ValueError(f"{nombre}: no hay grados")
        grados_vistos = set()
        for grupo in grupos:
            if grupo["nombre"] in grados_vistos:
                raise ValueError(f"{nombre}: grado duplicado")
            grados_vistos.add(grupo["nombre"])
            if grupo["seccion"] != "Única" or not isinstance(grupo["alumnos"], int) or not 2 <= grupo["alumnos"] <= 200:
                raise ValueError(f"{nombre}: sección o cantidad de alumnos inválida")
            subprogramas = grupo.get("subprogramas")
            if not isinstance(subprogramas, dict) or set(subprogramas) != set(PROGRAMAS):
                raise ValueError(f"{nombre} - {grupo['nombre']}: subprogramas inválidos")
            if any(type(cantidad) is not int or cantidad < 0 for cantidad in subprogramas.values()):
                raise ValueError(f"{nombre} - {grupo['nombre']}: cantidades de subprogramas inválidas")
            if sum(subprogramas.values()) != grupo["alumnos"]:
                raise ValueError(f"{nombre} - {grupo['nombre']}: cantidades no coinciden con el total de alumnos")


def _configurar_cuentas(entorno: Mapping[str, str]) -> list[CuentaE2E]:
    cuentas = []
    correos = set()
    for prefijo, rol, nombres, dni in CUENTAS:
        correo = entorno.get(f"{prefijo}_CORREO", "").strip()
        password = entorno.get(f"{prefijo}_PASS", "")
        if not correo or not password:
            raise ValueError(f"Faltan {prefijo}_CORREO o {prefijo}_PASS")
        if errores := errores_politica_contrasena(password):
            raise ValueError(f"{prefijo}_PASS incumple CU006: {', '.join(errores)}")
        correo = normalizar_correo(correo)
        if correo in correos:
            raise ValueError("Se repite un correo UAT")
        correos.add(correo)
        cuentas.append(CuentaE2E(f"{prefijo}_PASS", rol, nombres, "Demostración", dni, correo, True))
    return cuentas


def _identidad_alumno(colegio_idx: int, grado_idx: int, numero: int) -> tuple[str, str]:
    """Identidades ficticias estables, sin duplicarse dentro de cada grupo."""
    desplazamiento = colegio_idx * 13 + grado_idx * 7
    nombres = NOMBRES[(numero + desplazamiento) % len(NOMBRES)]
    paterno = APELLIDOS[((numero // len(NOMBRES)) + desplazamiento) % len(APELLIDOS)]
    materno = APELLIDOS[(numero + desplazamiento * 3) % len(APELLIDOS)]
    return nombres, f"{paterno} {materno}"


def sembrar_uat(db: Session, entorno: Optional[Mapping[str, str]] = None) -> Resumen:
    """Carga idempotente; debe invocarse manualmente, solo con ENTORNO=uat y SEED_UAT=true."""
    resumen = Resumen()
    entorno = os.environ if entorno is None else entorno
    if not entorno_uat_habilitado(entorno):
        resumen.omitir("UAT: requiere ENTORNO=uat y SEED_UAT=true")
        return resumen
    try:
        datos = cargar_datos_uat()
        validar_datos(datos)
        cuentas = _configurar_cuentas(entorno)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        resumen.error(f"Prevalidación UAT: {exc}")
        return resumen

    # Prevalidación de identidades: nunca reutilizar/modificar usuarios ajenos al seed.
    for cuenta in cuentas:
        usuario = db.exec(select(Usuario).where(Usuario.correo == cuenta.correo)).first()
        if usuario is not None and (usuario.dni != cuenta.dni or usuario.apellidos != "Demostración"):
            resumen.error(f"Correo ya pertenece a una cuenta ajena al seed: {cuenta.correo}")
            return resumen

    id_autor = id_supervisor_original(db)
    anio = _anio_escolar(db)
    if id_autor is None or anio is None or anio.nombre != str(datos["anio"]):
        resumen.error("Se requiere Supervisor original y año escolar 2026 vigente/de referencia")
        return resumen
    periodos = periodos_academicos_del_anio(db, anio.id_anio_escolar)
    if {p.numero for p in periodos} != {1, 2, 3, 4}:
        resumen.error("Faltan PA1, PA2, PA3 o PA4 del calendario 2026")
        return resumen
    calendarios = cargar_calendarios(db, anio.id_anio_escolar)
    calendario = calendarios.aplicable(None, anio.id_anio_escolar)
    if calendario is None or calendario.pa(1) is None:
        resumen.error("No hay calendario general 2026 con PA1")
        return resumen
    desde = calendario.pa(1).fecha_inicio

    nombres_grado = {g["nombre"] for c in datos["colegios"] for g in c["grados"]}
    grados = {g.nombre: g for g in db.exec(select(Grado).where(col(Grado.nombre).in_(nombres_grado))).all()}
    programas = {p.nombre: p for p in db.exec(select(Programa).where(col(Programa.nombre).in_(PROGRAMAS))).all()}
    if set(grados) != nombres_grado or set(programas) != set(PROGRAMAS):
        resumen.error("Faltan grados o subprogramas: ejecutar cargar-catalogos")
        return resumen

    roles = roles_por_nombre(db)
    if any(c.rol not in roles for c in cuentas):
        resumen.error("Faltan roles Docente/Supervisor/Directivo en catálogos")
        return resumen

    # Evita cambiar roles de cuentas preexistentes ajenas al seed.
    for cuenta in cuentas:
        existente = db.exec(select(Usuario).where(Usuario.correo == cuenta.correo)).first()
        if existente is not None and existente.id_rol != roles[cuenta.rol].id_rol:
            resumen.error(f"Rol de cuenta existente no coincide: {cuenta.correo}")
            return resumen

    # Crear cuentas antes de colegios y alumnos; una contraseña no válida ya fue rechazada.
    usuarios = []
    for cuenta in cuentas:
        usuario = _asegurar_cuenta(db, cuenta, entorno, roles, id_autor, resumen, etiqueta="UAT")
        if usuario is None:
            resumen.error(f"No se pudo preparar cuenta UAT {cuenta.correo}")
            return resumen
        usuarios.append(usuario)

    for indice, item in enumerate(datos["colegios"]):
        nombres = tuple(g["nombre"] for g in item["grados"])
        # Mismo helper que usa el seed E2E/demo; genera sección Única.
        ficha = {campo: item[campo] for campo in ("nombre", "departamento", "provincia", "distrito", "nivel_educativo")}
        dias = {nombre: (2 if indice % 2 == 0 else 4) for nombre in nombres}
        colegio = _asegurar_colegio(db, [grados[n] for n in nombres], list(programas.values()),
                                    id_autor, resumen, datos=ficha, dias=dias, etiqueta="UAT")
        docente = usuarios[indice]
        _asegurar_asignaciones(db, docente.id_docente, colegio, grados, periodos,
                               id_autor, resumen, nombres_grados=nombres, etiqueta="UAT")
        for indice_grado, grupo in enumerate(item["grados"]):
            grado = grados[grupo["nombre"]]
            seccion = _seccion_unica(db, colegio.id_colegio, grado.id_grado)
            existentes = {(a.nombres, a.apellidos): a for a in db.exec(
                select(Alumno).where(
                    Alumno.id_colegio == colegio.id_colegio,
                    Alumno.id_grado == grado.id_grado,
                )).all()}
            for numero in range(grupo["alumnos"]):
                identidad = _identidad_alumno(indice, indice_grado, numero)
                if identidad in existentes:
                    continue
                cantidad_alfabetizacion = grupo["subprogramas"]["Alfabetización"]
                programa = PROGRAMAS[0] if numero < cantidad_alfabetizacion else PROGRAMAS[1]
                _crear_alumno(db, colegio, grado, seccion, programas[programa].id_programa,
                              identidad, desde, id_autor)
                resumen.creados["alumno UAT"] += 1
            db.commit()
    return resumen