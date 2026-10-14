"""Asistencia semanal de los alumnos (CU022)."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.evaluacion import AsistenciaSemanal, SemanaReporte
from app.models.trazabilidad import Auditoria
from app.services import asistencia_service
from tests.conftest import crear_alumno, crear_colegio, iso, motivo


@pytest.fixture
def semanas(db, entorno) -> dict[str, SemanaReporte]:
    """Semana en curso y la anterior (editables), una futura del periodo vigente y una del
    periodo pasado (de solo lectura)."""
    lunes = entorno.hoy - timedelta(days=entorno.hoy.weekday())
    vigente, pasado = entorno.periodos["vigente"], entorno.periodos["pasado"]
    datos = {
        "anterior": (vigente, lunes - timedelta(days=7), 1),
        "actual": (vigente, lunes, 2),
        "futura": (vigente, lunes + timedelta(days=7), 3),
        "pasada": (pasado, pasado.fecha_inicio, 1),
    }
    creadas = {}
    for clave, (periodo, inicio, numero) in datos.items():
        creadas[clave] = SemanaReporte(
            id_anio_escolar=entorno.anio.id_anio_escolar, id_periodo_academico=periodo.id_periodo_academico,
            numero_semana=numero, fecha_inicio=inicio, fecha_fin=inicio + timedelta(days=4),
            creado_por=entorno.supervisor.id_usuario, creado_en=ahora_utc(),
        )
        db.add(creadas[clave])
    db.commit()
    for semana in creadas.values():
        db.refresh(semana)
    return creadas


def aula(entorno, semana, colegio="Andino", grado="2.º", programa="Comprensión Lectora") -> dict:
    return {
        "id_colegio": entorno.colegios[colegio].id_colegio,
        "id_grado": entorno.grados[grado],
        "id_programa": entorno.programas[programa],
        "id_semana": semana.id_semana,
    }


def consultar(client, cabecera, params):
    return client.get("/asistencias", params=params, headers=cabecera)


def guardar(client, cabecera, semana, registros, **extra):
    cuerpo = {"id_semana": semana.id_semana, "registros": registros, **extra}
    return client.put("/asistencias", json=cuerpo, headers=cabecera)


def marca(entorno, alumno, asistio=True) -> dict:
    return {"id_alumno": entorno.alumnos[alumno].id_alumno, "asistio": asistio}


def filas(db) -> list[AsistenciaSemanal]:
    db.expire_all()
    return db.exec(select(AsistenciaSemanal).order_by(AsistenciaSemanal.id)).all()


# ── Consulta ────────────────────────────────────────────────────────────────────────

def test_cu022_docente_lee_su_aula_sin_actividad(client, entorno, cabeceras, semanas):
    respuesta = consultar(client, cabeceras["Docente"], aula(entorno, semanas["actual"]))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["semana"] == {
        "id_semana": semanas["actual"].id_semana, "numero": 2,
        "fecha_inicio": semanas["actual"].fecha_inicio.isoformat(),
        "fecha_fin": semanas["actual"].fecha_fin.isoformat(),
    }
    assert cuerpo["editable"] is True
    # Solo activos del colegio, grado y subprograma (Iván está inactivo).
    assert cuerpo["alumnos"] == [
        {"id_alumno": entorno.alumnos["ana"].id_alumno, "nombres": "Ana", "apellidos": "Torres", "asistio": None}
    ]


def test_cu022_ordena_por_apellidos_y_muestra_lo_registrado(client, db, entorno, cabeceras, semanas, actividad):
    crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["2.º"],
                 entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario)
    db.commit()
    guardar(client, cabeceras["Docente"], semanas["actual"], [marca(entorno, "ana", False)])

    alumnos = consultar(client, cabeceras["Docente"], aula(entorno, semanas["actual"])).json()["alumnos"]

    assert [(a["apellidos"], a["asistio"]) for a in alumnos] == [("Abad", None), ("Torres", False)]


def test_cu022_docente_fuera_de_su_alcance(client, entorno, cabeceras, semanas):
    respuesta = consultar(client, cabeceras["Docente"], aula(entorno, semanas["actual"], "Lejano", "3.º"))

    assert (respuesta.status_code, motivo(respuesta)) == (403, "aula_fuera_de_alcance")


def test_cu022_supervisor_lee_cualquier_colegio_y_directivo_no(client, entorno, cabeceras, semanas):
    params = aula(entorno, semanas["actual"], "Lejano", "3.º")

    supervisor = consultar(client, cabeceras["Supervisor"], params)

    assert supervisor.status_code == 200
    assert [a["nombres"] for a in supervisor.json()["alumnos"]] == ["Mario"]
    assert consultar(client, cabeceras["Directivo"], params).status_code == 403


@pytest.mark.parametrize("caso", ["semana_inexistente", "colegio_inexistente", "grado_no_ofrecido", "programa_no_ofrecido"])
def test_cu022_consulta_invalida(client, db, entorno, cabeceras, semanas, caso):
    params = aula(entorno, semanas["actual"])
    if caso == "semana_inexistente":
        params["id_semana"] = 999
    elif caso == "colegio_inexistente":
        params["id_colegio"] = 999
    else:
        chico = crear_colegio(db, "Colegio Chico", [entorno.grados["1.º"]], [entorno.programas["Alfabetización"]],
                              entorno.supervisor.id_usuario)
        db.commit()
        params["id_colegio"] = chico.id_colegio
        if caso == "programa_no_ofrecido":
            params["id_grado"] = entorno.grados["1.º"]

    respuesta = consultar(client, cabeceras["Supervisor"], params)

    assert (respuesta.status_code, motivo(respuesta)) == (422, caso)


def test_cu022_los_cuatro_filtros_son_obligatorios(client, entorno, cabeceras, semanas):
    params = aula(entorno, semanas["actual"])
    del params["id_semana"]

    respuesta = consultar(client, cabeceras["Supervisor"], params)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


@pytest.mark.parametrize("semana, editable", [("anterior", True), ("actual", True), ("futura", False), ("pasada", False)])
def test_cu022_semana_editable(client, entorno, cabeceras, semanas, semana, editable):
    cuerpo = consultar(client, cabeceras["Docente"], aula(entorno, semanas[semana])).json()

    assert cuerpo["editable"] is editable


def test_cu022_sin_periodo_vigente_ninguna_semana_es_editable(client, db, entorno, cabeceras, semanas):
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = entorno.hoy - timedelta(days=1)
    db.add(vigente)
    db.commit()

    assert consultar(client, cabeceras["Supervisor"], aula(entorno, semanas["actual"])).json()["editable"] is False


# ── Registro ────────────────────────────────────────────────────────────────────────

def test_cu022_registrar_exige_actividad(client, entorno, cabeceras, semanas):
    respuesta = guardar(client, cabeceras["Docente"], semanas["actual"], [marca(entorno, "ana")])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_requerida")
    assert respuesta.json()["detail"] == "Debe iniciar una actividad para realizar cambios."


@pytest.mark.parametrize("rol", ["Supervisor", "Directivo"])
def test_cu022_solo_el_docente_registra(client, entorno, cabeceras, semanas, rol):
    respuesta = guardar(client, cabeceras[rol], semanas["actual"], [marca(entorno, "ana")])

    assert (respuesta.status_code, motivo(respuesta)) == (403, "sin_permiso")


def test_cu022_marcar_todos_y_cambiar_uno_cuenta_un_cambio_por_alumno(client, db, entorno, cabeceras, semanas, actividad):
    docente = cabeceras["Docente"]
    todos = [marca(entorno, "luis"), marca(entorno, "ana")]

    respuesta = guardar(client, docente, semanas["actual"], todos)

    assert respuesta.status_code == 200
    assert respuesta.json() == {"id_semana": semanas["actual"].id_semana, "registros": todos}
    assert [(f.asistio, f.id_docente) for f in filas(db)] == [(True, entorno.id_docente)] * 2

    guardar(client, docente, semanas["actual"], [marca(entorno, "ana", False)])
    guardar(client, docente, semanas["actual"], [marca(entorno, "ana", True)])
    guardar(client, docente, semanas["actual"], [marca(entorno, "ana", True)])  # sin cambio: no audita

    auditoria = db.exec(select(Auditoria.accion, Auditoria.campo).where(Auditoria.tabla == "asistencia_semanal")).all()
    assert sorted(auditoria, key=str) == sorted(
        [("crear", None), ("crear", None), ("editar", "asistio"), ("editar", "asistio")], key=str
    )
    ana = next(f for f in filas(db) if f.id_alumno == entorno.alumnos["ana"].id_alumno)
    assert (ana.asistio, ana.modificado_por) == (True, entorno.docente.id_usuario)

    detalle = client.get(f"/actividades/{actividad['id']}", headers=docente).json()
    assert detalle["cambios"] == 2
    assert [(m["modulo"], m["total"]) for m in detalle["cambios_por_modulo"]] == [("asistencia", 2)]


@pytest.mark.parametrize("semana", ["futura", "pasada"])
def test_cu022_semana_no_editable_se_rechaza(client, db, entorno, cabeceras, semanas, actividad, semana):
    respuesta = guardar(client, cabeceras["Docente"], semanas[semana], [marca(entorno, "ana")])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "semana_no_editable")
    assert filas(db) == []


def test_cu022_semana_inexistente(client, entorno, cabeceras, actividad):
    respuesta = client.put(
        "/asistencias", json={"id_semana": 999, "registros": [marca(entorno, "ana")]}, headers=cabeceras["Docente"]
    )

    assert (respuesta.status_code, motivo(respuesta)) == (422, "semana_inexistente")


def test_cu022_todo_o_nada_con_un_error_por_alumno(client, db, entorno, cabeceras, semanas, actividad):
    registros = [
        marca(entorno, "ana"),
        marca(entorno, "mario"),  # fuera del alcance
        marca(entorno, "inactivo"),
        {"id_alumno": 999, "asistio": True},
        marca(entorno, "ana", False),  # repetido
    ]

    respuesta = guardar(client, cabeceras["Docente"], semanas["actual"], registros)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert [e["campo"] for e in respuesta.json()["errores"]] == [
        "registros.1.id_alumno", "registros.2.id_alumno", "registros.3.id_alumno", "registros.4.id_alumno",
    ]
    assert filas(db) == []


def test_cu022_registros_vacios(client, entorno, cabeceras, semanas, actividad):
    respuesta = guardar(client, cabeceras["Docente"], semanas["actual"], [])

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


def test_cu022_fecha_de_registro(client, db, entorno, cabeceras, semanas, actividad):
    docente = cabeceras["Docente"]
    futura = guardar(client, docente, semanas["actual"], [marca(entorno, "ana")],
                     fecha_registro=iso(ahora_utc() + timedelta(minutes=3)))
    captura = ahora_utc().replace(microsecond=0) - timedelta(hours=2)

    sin_conexion = guardar(client, docente, semanas["actual"], [marca(entorno, "ana")], fecha_registro=iso(captura))

    assert (futura.status_code, futura.json()["errores"][0]["campo"]) == (422, "fecha_registro")
    assert sin_conexion.status_code == 200
    assert filas(db)[0].fecha_registro == captura


def test_cu022_creada_a_la_vez_por_otra_peticion_se_trata_como_edicion(client, db, entorno, cabeceras, semanas,
                                                                       actividad, monkeypatch):
    """Si la fila aparece entre la lectura y el INSERT, el INSERT no choca: se edita."""
    guardar(client, cabeceras["Docente"], semanas["actual"], [marca(entorno, "ana", True)])
    reales = asistencia_service._existentes
    llamadas = []

    def sin_ver_la_primera_vez(*args):
        llamadas.append(args)
        return {} if len(llamadas) == 1 else reales(*args)

    monkeypatch.setattr(asistencia_service, "_existentes", sin_ver_la_primera_vez)
    respuesta = guardar(client, cabeceras["Docente"], semanas["actual"], [marca(entorno, "ana", False)])

    assert respuesta.status_code == 200
    assert [f.asistio for f in filas(db)] == [False]


def test_cu015_porcentaje_de_asistencia_con_lo_registrado(client, entorno, cabeceras, semanas, actividad):
    """Asistidas sobre semanas con asistencia tomada para el alumno en el año."""
    docente = cabeceras["Docente"]
    guardar(client, docente, semanas["anterior"], [marca(entorno, "ana", True)])
    guardar(client, docente, semanas["actual"], [marca(entorno, "ana", False)])

    resumen = client.get(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/resumen", headers=docente).json()

    assert resumen["asistencia_porcentaje"] == 50.0
