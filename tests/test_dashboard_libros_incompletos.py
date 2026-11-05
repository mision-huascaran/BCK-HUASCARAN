"""Dashboard D5: detalle del aviso "Información incompleta" de CU034
(`GET /dashboard/libros/incompletos`).

Mismo escenario que `test_dashboard_libros.py`: hoy es miércoles 17/06/2026 y los grados
no tienen día de clase salvo que el test lo fije. La regla es la misma que marca
`incompleto` en el consolidado: los tests de coherencia lo comparan contra
`/dashboard/libros` y su detalle por colegio.
"""
from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.services.dashboard.filtros import ParametrosFiltros, resolver_filtros
from app.services.dashboard.libros import acumular, lecturas_del_anio
from tests.conftest import a_las, motivo
from tests.test_dashboard_libros import (
    HOY, LSN, LUNES, _colegio_detalle, _companeros, _dia_clase, _get, _leyo, _libros, _ok, _pasa_a_tercero, _tabla,
)

RUTA = "libros/incompletos"


@pytest.fixture(autouse=True)
def fecha_fija(reloj):
    reloj.fijar(a_las(HOY, 9))


def _incompletos(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, RUTA, **params))


def _items(client, cabeceras, entorno, **params) -> list[dict]:
    return _incompletos(client, cabeceras, entorno, **params)["items"]


# ── Qué aparece ─────────────────────────────────────────────────────────────────────

