"""Catálogos de solo lectura y asignaciones (precarga del frontend, CU003, CU016)."""
from datetime import timedelta

import pytest

from app.core.tiempo import ahora_utc
from app.models.evaluacion import SemanaReporte


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
    assert [(p["numero"], p["vigente"]) for p in periodos] == [(1, False), (2, True), (3, False)]
    assert del_anio == []


def test_semanas_por_periodo_y_cerradas(client, db, entorno, cabeceras):
    hoy = entorno.hoy
    for numero, lunes in enumerate([hoy - timedelta(days=14), hoy + timedelta(days=7)], start=1):
        db.add(SemanaReporte(
            id_anio_escolar=entorno.anio.id_anio_escolar,
            id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico,
            numero_semana=numero, fecha_inicio=lunes, fecha_fin=lunes + timedelta(days=4),
            creado_por=1, creado_en=ahora_utc(),
        ))
    db.commit()

    semanas = client.get(
        "/semanas", params={"id_periodo_academico": entorno.periodos["vigente"].id_periodo_academico},
        headers=cabeceras["Docente"],
    ).json()

    assert [(s["numero_semana"], s["cerrada"]) for s in semanas] == [(1, True), (2, False)]
    assert client.get("/semanas", headers=cabeceras["Docente"]).json() == semanas


def test_niveles(client, entorno, cabeceras):
    cabecera = cabeceras["Supervisor"]

    razkids = client.get("/niveles-razkids", headers=cabecera).json()
    rubrica = client.get("/niveles-rubrica", headers=cabecera).json()
    filtrada = client.get(
        "/niveles-rubrica", params={"id_programa": entorno.programas["Alfabetización"]}, headers=cabecera
    ).json()
    generales = client.get("/niveles-generales", headers=cabecera).json()

    assert razkids[0] == {"id": 1, "letra": "aa"}
    assert {n["id_programa"] for n in filtrada} == {entorno.programas["Alfabetización"]}
    assert len(filtrada) < len(rubrica)
    assert [n["orden"] for n in generales] == sorted(n["orden"] for n in generales)


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
