"""Carga de datos UAT"""
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from sqlmodel import Session, col, select

from app.cli.catalogos import roles_por_nombre
from app.cli.comun import Resumen, id_supervisor_original
from app.cli.seed_demo import APELLIDOS, NOMBRES, _crear_alumno, _seccion_unica
from app.cli.seed_e2e import CuentaE2E, _anio_escolar
from app.core.correo import normalizar_correo
from app.core.politica_contrasena import errores_politica_contrasena
from app.core.security import hash_password
from app.core.texto import nombre_comparable
from app.core.tiempo import ahora_utc
from app.models.organizacion import (
    Alumno, Colegio, ColegioGrado, Docente, DocenteColegioGrado,
    Grado, Programa, Usuario,
)
from app.services.calendario import cargar_calendarios, periodos_academicos_del_anio
from app.services.colegio_service import completar_oferta

ARCHIVO_UAT = Path(__file__).resolve().parents[1] / "datos" / "uat_demo.json"
PROGRAMAS = ("Alfabetización", "Comprensión Lectora")
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
    return (entorno.get("ENTORNO", "").strip().lower() == "uat"
            and entorno.get("SEED_UAT", "").strip().lower() == "true")


def validar_datos(datos: dict) -> None:
    """Valida integralmente el lote antes de tocar PostgreSQL."""
    if datos.get("anio") != 2026:
        raise ValueError("uat_demo.json debe indicar el año 2026")
    colegios = datos.get("colegios")
    if not isinstance(colegios, list) or not colegios:
        raise ValueError("Se requiere al menos un colegio")
    nombres_colegio = set()
    for colegio in colegios:
        nombre = colegio["nombre"].strip()
        clave = nombre_comparable(nombre)
        if not nombre or clave in nombres_colegio or "Demo UAT" not in nombre:
            raise ValueError(f"Colegio duplicado o sin identificador Demo UAT: {nombre}")
        nombres_colegio.add(clave)
        for campo in ("departamento", "provincia", "distrito", "nivel_educativo"):
            if not isinstance(colegio.get(campo), str) or not colegio[campo].strip():
                raise ValueError(f"{nombre}: campo {campo} inválido")
        grupos = colegio.get("grados")
        if not isinstance(grupos, list) or not grupos:
            raise ValueError(f"{nombre}: no hay grados")
        grados_vistos = set()
        for grupo in grupos:
            nombre_grado = grupo["nombre"]
            if nombre_grado in grados_vistos:
                raise ValueError(f"{nombre}: grado duplicado")
            grados_vistos.add(nombre_grado)
            total = grupo.get("alumnos")
            if grupo.get("seccion") != "Única" or type(total) is not int or not 2 <= total <= 200:
                raise ValueError(f"{nombre}: sección o cantidad de alumnos inválida")
            subprogramas = grupo.get("subprogramas")
            if not isinstance(subprogramas, dict) or set(subprogramas) != set(PROGRAMAS):
                raise ValueError(f"{nombre} - {nombre_grado}: subprogramas inválidos")
            if any(type(n) is not int or n < 0 for n in subprogramas.values()):
                raise ValueError(f"{nombre} - {nombre_grado}: cantidades inválidas")
            if sum(subprogramas.values()) != total:
                raise ValueError(f"{nombre} - {nombre_grado}: los subprogramas no suman el total")
            identidades = [_identidad_alumno(len(nombres_colegio)-1, len(grados_vistos)-1, n)
                           for n in range(total)]
            if len(set(identidades)) != total:
                raise ValueError(f"{nombre} - {nombre_grado}: identidades ficticias duplicadas")


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
    """Identidades ficticias estables en cada grado."""
    desplazamiento = colegio_idx * 13 + grado_idx * 7
    nombres = NOMBRES[(numero + desplazamiento) % len(NOMBRES)]
    paterno = APELLIDOS[((numero // len(NOMBRES)) + desplazamiento) % len(APELLIDOS)]
    materno = APELLIDOS[(numero + desplazamiento * 3) % len(APELLIDOS)]
    return nombres, f"{paterno} {materno}"


def _asegurar_cuenta_uat(db: Session, cuenta: CuentaE2E, entorno: Mapping[str, str],
                         id_rol: int, id_autor: int, resumen: Resumen) -> Usuario:
    """No cambia contraseñas, roles o fichas de usuarios preexistentes."""
    usuario = db.exec(select(Usuario).where(Usuario.correo == cuenta.correo)).first()
    if usuario is not None:
        identidad = (usuario.dni == cuenta.dni and usuario.nombres == cuenta.nombres
                     and usuario.apellidos == cuenta.apellidos and usuario.id_rol == id_rol
                     and usuario.activo and not usuario.es_supervisor_original)
        if not identidad or (cuenta.rol == "Docente" and usuario.id_docente is None):
            raise ValueError(f"Cuenta existente incompatible; no se modificó: {cuenta.correo}")
        return usuario
    if db.exec(select(Usuario).where(Usuario.dni == cuenta.dni)).first():
        raise ValueError(f"DNI de cuenta UAT ya utilizado: {cuenta.dni}")
    auditoria = {"creado_por": id_autor, "creado_en": ahora_utc()}
    id_docente = None
    if cuenta.rol == "Docente":
        docente = Docente(nombres=cuenta.nombres, apellidos=cuenta.apellidos, activo=True, **auditoria)
        db.add(docente)
        db.flush()
        id_docente = docente.id_docente
    usuario = Usuario(
        id_rol=id_rol, correo=cuenta.correo, password_hash=hash_password(entorno[cuenta.variable]),
        id_docente=id_docente, dni=cuenta.dni, nombres=cuenta.nombres,
        apellidos=cuenta.apellidos, activo=True, **auditoria,
    )
    db.add(usuario)
    db.flush()
    resumen.creados["usuario UAT"] += 1
    return usuario


def _asegurar_colegio_uat(db: Session, item: dict, grados: dict, programas: dict,
                           id_autor: int, resumen: Resumen) -> Colegio:
    ficha = {campo: item[campo] for campo in
             ("nombre", "departamento", "provincia", "distrito", "nivel_educativo")}
    clave = nombre_comparable(item["nombre"])
    colegio = next((c for c in db.exec(select(Colegio)).all()
                    if nombre_comparable(c.nombre) == clave), None)
    auditoria = {"creado_por": id_autor, "creado_en": ahora_utc()}
    if colegio is None:
        colegio = Colegio(**ficha, activo=True, **auditoria)
        db.add(colegio)
        db.flush()
        resumen.creados["colegio UAT"] += 1
    elif not colegio.activo or any(getattr(colegio, campo) != valor for campo, valor in ficha.items()):
        raise ValueError(f"Colegio existente incompatible; no se modificó: {item['nombre']}")
    lista_grados = [grados[g["nombre"]] for g in item["grados"]]
    completar_oferta(db, colegio, lista_grados, list(programas.values()), auditoria)
    for grado in lista_grados:
        fila = db.get(ColegioGrado, (colegio.id_colegio, grado.id_grado))
        dia = 2 if grado.nombre in ("1.º", "2.º", "5.º", "6.º") else 4
        if fila.dia_clase is None:
            fila.dia_clase = dia
            db.add(fila)
        elif fila.dia_clase != dia:
            raise ValueError(f"Día de clase incompatible: {item['nombre']} / {grado.nombre}")
    return colegio


def _asegurar_asignaciones_uat(db: Session, docente: Usuario, colegio: Colegio, nombres: tuple,
                                grados: dict, periodos: list, id_autor: int, resumen: Resumen) -> None:
    for periodo in periodos:
        for nombre in nombres:
            grado = grados[nombre]
            existente = db.exec(select(DocenteColegioGrado).where(
                DocenteColegioGrado.id_colegio == colegio.id_colegio,
                DocenteColegioGrado.id_grado == grado.id_grado,
                DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
            )).first()
            if existente is not None:
                if existente.id_docente != docente.id_docente:
                    raise ValueError(f"Conflicto docente: {colegio.nombre}, {nombre}, PA{periodo.numero}")
                continue
            db.add(DocenteColegioGrado(
                id_docente=docente.id_docente, id_colegio=colegio.id_colegio,
                id_grado=grado.id_grado, id_periodo_academico=periodo.id_periodo_academico,
                creado_por=id_autor, creado_en=ahora_utc(),
            ))
            resumen.creados["asignación UAT"] += 1
    db.flush()


def sembrar_uat(db: Session, entorno: Optional[Mapping[str, str]] = None) -> Resumen:
    """Una ejecución explícita; todos los cambios se confirman juntos o se revierten."""
    resumen = Resumen()
    entorno = os.environ if entorno is None else entorno
    if not entorno_uat_habilitado(entorno):
        resumen.omitir("UAT: requiere ENTORNO=uat y SEED_UAT=true")
        return resumen
    try:
        datos = cargar_datos_uat()
        validar_datos(datos)
        cuentas = _configurar_cuentas(entorno)
        id_autor = id_supervisor_original(db)
        anio = _anio_escolar(db)
        if id_autor is None or anio is None or anio.nombre != str(datos["anio"]):
            raise ValueError("Se requiere Supervisor original y año escolar 2026")
        periodos = periodos_academicos_del_anio(db, anio.id_anio_escolar)
        if {p.numero for p in periodos} != {1, 2, 3, 4}:
            raise ValueError("Faltan PA1, PA2, PA3 o PA4 del calendario 2026")
        calendario = cargar_calendarios(db, anio.id_anio_escolar).aplicable(None, anio.id_anio_escolar)
        if calendario is None or any(calendario.pa(i) is None for i in range(1, 5)):
            raise ValueError("Falta calendario general 2026 completo (PA1–PA4)")
        desde = calendario.pa(1).fecha_inicio
        nombres_grado = {g["nombre"] for c in datos["colegios"] for g in c["grados"]}
        grados = {g.nombre: g for g in db.exec(select(Grado).where(col(Grado.nombre).in_(nombres_grado))).all()}
        programas = {p.nombre: p for p in db.exec(select(Programa).where(col(Programa.nombre).in_(PROGRAMAS))).all()}
        if set(grados) != nombres_grado or set(programas) != set(PROGRAMAS):
            raise ValueError("Faltan grados o subprogramas; ejecutar cargar-catalogos")
        roles = roles_por_nombre(db)
        if any(c.rol not in roles for c in cuentas):
            raise ValueError("Faltan roles Docente/Supervisor/Directivo")

        # Se usan las sesiones/catálogos existentes, pero ningún helper que haga commit intermedio.
        usuarios = [_asegurar_cuenta_uat(db, c, entorno, roles[c.rol].id_rol, id_autor, resumen)
                    for c in cuentas]
        for indice, item in enumerate(datos["colegios"]):
            colegio = _asegurar_colegio_uat(db, item, grados, programas, id_autor, resumen)
            nombres = tuple(g["nombre"] for g in item["grados"])
            _asegurar_asignaciones_uat(db, usuarios[indice], colegio, nombres, grados, periodos,
                                      id_autor, resumen)
            for indice_grado, grupo in enumerate(item["grados"]):
                grado = grados[grupo["nombre"]]
                seccion = _seccion_unica(db, colegio.id_colegio, grado.id_grado)
                existentes = {(a.nombres, a.apellidos): a for a in db.exec(
                    select(Alumno).where(Alumno.id_colegio == colegio.id_colegio,
                                         Alumno.id_grado == grado.id_grado)).all()}
                for numero in range(grupo["alumnos"]):
                    identidad = _identidad_alumno(indice, indice_grado, numero)
                    programa = (PROGRAMAS[0] if numero < grupo["subprogramas"][PROGRAMAS[0]]
                                else PROGRAMAS[1])
                    existente = existentes.get(identidad)
                    if existente is not None:
                        if (existente.id_programa_actual != programas[programa].id_programa
                                or existente.id_seccion != seccion.id_seccion or not existente.activo):
                            raise ValueError(f"Alumno existente incompatible: {colegio.nombre} / {grupo['nombre']}")
                        continue
                    _crear_alumno(db, colegio, grado, seccion, programas[programa].id_programa,
                                  identidad, desde, id_autor)
                    resumen.creados["alumno UAT"] += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        resumen = Resumen()  # No reportar como creadas filas que se revirtieron.
        resumen.error(f"Carga UAT revertida sin cambios: {type(exc).__name__}: {exc}")
    return resumen