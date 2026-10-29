"""Dashboard D1: filtros comunes, alcance, contexto y selección inicial (CU033 a CU035).

Los filtros se prueban por HTTP con una app mínima que monta las dos dependencias
(`FiltrosDelPeriodo` y `FiltrosAnuales`), porque en D1 todavía ningún endpoint real las usa.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import tiempo
from app.core.database import get_db
from app.core.errores import registrar_manejadores
from app.models.organizacion import AnioEscolar, CalendarioPeriodo, PeriodoAcademico
from app.services.dashboard.filtros import (
    MENSAJE_SIN_CALENDARIO, MENSAJE_SIN_COLEGIOS, FiltrosAnuales, FiltrosDelPeriodo, nombre_visible_periodo,
)
from tests.conftest import (
    a_las, cabecera_de, crear_calendario, crear_particular, fijar_periodo, motivo, rangos_relativos,
    terminar_pa_vigente,
)

# ── Preparación ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def cliente_filtros(motor):
    """App de prueba con un endpoint por tipo de bloque, y los mismos manejadores de error."""
    prueba = FastAPI()
    registrar_manejadores(prueba)

    @prueba.get("/periodo")
    def del_periodo(filtros: FiltrosDelPeriodo):
        return filtros.contexto()

    @prueba.get("/anual")
    def anual(filtros: FiltrosAnuales):
        return filtros.contexto()

    def sesion_por_peticion():
        with Session(motor) as sesion:
            yield sesion

    prueba.dependency_overrides[get_db] = sesion_por_peticion
    return TestClient(prueba)


def _ids(entorno, *nombres):
    return [entorno.colegios[n].id_colegio for n in nombres]


def _consultar(cliente, cabeceras, entorno, ruta="/periodo", rol="Supervisor", **params):
    params.setdefault("id_anio_escolar", entorno.anio.id_anio_escolar)
    if ruta == "/periodo":
        params.setdefault("id_periodo_academico", entorno.periodos["vigente"].id_periodo_academico)
    return cliente.get(ruta, params=params, headers=cabeceras[rol])


def _error_de_campo(respuesta):
    """(status, campo, mensaje) de un 422 de validación."""
    error = respuesta.json()["errores"][0]
    return respuesta.status_code, error["campo"], error["mensaje"]


def _otro_anio(db, entorno, rangos=None, id_colegio=None) -> AnioEscolar:
    """Año escolar siguiente con un calendario (general o, con `id_colegio`, solo ese
    particular) y sus cuatro PA."""
    inicio = entorno.anio.fecha_fin + timedelta(days=30)
    anio = AnioEscolar(
        nombre=str(inicio.year + 1), fecha_inicio=inicio, fecha_fin=inicio + timedelta(days=300),
        creado_por=1, creado_en=tiempo.ahora_utc(),
    )
    db.add(anio)
    db.flush()
    crear_calendario(db, anio.id_anio_escolar, rangos or rangos_relativos(inicio + timedelta(days=160)), 1,
                     id_colegio=id_colegio)
    db.commit()
    return anio


def _pa(db, anio: AnioEscolar, numero: int) -> PeriodoAcademico:
    return db.exec(select(PeriodoAcademico).where(
        PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar, PeriodoAcademico.numero == numero,
    )).one()


# ── Nombres visibles ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("numero, nombre", [
    (1, "Primer periodo"), (2, "Segundo periodo"), (3, "Tercer periodo"), (4, "Cuarto periodo"),
])
def test_nombre_visible_periodo(numero, nombre):
    assert nombre_visible_periodo(numero) == nombre


# ── Filtros: colegios ───────────────────────────────────────────────────────────────

def test_todos_los_colegios_da_el_alcance_y_el_contexto_del_periodo(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, todos_los_colegios=True)
    assert respuesta.status_code == 200, respuesta.text
    contexto = respuesta.json()
    assert contexto["alcance"] == "todos"
    assert [c["nombre"] for c in contexto["colegios"]] == ["Colegio Andino", "Colegio Lejano"]
    assert contexto["anio_escolar"] == {"id": entorno.anio.id_anio_escolar, "nombre": entorno.anio.nombre}
    assert contexto["periodo"] == {
        "id": entorno.periodos["vigente"].id_periodo_academico, "numero": 2, "nombre": "Segundo periodo",
    }
    assert contexto["intervalo"] == {"desde": entorno.inicio("vigente").isoformat(),
                                     "hasta": entorno.fin("vigente").isoformat()}
    assert contexto["parcial"] is True
    assert datetime.fromisoformat(contexto["fecha_consulta"]).tzinfo is not None


@pytest.mark.parametrize("nombres, alcance", [(("Lejano",), "uno"), (("Lejano", "Andino"), "varios")])
def test_lista_de_colegios_en_el_orden_del_alcance(cliente_filtros, cabeceras, entorno, nombres, alcance):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_colegios=_ids(entorno, *nombres))
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["alcance"] == alcance
    assert [c["id"] for c in respuesta.json()["colegios"]] == _ids(entorno, *sorted(nombres))


def test_sin_colegios_pide_elegir_al_menos_uno(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno)
    assert _error_de_campo(respuesta) == (422, "id_colegios", MENSAJE_SIN_COLEGIOS)
    assert respuesta.json()["detail"] == MENSAJE_SIN_COLEGIOS


def test_lista_vacia_de_colegios_pide_elegir_al_menos_uno(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_colegios=[], todos_los_colegios=False)
    assert _error_de_campo(respuesta) == (422, "id_colegios", MENSAJE_SIN_COLEGIOS)


def test_todos_y_lista_a_la_vez_se_rechaza(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, todos_los_colegios=True,
                           id_colegios=_ids(entorno, "Andino"))
    assert _error_de_campo(respuesta)[:2] == (422, "id_colegios")


def test_colegios_repetidos_se_rechazan(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_colegios=_ids(entorno, "Andino", "Andino"))
    assert _error_de_campo(respuesta) == (422, "id_colegios", "No repitas colegios en la selección.")


def test_colegio_inexistente_se_rechaza(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_colegios=[999999])
    assert _error_de_campo(respuesta)[:2] == (422, "id_colegios")


def test_colegio_inactivo_esta_fuera_del_alcance(cliente_filtros, cabeceras, db, entorno):
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_colegios=_ids(entorno, "Andino", "Lejano"))
    assert respuesta.status_code == 403, respuesta.text
    assert motivo(respuesta) == "colegio_fuera_de_alcance"

    todos = _consultar(cliente_filtros, cabeceras, entorno, todos_los_colegios=True)
    assert [c["nombre"] for c in todos.json()["colegios"]] == ["Colegio Andino"]
    assert todos.json()["alcance"] == "todos"


# ── Filtros: año y periodo ──────────────────────────────────────────────────────────

def test_anio_inexistente_se_rechaza(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, id_anio_escolar=999999, todos_los_colegios=True)
    assert _error_de_campo(respuesta)[:2] == (422, "id_anio_escolar")


def test_pa_de_otro_anio_se_rechaza(cliente_filtros, cabeceras, db, entorno):
    otro = _otro_anio(db, entorno)
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, todos_los_colegios=True,
                           id_periodo_academico=_pa(db, otro, 1).id_periodo_academico)
    assert _error_de_campo(respuesta) == (
        422, "id_periodo_academico", "El periodo académico no pertenece al año escolar seleccionado.",
    )


def test_bloque_del_periodo_exige_el_pa(cliente_filtros, cabeceras, entorno):
    respuesta = cliente_filtros.get("/periodo", headers=cabeceras["Supervisor"], params={
        "id_anio_escolar": entorno.anio.id_anio_escolar, "todos_los_colegios": True,
    })
    assert _error_de_campo(respuesta) == (422, "id_periodo_academico", "Selecciona un periodo académico.")


def test_bloque_anual_no_admite_pa(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, "/anual", todos_los_colegios=True,
                           id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico)
    assert _error_de_campo(respuesta)[:2] == (422, "id_periodo_academico")


def test_bloque_anual_da_las_fechas_del_anio(cliente_filtros, cabeceras, entorno):
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, "/anual", todos_los_colegios=True)
    assert respuesta.status_code == 200, respuesta.text
    contexto = respuesta.json()
    assert contexto["periodo"] is None
    assert contexto["intervalo"] == {"desde": entorno.anio.fecha_inicio.isoformat(),
                                     "hasta": entorno.anio.fecha_fin.isoformat()}
    assert contexto["parcial"] is True


# ── Filtros: calendario por colegio ─────────────────────────────────────────────────

def test_colegio_sin_calendario_en_un_bloque_del_periodo(cliente_filtros, cabeceras, db, entorno):
    # El año siguiente solo tiene el particular de Andino: Lejano no tiene calendario aplicable.
    otro = _otro_anio(db, entorno, id_colegio=entorno.colegios["Andino"].id_colegio)
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, todos_los_colegios=True,
                           id_anio_escolar=otro.id_anio_escolar,
                           id_periodo_academico=_pa(db, otro, 1).id_periodo_academico)
    assert respuesta.status_code == 422, respuesta.text
    assert motivo(respuesta) == "calendario_no_configurado"
    assert respuesta.json()["detail"] == MENSAJE_SIN_CALENDARIO
    lejano = entorno.colegios["Lejano"]
    assert respuesta.json()["colegios_sin_calendario"] == [{"id": lejano.id_colegio, "nombre": lejano.nombre}]


def test_colegio_sin_calendario_no_impide_un_bloque_anual(cliente_filtros, cabeceras, db, entorno):
    otro = _otro_anio(db, entorno, id_colegio=entorno.colegios["Andino"].id_colegio)
    respuesta = _consultar(cliente_filtros, cabeceras, entorno, "/anual", todos_los_colegios=True,
                           id_anio_escolar=otro.id_anio_escolar)
    assert respuesta.status_code == 200, respuesta.text
    assert len(respuesta.json()["colegios"]) == 2


# ── Roles ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ruta", ["/periodo", "/anual"])
def test_filtros_directivo_puede_y_docente_no(cliente_filtros, cabeceras, entorno, ruta):
    assert _consultar(cliente_filtros, cabeceras, entorno, ruta, "Directivo", todos_los_colegios=True).status_code == 200
    docente = _consultar(cliente_filtros, cabeceras, entorno, ruta, "Docente", todos_los_colegios=True)
    assert docente.status_code == 403
    assert motivo(docente) == "sin_permiso"


def test_filtros_sin_token(cliente_filtros, entorno):
    respuesta = cliente_filtros.get("/periodo", params={"id_anio_escolar": entorno.anio.id_anio_escolar})
    assert respuesta.status_code == 401


# ── GET /dashboard/filtros ──────────────────────────────────────────────────────────

def _carga_inicial(client, cabeceras, rol="Supervisor"):
    respuesta = client.get("/dashboard/filtros", headers=cabeceras[rol])
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _carga_inicial_tras_mover_el_reloj(client, db, entorno):
    """Con el reloj movido, la sesión de `cabeceras` ya venció: se abre otra."""
    respuesta = client.get("/dashboard/filtros", headers=cabecera_de(db, entorno.supervisor))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _seleccion(entorno, clave):
    return {"id_anio_escolar": entorno.anio.id_anio_escolar,
            "id_periodo_academico": entorno.periodos[clave].id_periodo_academico}


def test_carga_inicial_durante_un_pa(client, cabeceras, entorno):
    datos = _carga_inicial(client, cabeceras)
    assert [c["nombre"] for c in datos["colegios"]] == ["Colegio Andino", "Colegio Lejano"]
    assert datos["anios"] == [{
        "id": entorno.anio.id_anio_escolar,
        "nombre": entorno.anio.nombre,
        "periodos": [
            {"id": entorno.periodos[clave].id_periodo_academico, "numero": numero, "nombre": nombre}
            for clave, numero, nombre in (
                ("pasado", 1, "Primer periodo"), ("vigente", 2, "Segundo periodo"),
                ("futuro", 3, "Tercer periodo"), ("cuarto", 4, "Cuarto periodo"),
            )
        ],
    }]
    assert datos["seleccion_inicial"] == _seleccion(entorno, "vigente")


def test_carga_inicial_del_directivo(client, cabeceras, entorno):
    assert _carga_inicial(client, cabeceras, "Directivo")["seleccion_inicial"] == _seleccion(entorno, "vigente")


def test_carga_inicial_durante_un_receso_toma_el_pa_anterior(client, cabeceras, db, entorno):
    terminar_pa_vigente(db, entorno)
    assert _carga_inicial(client, cabeceras)["seleccion_inicial"] == _seleccion(entorno, "vigente")


def test_carga_inicial_receso_en_el_anio_calendario_siguiente_conserva_su_anio(client, db, entorno, reloj):
    # El PR4 se extiende hasta enero del año calendario siguiente y hoy cae ahí.
    enero = date(entorno.inicio("PR4").year + 1, 1, 10)
    fijar_periodo(db, entorno.calendario, "PR4", fin=enero)
    reloj.fijar(a_las(enero))
    assert _carga_inicial_tras_mover_el_reloj(client, db, entorno)["seleccion_inicial"] == _seleccion(entorno, "cuarto")


def test_carga_inicial_con_colegios_en_pa_distintos_es_null(client, cabeceras, db, entorno):
    # Lejano ya empezó su PA3 mientras Andino (calendario general) sigue en el PA2.
    hoy = entorno.hoy
    rangos = rangos_relativos(hoy)
    rangos["PA2"] = (rangos["PA2"][0], hoy - timedelta(days=2))
    rangos["PR2"] = (hoy - timedelta(days=1), hoy - timedelta(days=1))
    rangos["PA3"] = (hoy, rangos["PA3"][1])
    crear_particular(db, entorno, rangos)
    assert _carga_inicial(client, cabeceras)["seleccion_inicial"] is None


def test_carga_inicial_fuera_de_todo_periodo_es_null(client, db, entorno, reloj):
    reloj.fijar(a_las(entorno.fin("PR4") + timedelta(days=1)))
    assert _carga_inicial_tras_mover_el_reloj(client, db, entorno)["seleccion_inicial"] is None


def test_carga_inicial_sin_calendario_es_null(client, cabeceras, db, entorno):
    # Un calendario sin periodos no se resuelve: ningún colegio tiene calendario aplicable.
    for periodo in db.exec(select(CalendarioPeriodo).where(
        CalendarioPeriodo.id_calendario == entorno.calendario.id_calendario
    )).all():
        db.delete(periodo)
    db.commit()
    datos = _carga_inicial(client, cabeceras)
    assert datos["seleccion_inicial"] is None
    assert len(datos["anios"]) == 1


def test_carga_inicial_roles(client, cabeceras):
    docente = client.get("/dashboard/filtros", headers=cabeceras["Docente"])
    assert docente.status_code == 403
    assert motivo(docente) == "sin_permiso"
    assert client.get("/dashboard/filtros").status_code == 401
