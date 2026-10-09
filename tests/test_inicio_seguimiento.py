"""Inicio por rol (CU010, CU011, CU012) y seguimiento de docentes (CU020, CU021)."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.organizacion import DiaNoLaborable, DocenteColegioGrado, PeriodoAcademico
from app.models.trazabilidad import Actividad
from tests.conftest import a_las, cabecera_de, crear_docente, iniciar_actividad, motivo

# Miércoles. Las alertas cuentan días hábiles hasta ayer (martes 16).
MIERCOLES = date(2026, 6, 17)


@pytest.fixture
def miercoles(reloj):
    """Fija el reloj antes de armar el entorno: el calendario se arma relativo a este día."""
    reloj.fijar(a_las(MIERCOLES, 9))
    return reloj


def asignado_desde(db, entorno, dia: date) -> None:
    vigente = entorno.periodos["vigente"].id_periodo_academico
    for fila in db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_periodo_academico == vigente)).all():
        fila.creado_en = a_las(dia, 8)
        db.add(fila)
    db.commit()


def alertas(client, cabeceras) -> list[dict]:
    return client.get("/inicio/supervisor", headers=cabeceras["Supervisor"]).json()["alertas"]


def terminar_periodo_vigente(db, entorno) -> None:
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = entorno.hoy - timedelta(days=1)
    db.add(vigente)
    db.commit()


# ── CU010: Inicio del Docente ───────────────────────────────────────────────────────

def test_cu010_inicio_del_docente(client, entorno, cabeceras):
    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert cuerpo["puede_iniciar_actividad"] is True
    assert cuerpo["actividad_activa"] is None
    assert cuerpo["totales"] == {"ciclos": ["III"], "subprogramas": ["Alfabetización", "Comprensión Lectora"], "cantidad_alumnos": 2}
    assert [a["colegio"] for a in cuerpo["asignaciones"]] == ["Colegio Andino"]
    assert cuerpo["registros_offline_pendientes"] is None


def test_cu010_con_actividad_en_curso(client, entorno, cabeceras, actividad):
    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert cuerpo["actividad_activa"]["id"] == actividad["id"]
    assert cuerpo["puede_iniciar_actividad"] is False


def test_cu010_entre_periodos_no_puede_iniciar(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert (cuerpo["asignaciones"], cuerpo["puede_iniciar_actividad"]) == ([], False)
    assert client.get("/me/asignaciones", headers=cabeceras["Docente"]).json() == []


@pytest.mark.parametrize("ruta, rol", [("/inicio/docente", "Supervisor"), ("/inicio/supervisor", "Directivo"), ("/inicio/directivo", "Docente")])
def test_cu010_cada_inicio_es_solo_de_su_rol(client, entorno, cabeceras, ruta, rol):
    assert client.get(ruta, headers=cabeceras[rol]).status_code == 403


# ── CU011: Inicio del Supervisor ────────────────────────────────────────────────────

def test_cu011_indicadores_del_supervisor(client, entorno, cabeceras, actividad):
    cuerpo = client.get("/inicio/supervisor", headers=cabeceras["Supervisor"]).json()

    assert (cuerpo["colegios_registrados"], cuerpo["docentes_activos"], cuerpo["docentes_con_actividad_activa"]) == (2, 1, 1)
    assert (cuerpo["registros_pendientes"], cuerpo["registros_incompletos"]) == (None, None)


def test_cu011_docente_recien_asignado_no_alerta(miercoles, client, entorno, cabeceras):
    assert alertas(client, cabeceras) == []


@pytest.mark.parametrize(
    "asignado, dias",
    [(date(2026, 6, 10), 4), (date(2026, 6, 11), 3), (date(2026, 5, 1), 22)],
    ids=["cuatro_dias_alerta", "tres_dias_no_alerta", "desde_el_inicio_del_periodo"],
)
def test_cu011_alerta_con_4_dias_habiles_sin_actividad(miercoles, client, db, entorno, cabeceras, asignado, dias):
    asignado_desde(db, entorno, asignado)

    resultado = alertas(client, cabeceras)

    if dias < 4:
        assert resultado == []
    else:
        assert len(resultado) == 1
        assert resultado[0]["tipo"] == "docente_inactivo"
        assert resultado[0]["mensaje"] == f"Atención: El docente Rosa Quispe lleva {resultado[0]['dias_habiles']} días hábiles sin iniciar actividad."
        assert resultado[0]["dias_habiles"] == dias


def test_cu011_los_feriados_no_cuentan(miercoles, client, db, entorno, cabeceras):
    asignado_desde(db, entorno, date(2026, 6, 10))
    db.add(DiaNoLaborable(fecha=date(2026, 6, 15), motivo="Feriado", creado_por=1, creado_en=ahora_utc()))
    db.commit()

    assert alertas(client, cabeceras) == []


def test_cu011_el_ultimo_inicio_de_actividad_reinicia_la_cuenta(miercoles, client, db, entorno, cabeceras):
    asignado_desde(db, entorno, date(2026, 6, 1))
    miercoles.fijar(a_las(date(2026, 6, 12), 9))
    iniciar_actividad(client, cabecera_de(db, entorno.docente))
    miercoles.fijar(a_las(MIERCOLES, 9))

    assert alertas(client, cabeceras) == []
    assert db.exec(select(Actividad)).one() is not None


def test_cu011_varias_alertas_ordenadas_por_dias(miercoles, client, db, entorno, cabeceras):
    asignado_desde(db, entorno, date(2026, 6, 1))
    otro = crear_docente(db, entorno, "Beto", "beto@sicedu.test", "70000002")
    db.add(DocenteColegioGrado(
        id_docente=otro.id_docente, id_colegio=entorno.colegios["Lejano"].id_colegio, id_grado=entorno.grados["3.º"],
        id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico, creado_por=1,
        creado_en=a_las(date(2026, 6, 9), 8),
    ))
    db.commit()

    resultado = alertas(client, cabeceras)

    assert [a["docente"] for a in resultado] == ["Rosa Quispe", "Beto Prueba"]
    assert resultado[0]["dias_habiles"] > resultado[1]["dias_habiles"]


def test_cu011_entre_periodos_no_hay_alertas(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    assert alertas(client, cabeceras) == []


# ── CU012: Inicio del Directivo ─────────────────────────────────────────────────────

def test_cu012_indicadores_del_directivo(client, entorno, cabeceras):
    cuerpo = client.get("/inicio/directivo", headers=cabeceras["Directivo"]).json()

    assert cuerpo == {"beneficiarios_activos": 3, "colegios_operando": 1, "salud_sistema": None}


def test_cu012_sin_periodo_vigente_no_opera_ningun_colegio(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    assert client.get("/inicio/directivo", headers=cabeceras["Directivo"]).json()["colegios_operando"] == 0


# ── CU020 y CU021: seguimiento ──────────────────────────────────────────────────────

def test_cu020_tabla_de_seguimiento(client, entorno, cabeceras):
    cuerpo = client.get("/seguimiento/docentes", headers=cabeceras["Supervisor"]).json()

    fila = cuerpo["items"][0]
    assert (fila["id_docente"], fila["sincronizacion"], fila["registros_pendientes"]) == (entorno.id_docente, "al_dia", 0)
    assert fila["colegios"] == [{"id": entorno.colegios["Andino"].id_colegio, "nombre": "Colegio Andino"}]
    assert fila["ultima_conexion"] is not None


def test_cu020_filtros(client, db, entorno, cabeceras):
    crear_docente(db, entorno, "Nunca", "nunca@sicedu.test", "70000003")
    db.commit()
    sup = cabeceras["Supervisor"]
    hoy = entorno.hoy.isoformat()

    def nombres(**params):
        return [d["nombres"] for d in client.get("/seguimiento/docentes", params=params, headers=sup).json()["items"]]

    assert nombres() == ["Nunca", "Rosa"]
    assert nombres(desde=hoy, hasta=hoy) == ["Rosa"]
    assert nombres(id_colegio=entorno.colegios["Andino"].id_colegio) == ["Rosa"]
    assert nombres(sincronizacion="pendiente") == []
    assert nombres(activo="false") == []


def test_cu020_rango_invertido_y_entre_periodos(client, db, entorno, cabeceras):
    sup = cabeceras["Supervisor"]
    invertido = client.get("/seguimiento/docentes", params={"desde": "2026-05-02", "hasta": "2026-05-01"}, headers=sup)
    terminar_periodo_vigente(db, entorno)
    por_colegio = client.get(
        "/seguimiento/docentes", params={"id_colegio": entorno.colegios["Andino"].id_colegio}, headers=sup
    ).json()

    assert motivo(invertido) == "rango_fechas_invalido"
    assert por_colegio["total"] == 0


def test_cu021_cabecera_e_historial_del_docente(client, entorno, cabeceras, actividad):
    sup = cabeceras["Supervisor"]
    url = f"/seguimiento/docentes/{entorno.id_docente}"

    cabecera = client.get(url, headers=sup).json()
    historial = client.get(f"{url}/actividades", headers=sup).json()

    assert cabecera["nombres"] == "Rosa"
    assert [a["id"] for a in historial["items"]] == [actividad["id"]]
    assert historial["items"][0]["productividad_texto"] == "Sin registros"


def test_cu021_docente_inexistente(client, entorno, cabeceras):
    sup = cabeceras["Supervisor"]

    for url in ("/seguimiento/docentes/999", "/seguimiento/docentes/999/actividades"):
        respuesta = client.get(url, headers=sup)
        assert (respuesta.status_code, motivo(respuesta)) == (404, "docente_no_encontrado")


@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_cu020_solo_el_supervisor(client, entorno, cabeceras, rol):
    assert client.get("/seguimiento/docentes", headers=cabeceras[rol]).status_code == 403


def test_periodo_vigente_unico(db, entorno):
    vigentes = db.exec(
        select(PeriodoAcademico).where(PeriodoAcademico.fecha_inicio <= entorno.hoy, PeriodoAcademico.fecha_fin >= entorno.hoy)
    ).all()

    assert len(vigentes) == 1
