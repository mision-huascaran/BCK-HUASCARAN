"""Carga UAT: restricciones de entorno, catálogo, seguridad, atomicidad e idempotencia."""
import copy

import pytest
from sqlmodel import func, select

from app.cli.seed_uat import (
    CUENTAS, _identidad_alumno, cargar_datos_uat, entorno_uat_habilitado,
    sembrar_uat, validar_datos,
)
from app.models.organizacion import (
    Alumno, AlumnoPertenencia, Colegio, DocenteColegioGrado, Usuario,
)
from tests.test_seed_e2e import base


VARIABLES = {"ENTORNO": "uat", "SEED_UAT": "true"}
for i, (prefijo, _, _, _) in enumerate(CUENTAS, 1):
    VARIABLES[f"{prefijo}_CORREO"] = f"uat.rol{i}@sicedu.test"
    VARIABLES[f"{prefijo}_PASS"] = f"Rol-Uat-{i}-Seguro"


def total(db, modelo):
    return db.exec(select(func.count()).select_from(modelo)).one()


def test_json_y_distribucion():
    datos = cargar_datos_uat()
    validar_datos(datos)
    grupos = [g for c in datos["colegios"] for g in c["grados"]]
    assert len(datos["colegios"]) == 3
    assert len(grupos) == 6
    assert sum(g["alumnos"] for g in grupos) == 140
    assert sum(g["subprogramas"]["Alfabetización"] for g in grupos) == 53
    assert sum(g["subprogramas"]["Comprensión Lectora"] for g in grupos) == 87
    for i, colegio in enumerate(datos["colegios"]):
        for j, grupo in enumerate(colegio["grados"]):
            assert len({_identidad_alumno(i, j, n) for n in range(grupo["alumnos"])}) == grupo["alumnos"]


def test_json_rechaza_distribucion_invalida():
    datos = copy.deepcopy(cargar_datos_uat())
    datos["colegios"][0]["grados"][0]["subprogramas"]["Alfabetización"] += 1
    with pytest.raises(ValueError, match="no suman"):
        validar_datos(datos)


@pytest.mark.parametrize("entorno", [
    {}, {"ENTORNO": "dev", "SEED_UAT": "true"},
    {"ENTORNO": "qa", "SEED_UAT": "true"},
    {"ENTORNO": "produccion", "SEED_UAT": "true"},
    {"ENTORNO": "uat", "SEED_UAT": "false"},
])
def test_candado_de_entorno(db, entorno):
    assert not entorno_uat_habilitado(entorno)
    resumen = sembrar_uat(db, entorno)
    assert resumen.omitidos
    assert total(db, Colegio) == 0


def test_carga_integral_y_segunda_ejecucion(db, base):
    primera = sembrar_uat(db, VARIABLES)
    assert not primera.errores, primera.errores
    assert primera.creados["colegio UAT"] == 3
    assert primera.creados["usuario UAT"] == 5
    assert primera.creados["asignación UAT"] == 24
    assert primera.creados["alumno UAT"] == 140
    assert total(db, Colegio) == 3
    assert total(db, Alumno) == 140
    assert total(db, AlumnoPertenencia) == 140
    assert total(db, DocenteColegioGrado) == 24
    assert total(db, Usuario) == 6  # 5 docentes + supervisor original

    antes = tuple(total(db, m) for m in (Colegio, Alumno, AlumnoPertenencia, Usuario, DocenteColegioGrado))
    segunda = sembrar_uat(db, VARIABLES)
    despues = tuple(total(db, m) for m in (Colegio, Alumno, AlumnoPertenencia, Usuario, DocenteColegioGrado))
    assert not segunda.errores, segunda.errores
    assert not segunda.creados
    assert antes == despues


def test_colegio_existente_incompatible_revierte_todo(db, base):
    from app.core.tiempo import ahora_utc
    from app.cli.comun import id_supervisor_original
    colegio = Colegio(nombre="I.E. Nueva Esperanza - Demo UAT", departamento="Áncash",
                      provincia="Huaraz", distrito="Distrito diferente", nivel_educativo="Primaria",
                      creado_por=id_supervisor_original(db), creado_en=ahora_utc())
    db.add(colegio)
    db.commit()
    respuesta = sembrar_uat(db, VARIABLES)
    assert respuesta.errores
    assert not respuesta.creados
    assert total(db, Usuario) == 1  # No persistieron las cinco cuentas del seed
    assert total(db, Colegio) == 1
    assert total(db, Alumno) == 0


def test_no_reasigna_docente_existente(db, base):
    respuesta = sembrar_uat(db, VARIABLES)
    assert not respuesta.errores
    asignacion = db.exec(select(DocenteColegioGrado)).first()
    docente_anterior = asignacion.id_docente
    # Simula una reasignación posterior, que el seed nunca debe deshacer.
    from app.models.organizacion import Docente
    from app.core.tiempo import ahora_utc
    from app.cli.comun import id_supervisor_original
    nuevo = Docente(nombres="Otra", apellidos="Persona", activo=True,
                    creado_por=id_supervisor_original(db), creado_en=ahora_utc())
    db.add(nuevo)
    db.flush()
    asignacion.id_docente = nuevo.id_docente
    db.commit()
    respuesta = sembrar_uat(db, VARIABLES)
    db.expire_all()
    assert respuesta.errores
    assert db.get(DocenteColegioGrado, asignacion.id).id_docente == nuevo.id_docente
    assert docente_anterior != nuevo.id_docente
    assert total(db, DocenteColegioGrado) == 24


def test_segunda_ejecucion_no_cambia_contrasenas(db, base):
    primera = sembrar_uat(db, VARIABLES)
    assert not primera.errores
    cuenta = db.exec(select(Usuario).where(Usuario.correo == VARIABLES["UAT_DOCENTE_1_CORREO"])).one()
    hash_anterior = cuenta.password_hash
    nuevos_secretos = {**VARIABLES, "UAT_DOCENTE_1_PASS": "Otra-Clave-Uat-2"}
    segunda = sembrar_uat(db, nuevos_secretos)
    db.refresh(cuenta)
    assert not segunda.errores
    assert cuenta.password_hash == hash_anterior