def test_semana_esperada_sin_tabla_con_la_clase_habitual(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    _leyo(db, entorno.alumnos["ana"], date(2026, 6, 8), lsl=1)
    items = _items(client, cabeceras, entorno, anio=2026, mes=6)
    # La semana del 8 tiene tabla completa y la del 15 todavía no vence: falta la del 1.
    assert items == [{
        "colegio": {"id": entorno.colegios["Andino"].id_colegio, "nombre": "Colegio Andino"},
        "grado": {"id": entorno.grados["2.º"], "nombre": "2.º"},
        "seccion": items[0]["seccion"],
        "subprograma": {"id": entorno.programas["Comprensión Lectora"], "nombre": "Comprensión Lectora"},
        "semana": {"lunes": "2026-06-01", "viernes": "2026-06-05"},
        "anio": 2026, "mes": 6,
        "clase_referencia": {"id_clase": None, "fecha": "2026-06-01"},
        "motivo": "sin_tabla",
        "id_lectura_semanal": None,
        "filas_pendientes": None,
    }]
    assert items[0]["seccion"]["nombre"] == "Única"


def test_tabla_con_filas_pendientes(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otros = _companeros(db, entorno, 2)
    tabla = _tabla(db, date(2026, 3, 9), [
        (ana, "presente", {"cantidad_lsl": 2}), (otros[0], None, {}), (otros[1], "presente", {}),
    ])
    items = _items(client, cabeceras, entorno)
    assert [(i["motivo"], i["filas_pendientes"], i["id_lectura_semanal"]) for i in items] == [
        ("filas_pendientes", 2, tabla.id),
    ]
    assert items[0]["clase_referencia"] == {"id_clase": tabla.id_clase_referencia, "fecha": "2026-03-09"}
    assert (items[0]["semana"], items[0]["anio"], items[0]["mes"]) == (
        {"lunes": "2026-03-09", "viernes": "2026-03-13"}, 2026, 3,
    )


def test_tabla_completa_no_realizada_o_que_no_vence_no_aparece(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _leyo(db, ana, date(2026, 3, 9), lsn=True, lsl=1)
    _tabla(db, date(2026, 3, 16), [(ana, None, {}), (otro, None, {})], estado="no_realizada")
    _tabla(db, HOY - timedelta(days=2), [(ana, "presente", {"cantidad_lsl": 2}), (otro, None, {})])
    datos = _incompletos(client, cabeceras, entorno)
    assert (datos["items"], datos["total"]) == ([], 0)
    assert _colegio_detalle(client, cabeceras, entorno)["resumen"]["estado"] == "completo"


def test_orden_y_paginacion(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    _dia_clase(db, entorno, LUNES, grado="1.º")
    _dia_clase(db, entorno, LUNES, colegio="Lejano", grado="3.º")
    datos = _incompletos(client, cabeceras, entorno)
    assert (datos["page"], datos["page_size"]) == (1, 40)
    assert datos["total"] > 40
    assert len(datos["items"]) == 40
    todos = datos["items"] + _incompletos(client, cabeceras, entorno, page=2)["items"]
    todos += _incompletos(client, cabeceras, entorno, page=3)["items"]
    assert len(todos) == datos["total"]
    claves = [(i["colegio"]["nombre"], i["grado"]["nombre"], i["clase_referencia"]["fecha"]) for i in todos]
    assert claves == sorted(claves)
    assert (claves[0][0], claves[-1][0]) == ("Colegio Andino", "Colegio Lejano")


# ── Coherencia con el consolidado ───────────────────────────────────────────────────

def _escenario(db, entorno):
    """Andino 2.º con clase los lunes (semanas sin tabla), una tabla con una fila
    pendiente, otra completa, un alumno que pasa a 3.º y Lejano con una fila pendiente."""
    _dia_clase(db, entorno, LUNES)
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _tabla(db, date(2026, 3, 9), [(ana, "presente", {**LSN, "cantidad_lsl": 1}), (otro, "presente", {"cantidad_lsl": 1})])
    _tabla(db, date(2026, 3, 16), [(ana, "presente", {"cantidad_lsl": 2}), (otro, None, {})])
    _tabla(db, date(2026, 6, 8), [(ana, "presente", {"cantidad_lsl": 1}), (otro, "presente", {"cantidad_lsl": 0})])
    _pasa_a_tercero(db, entorno, entorno.alumnos["luis"], date(2026, 4, 1))
    _leyo(db, entorno.alumnos["luis"], date(2026, 4, 7), lsl=1)
    _leyo(db, entorno.alumnos["luis"], date(2026, 4, 14), lsl=None)
    _tabla(db, date(2026, 5, 5), [(entorno.alumnos["mario"], None, {})])
    _leyo(db, entorno.alumnos["mario"], date(2026, 5, 12), lsl=3)
    return otro


def _coherente(estado: str, hay_items: bool) -> bool:
    """`incompleto` siempre tiene ítems y `completo` nunca. `sin_informacion` puede
    tenerlos: un mes solo con semanas sin tabla falta, pero no tiene cantidades."""
    return {"incompleto": hay_items, "completo": not hay_items, "futuro": not hay_items}.get(estado, True)


def test_coherencia_por_colegio_y_mes(client, cabeceras, db, entorno):
    _escenario(db, entorno)
    datos = _libros(client, cabeceras, entorno)
    revisados = 0
    for colegio in datos["colegios"]:
        id_colegio = colegio["colegio"]["id"]
        items = _items(client, cabeceras, entorno, id_colegio=id_colegio)
        assert (colegio["estado"] == "incompleto") == bool(items)
        for mes in colegio["meses"]:
            del_mes = [i for i in items if (i["anio"], i["mes"]) == (mes["anio"], mes["mes"])]
            assert _coherente(mes["estado"], bool(del_mes)), (colegio["colegio"]["nombre"], mes)
            assert del_mes == _items(client, cabeceras, entorno, id_colegio=id_colegio, anio=mes["anio"],
                                     mes=mes["mes"])
            revisados += mes["estado"] == "incompleto"
    assert revisados >= 2


def test_coherencia_exacta_con_los_aportes_pendientes(client, cabeceras, db, entorno):
    """Por colegio y mes: hay ítems si y solo si algún aporte del consolidado está
    pendiente (lo que decide `incompleto` cuando además hay cantidades)."""
    _escenario(db, entorno)
    parametros = ParametrosFiltros(entorno.anio.id_anio_escolar, None, True, None)
    filtros = resolver_filtros(db, entorno.supervisor, parametros, False)
    por_mes = defaultdict(list)
    for aporte in lecturas_del_anio(db, filtros.calendarios, filtros.cargados, filtros.ids_colegio).aportes:
        por_mes[(aporte.id_colegio, aporte.anio, aporte.mes)].append(aporte)
    items = defaultdict(int)
    for item in _items(client, cabeceras, entorno):
        items[(item["colegio"]["id"], item["anio"], item["mes"])] += 1
    pendientes = {clave for clave, aportes in por_mes.items() if acumular(aportes).pendiente}
    assert pendientes
    assert pendientes == set(items)


def test_coherencia_por_grado_y_alumno_en_el_detalle(client, cabeceras, db, entorno):
    otro = _escenario(db, entorno)
    andino = entorno.colegios["Andino"].id_colegio
    detalle = _colegio_detalle(client, cabeceras, entorno)
    grados = detalle["grados"] + detalle["grados_sin_informacion"]
    assert {g["grado"]["nombre"] for g in grados} == {"2.º", "3.º"}
    for grado in grados:
        items = _items(client, cabeceras, entorno, id_colegio=andino, id_grado=grado["grado"]["id"])
        assert _coherente(grado["estado"], bool(items)), grado["grado"]
        for alumno in grado["alumnos"]:
            del_alumno = _items(client, cabeceras, entorno, id_colegio=andino, id_alumno=alumno["alumno"]["id"])
            assert _coherente(alumno["estado"], bool(del_alumno)), alumno["alumno"]
    # Ana no tiene la fila pendiente del 16/03 (es de su compañero), pero sí las semanas sin tabla.
    de_ana = _items(client, cabeceras, entorno, id_alumno=entorno.alumnos["ana"].id_alumno, anio=2026, mes=3)
    del_otro = _items(client, cabeceras, entorno, id_alumno=otro.id_alumno, anio=2026, mes=3)
    assert "filas_pendientes" not in {i["motivo"] for i in de_ana}
    assert [i["filas_pendientes"] for i in del_otro if i["motivo"] == "filas_pendientes"] == [1]


# ── Filtros ─────────────────────────────────────────────────────────────────────────

def test_filtros(client, cabeceras, db, entorno):
    _escenario(db, entorno)
    lejano = entorno.colegios["Lejano"].id_colegio
    todos = _items(client, cabeceras, entorno)
    assert {i["colegio"]["id"] for i in _items(client, cabeceras, entorno, id_colegio=lejano)} == {lejano}
    assert {i["colegio"]["id"] for i in _items(client, cabeceras, entorno, id_colegios=[lejano])} == {lejano}
    tercero = _items(client, cabeceras, entorno, id_grado=entorno.grados["3.º"])
    assert [(i["colegio"]["nombre"], i["clase_referencia"]["fecha"], i["motivo"]) for i in tercero] == [
        ("Colegio Andino", "2026-04-14", "filas_pendientes"), ("Colegio Lejano", "2026-05-05", "filas_pendientes"),
    ]
    marzo = _items(client, cabeceras, entorno, anio=2026, mes=3)
    assert marzo
    assert marzo == [i for i in todos if (i["anio"], i["mes"]) == (2026, 3)]
    mario = _items(client, cabeceras, entorno, id_alumno=entorno.alumnos["mario"].id_alumno)
    assert [(i["colegio"]["id"], i["clase_referencia"]["fecha"]) for i in mario] == [(lejano, "2026-05-05")]


# ── Roles y errores ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol", ["Directivo", "Docente"])
def test_solo_supervisor(client, cabeceras, entorno, rol):
    assert _get(client, cabeceras, entorno, RUTA, rol=rol).status_code == 403


@pytest.mark.parametrize("params, campo", [
    ({"anio": 2026}, "mes"),
    ({"mes": 3}, "anio"),
    ({"anio": 2026, "mes": 13}, "mes"),
    ({"id_alumno": 999999}, "id_alumno"),
    ({"id_grado": 999999}, "id_grado"),
])
def test_filtros_invalidos(client, cabeceras, entorno, params, campo):
    respuesta = _get(client, cabeceras, entorno, RUTA, **params)
    assert respuesta.status_code == 422
    assert campo in respuesta.text


def test_grado_y_colegio_fuera_de_la_seleccion_o_del_alcance(client, cabeceras, db, entorno):
    andino, lejano = entorno.colegios["Andino"], entorno.colegios["Lejano"]
    grado_ajeno = _get(client, cabeceras, entorno, RUTA, id_colegio=andino.id_colegio, id_grado=entorno.grados["3.º"] + 99)
    assert grado_ajeno.status_code == 422
    fuera = _get(client, cabeceras, entorno, RUTA, id_colegio=lejano.id_colegio, id_colegios=[andino.id_colegio])
    assert (fuera.status_code, fuera.json()["errores"][0]["campo"]) == (422, "id_colegio")
    lejano.activo = False
    db.add(lejano)
    db.commit()
    respuesta = _get(client, cabeceras, entorno, RUTA, id_colegio=lejano.id_colegio, id_colegios=[andino.id_colegio])
    assert (respuesta.status_code, motivo(respuesta)) == (403, "colegio_fuera_de_alcance")


def test_comun_sin_colegios_y_con_pa(client, cabeceras, entorno):
    sin_colegios = client.get("/dashboard/libros/incompletos", headers=cabeceras["Supervisor"],
                              params={"id_anio_escolar": entorno.anio.id_anio_escolar})
    con_pa = _get(client, cabeceras, entorno, RUTA, id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico)
    assert (sin_colegios.status_code, con_pa.status_code) == (422, 422)
