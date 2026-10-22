"""Catálogos de solo lectura y asignaciones (precarga del frontend, CU003, CU016)."""
from datetime import timedelta

import pytest

from app.core.tiempo import ahora_utc
from app.models.organizacion import AnioEscolar, PeriodoAcademico
from tests.conftest import crear_particular, rangos_en_receso


@pytest.mark.parametrize("rol", ["Docente", "Supervisor", "Directivo"])
def test_catalogos_los_ve_cualquier_rol(client, entorno, cabeceras, rol):
    cabecera = cabeceras[rol]

    grados = client.get("/grados", headers=cabecera).json()
    programas = client.get("/programas", headers=cabecera).json()

    assert [g["nombre"] for g in grados] == ["1.º", "2.º", "3.º", "4.º", "5.º", "6.º"]
    assert grados[0]["id"] == grados[0]["id_grado"]
    assert [p["nombre"] for p in programas] == ["Alfabetización", "Comprensión Lectora"]


def test_catalogos_sin_sesion(client, entorno):
    assert client.get("/grados").status_code == 401


def test_anios_y_periodos_marcan_el_vigente(client, entorno, cabeceras):
    cabecera = cabeceras["Docente"]

    anios = client.get("/anios-escolares", headers=cabecera).json()
    periodos = client.get("/periodos-academicos", headers=cabecera).json()
    del_anio = client.get("/periodos-academicos", params={"id_anio_escolar": 999}, headers=cabecera).json()

    assert [a["vigente"] for a in anios] == [True]
    assert [(p["numero"], p["tipo"], p["vigente"]) for p in periodos] == [
        (1, "PA", False), (2, "PA", True), (3, "PA", False), (4, "PA", False)
    ]
    rango = (periodos[1]["fecha_inicio"], periodos[1]["fecha_fin"])
    assert rango == tuple(d.isoformat() for d in entorno.rangos["PA2"])
    assert del_anio == []


def test_periodos_academicos_con_las_fechas_del_colegio(client, db, entorno, cabeceras):
    """Sin `id_colegio`, las fechas del general; con él, las del calendario que le aplica."""
    lejano = entorno.colegios["Lejano"].id_colegio
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))
    cabecera = cabeceras["Docente"]

    general = client.get("/periodos-academicos", headers=cabecera).json()
    de_lejano = client.get("/periodos-academicos", params={"id_colegio": lejano}, headers=cabecera).json()

    assert [p["vigente"] for p in general] == [False, True, False, False]
    assert [p["vigente"] for p in de_lejano] == [False, False, False, False]
    assert de_lejano[1]["fecha_fin"] == (entorno.hoy - timedelta(days=1)).isoformat()


def test_periodos_academicos_sin_calendario_traen_fechas_null(client, db, entorno, cabeceras):
    otro = AnioEscolar(nombre="Siguiente", fecha_inicio=entorno.hoy + timedelta(days=400),
                       fecha_fin=entorno.hoy + timedelta(days=700), creado_por=1, creado_en=ahora_utc())
    db.add(otro)
    db.flush()
    db.add(PeriodoAcademico(id_anio_escolar=otro.id_anio_escolar, numero=1, creado_por=1, creado_en=ahora_utc()))
    db.commit()

    periodos = client.get(
        "/periodos-academicos", params={"id_anio_escolar": otro.id_anio_escolar}, headers=cabeceras["Docente"]
    ).json()

    assert periodos == [{"id": periodos[0]["id"], "id_anio_escolar": otro.id_anio_escolar, "numero": 1, "tipo": "PA",
                         "fecha_inicio": None, "fecha_fin": None, "vigente": False}]


def test_niveles(client, entorno, cabeceras):
    cabecera = cabeceras["Supervisor"]

    razkids = client.get("/niveles-razkids", headers=cabecera).json()
    rubrica = client.get("/niveles-rubrica", headers=cabecera).json()
    filtrada = client.get(
        "/niveles-rubrica", params={"id_programa": entorno.programas["Alfabetización"]}, headers=cabecera
    ).json()
    generales = client.get("/niveles-generales", headers=cabecera)

    # La escala Raz-Kids es aa..Z (sin Z1 ni Z2) y los niveles generales ya no existen.
    assert (razkids[0], razkids[-1], len(razkids)) == ({"id": 1, "letra": "aa"}, {"id": 27, "letra": "Z"}, 27)
    assert generales.status_code == 404
    assert {n["id_programa"] for n in filtrada} == {entorno.programas["Alfabetización"]}
    assert len(filtrada) < len(rubrica)


# ── Asignaciones ────────────────────────────────────────────────────────────────────

def test_asignaciones_del_supervisor_con_filtros(client, entorno, cabeceras):
    cabecera = cabeceras["Supervisor"]
    vigente = entorno.periodos["vigente"].id_periodo_academico

    todas = client.get("/asignaciones", headers=cabecera).json()
    del_periodo = client.get("/asignaciones", params={"id_periodo_academico": vigente}, headers=cabecera).json()
    filtradas = client.get(
        "/asignaciones",
        params={"id_docente": entorno.id_docente, "id_colegio": entorno.colegios["Lejano"].id_colegio},
        headers=cabecera,
    ).json()

    assert len(todas) == 4
    assert [a["vigente"] for a in del_periodo] == [True, True]
    assert del_periodo[0]["docente"] == "Rosa Quispe"
    assert filtradas == []


@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_asignaciones_solo_para_el_supervisor(client, entorno, cabeceras, rol):
    assert client.get("/asignaciones", headers=cabeceras[rol]).status_code == 403


def test_me_asignaciones_del_docente(client, entorno, cabeceras):
    cuerpo = client.get("/me/asignaciones", headers=cabeceras["Docente"]).json()

    assert len(cuerpo) == 1
    colegio = cuerpo[0]
    assert colegio["colegio"] == "Colegio Andino"
    assert colegio["cantidad_alumnos"] == 2
    assert colegio["ciclos"] == ["III"]
    assert colegio["subprogramas"] == ["Alfabetización", "Comprensión Lectora"]
    assert [(g["nombre"], g["cantidad_alumnos"]) for g in colegio["grados"]] == [("1.º", 1), ("2.º", 1)]


def test_me_asignaciones_es_solo_del_docente(client, entorno, cabeceras):
    assert client.get("/me/asignaciones", headers=cabeceras["Supervisor"]).status_code == 403
