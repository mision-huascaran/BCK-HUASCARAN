"""Programación de clases y reprogramaciones (CU013, T6)."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.paginacion import TAMANO_PAGINA
from app.models.evaluacion import ClaseReprogramacion
from app.models.organizacion import AlumnoPertenencia, ColegioGrado
from app.services.programacion import FiltroGrupos, clases_programadas
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    crear_alumno,
    crear_particular,
    motivo,
    rangos_relativos,
    registrar_asistencia,
)

# Miércoles. PA2 del general: 18/05 al 17/07; PA1: 18/01 al 18/04; PR1: 19/04 al 17/05.
HOY = date(2026, 6, 17)
LUNES, MARTES, MIERCOLES = date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17)
LUNES_SIGUIENTE = date(2026, 6, 22)
FERIADO = date(2026, 6, 12)  # viernes
SABADO = date(2026, 6, 13)
LUNES_EN_RECESO = date(2026, 5, 4)
LUNES_DEL_PA1 = date(2026, 4, 13)
CON_ASISTENCIAS = "Esta clase ya tiene asistencias registradas y no puede reprogramarse."


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture(autouse=True)
def calendario(miercoles, db, entorno):
    agregar_no_lectivo(db, entorno.calendario, FERIADO)


def dia_de_clase(db, entorno, dia, colegio="Andino", grado="2.º") -> None:
    fila = db.get(ColegioGrado, (entorno.colegios[colegio].id_colegio, entorno.grados[grado]))
    fila.dia_clase = dia
    db.add(fila)
    db.commit()


def ana(entorno):
    return entorno.alumnos["ana"]


def programadas(db, entorno, desde, hasta, colegio="Andino", grado="2.º"):
    filtro = FiltroGrupos(entorno.colegios[colegio].id_colegio, entorno.grados[grado])
    return clases_programadas(db, filtro, desde, hasta)


def listar(client, cabecera, entorno, colegio="Andino", **params):
    return client.get(f"/colegios/{entorno.colegios[colegio].id_colegio}/clases", params=params, headers=cabecera)


def reprogramar(client, cabeceras, entorno, original, nueva, motivo_="Actuación", **extra):
    cuerpo = {"id_grado": ana(entorno).id_grado, "id_seccion": ana(entorno).id_seccion,
              "fecha_original": original.isoformat(), "fecha_nueva": nueva.isoformat(), "motivo": motivo_, **extra}
    return client.post(f"/colegios/{ana(entorno).id_colegio}/clases/reprogramaciones", json=cuerpo,
                       headers=cabeceras["Supervisor"])


# ── Programación ────────────────────────────────────────────────────────────────────

def test_habitual_en_cada_dia_de_clase_lectivo_del_pa(db, entorno):
    dia_de_clase(db, entorno, 1)

    fechas = [c.fecha for c in programadas(db, entorno, date(2026, 6, 1), date(2026, 6, 30))]

    assert fechas == [date(2026, 6, 1), date(2026, 6, 8), LUNES, LUNES_SIGUIENTE, date(2026, 6, 29)]
    assert all(c.id_clase is None and c.tipo == "habitual" for c in programadas(db, entorno, LUNES, LUNES))


def test_no_hay_clase_en_dia_no_lectivo_ni_en_receso(db, entorno):
    dia_de_clase(db, entorno, 5)
    assert FERIADO not in [c.fecha for c in programadas(db, entorno, date(2026, 6, 1), date(2026, 6, 30))]

    dia_de_clase(db, entorno, 1)
    assert programadas(db, entorno, LUNES_EN_RECESO, LUNES_EN_RECESO) == []


def test_sin_alumnos_o_sin_dia_no_hay_clases(db, entorno):
    dia_de_clase(db, entorno, 1, grado="3.º")

    assert programadas(db, entorno, LUNES, LUNES, grado="3.º") == []
    assert programadas(db, entorno, LUNES, LUNES, grado="1.º") == []


def test_dos_colegios_con_calendarios_distintos(db, entorno):
    """Lejano empieza su PA2 el 8 de junio: en junio tiene un lunes menos que Andino."""
    rangos = rangos_relativos(HOY)
    rangos["PR1"] = (rangos["PR1"][0], date(2026, 6, 7))
    rangos["PA2"] = (date(2026, 6, 8), rangos["PA2"][1])
    crear_particular(db, entorno, rangos)
    dia_de_clase(db, entorno, 1)
    dia_de_clase(db, entorno, 1, colegio="Lejano", grado="3.º")

    andino = programadas(db, entorno, date(2026, 6, 1), date(2026, 6, 30))
    lejano = programadas(db, entorno, date(2026, 6, 1), date(2026, 6, 30), colegio="Lejano", grado="3.º")

    assert (len(andino), len(lejano)) == (5, 4)


# ── Listado ─────────────────────────────────────────────────────────────────────────

def test_listado_arreglo_y_paginado(client, db, entorno, cabeceras):
    dia_de_clase(db, entorno, 1)
    dia_de_clase(db, entorno, 1, grado="1.º")

    arreglo = listar(client, cabeceras["Supervisor"], entorno, desde="2026-06-15", hasta="2026-06-22").json()
    pagina = listar(client, cabeceras["Supervisor"], entorno, page=1).json()

    assert [(c["fecha"], c["id_grado"]) for c in arreglo] == [
        ("2026-06-15", entorno.grados["1.º"]), ("2026-06-15", entorno.grados["2.º"]),
        ("2026-06-22", entorno.grados["1.º"]), ("2026-06-22", entorno.grados["2.º"]),
    ]
    assert set(arreglo[0]) == {"id_clase", "id_grado", "id_seccion", "fecha_original", "fecha", "tipo", "reprogramada",
                               "tiene_asistencias", "id_periodo_academico", "version", "subprogramas"}
    assert (pagina["page_size"], len(pagina["items"])) == (TAMANO_PAGINA, min(TAMANO_PAGINA, pagina["total"]))


# ── Subprogramas (CU013) ────────────────────────────────────────────────────────────

ALFA, CL = "Alfabetización", "Comprensión Lectora"


def subprogramas(clase) -> list[str]:
    return [s["nombre"] for s in clase["subprogramas"]]


@pytest.fixture
def beto(db, entorno):
    """Beto, en Alfabetización, en el 2.º del Andino con Ana (Comprensión Lectora): el 2.º
    tiene los dos subprogramas y el 1.º (Luis), solo Alfabetización. Clase los lunes."""
    dia_de_clase(db, entorno, 1)
    dia_de_clase(db, entorno, 1, grado="1.º")
    return crear_alumno(db, "Beto", "Alfa", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas[ALFA], 1, desde=entorno.anio.fecha_inicio)


def test_subprogramas_de_la_clase_en_una_sola_fila(client, entorno, cabeceras, beto):
    sup = cabeceras["Supervisor"]
    lunes = {"desde": LUNES.isoformat(), "hasta": LUNES.isoformat()}

    todas = listar(client, sup, entorno, **lunes).json()
    con_alfa = listar(client, sup, entorno, id_programa=entorno.programas[ALFA], **lunes).json()
    con_cl = listar(client, sup, entorno, id_programa=entorno.programas[CL], **lunes).json()

    assert [(c["id_grado"], subprogramas(c)) for c in todas] == [
        (entorno.grados["1.º"], [ALFA]), (entorno.grados["2.º"], sorted([ALFA, CL], key=lambda n: entorno.programas[n])),
    ]
    assert [c["id_grado"] for c in con_alfa] == [entorno.grados["1.º"], entorno.grados["2.º"]]
    # El 1.º no tiene alumnos de Comprensión Lectora; el 2.º sale una sola vez.
    assert [c["id_grado"] for c in con_cl] == [entorno.grados["2.º"]]


def test_subprogramas_de_la_lista_congelada_y_de_la_calculada(client, db, entorno, cabeceras, beto):
    registrar_asistencia(db, ana(entorno), LUNES, lista=[ana(entorno), beto])
    # Después de congelar la lista del lunes, Beto pasa a Comprensión Lectora desde el inicio.
    intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == beto.id_alumno)).one()
    intervalo.id_programa = entorno.programas[CL]
    db.add(intervalo)
    db.commit()

    congelada, calculada = listar(client, cabeceras["Supervisor"], entorno, desde=LUNES.isoformat(),
                                  hasta=LUNES_SIGUIENTE.isoformat(), id_grado=entorno.grados["2.º"]).json()

    assert len(subprogramas(congelada)) == 2
    assert subprogramas(calculada) == [CL]


def test_filtro_por_subprograma_antes_de_paginar(client, entorno, cabeceras, beto):
    sup = cabeceras["Supervisor"]

    del_segundo = listar(client, sup, entorno, id_grado=entorno.grados["2.º"], page=1).json()
    con_cl = listar(client, sup, entorno, id_programa=entorno.programas[CL], page=1).json()
    con_alfa = listar(client, sup, entorno, id_programa=entorno.programas[ALFA], page=1).json()
    todas = listar(client, sup, entorno, page=1).json()

    assert con_cl["total"] == del_segundo["total"] > 0
    assert len(con_cl["items"]) == min(TAMANO_PAGINA, con_cl["total"])
    assert all(CL in subprogramas(c) for c in con_cl["items"])
    assert con_alfa["total"] == todas["total"]


def test_listado_marca_las_que_tienen_asistencias(client, db, entorno, cabeceras):
    dia_de_clase(db, entorno, 1)
    registrar_asistencia(db, ana(entorno), LUNES)

    [clase] = listar(client, cabeceras["Docente"], entorno, desde=LUNES.isoformat(), hasta=LUNES.isoformat(),
                     id_grado=entorno.grados["2.º"]).json()

    assert (clase["tiene_asistencias"], clase["id_clase"] is not None, clase["version"]) == (True, True, 1)


def test_listado_roles_y_errores(client, entorno, cabeceras):
    assert listar(client, {}, entorno).status_code == 401
    assert listar(client, cabeceras["Directivo"], entorno).status_code == 403
    ajeno = listar(client, cabeceras["Docente"], entorno, colegio="Lejano")
    invertido = listar(client, cabeceras["Supervisor"], entorno, desde="2026-06-20", hasta="2026-06-01")
    inexistente = client.get("/colegios/999/clases", headers=cabeceras["Supervisor"])

    assert (ajeno.status_code, motivo(ajeno)) == (403, "aula_fuera_de_alcance")
    assert (invertido.status_code, motivo(invertido)) == (422, "rango_fechas_invalido")
    assert (inexistente.status_code, motivo(inexistente)) == (404, "colegio_no_encontrado")


# ── Reprogramación ──────────────────────────────────────────────────────────────────

@pytest.fixture
def los_lunes(db, entorno):
    dia_de_clase(db, entorno, 1)


def test_reprogramar_mueve_la_obligacion(client, db, entorno, cabeceras, los_lunes):
    respuesta = reprogramar(client, cabeceras, entorno, LUNES, MIERCOLES)

    assert respuesta.status_code == 201, respuesta.text
    clase = respuesta.json()
    assert (clase["fecha_original"], clase["fecha"], clase["reprogramada"], clase["version"]) == (
        LUNES.isoformat(), MIERCOLES.isoformat(), True, 1)
    fechas = [c.fecha for c in programadas(db, entorno, LUNES, MIERCOLES)]
    assert fechas == [MIERCOLES]


def test_reprogramar_de_nuevo_deja_historial(client, db, entorno, cabeceras, los_lunes):
    primera = reprogramar(client, cabeceras, entorno, LUNES, MIERCOLES).json()
    segunda = reprogramar(client, cabeceras, entorno, LUNES, MARTES, version=primera["version"])

    assert segunda.status_code == 201, segunda.text
    historial = client.get(f"/clases/{primera['id_clase']}/reprogramaciones", headers=cabeceras["Docente"]).json()
    assert [(h["fecha_anterior"], h["fecha_nueva"]) for h in historial] == [
        (LUNES.isoformat(), MIERCOLES.isoformat()), (MIERCOLES.isoformat(), MARTES.isoformat())]
    assert historial[0]["usuario"]["nombre"] == "Sara Supervisora"
    assert segunda.json()["version"] == 2


def test_reprogramar_con_version_vieja_es_409(client, cabeceras, entorno, los_lunes):
    reprogramar(client, cabeceras, entorno, LUNES, MIERCOLES)

    sin_fila = reprogramar(client, cabeceras, entorno, LUNES, MARTES, version=None)
    vieja = reprogramar(client, cabeceras, entorno, LUNES, MARTES, version=5)

    assert (sin_fila.status_code, motivo(sin_fila)) == (409, "modificacion_concurrente")
    assert (vieja.status_code, motivo(vieja)) == (409, "modificacion_concurrente")


def test_reprogramar_con_asistencias_es_409(client, db, entorno, cabeceras, los_lunes):
    registrar_asistencia(db, ana(entorno), LUNES)

    respuesta = reprogramar(client, cabeceras, entorno, LUNES, MARTES)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (
        409, "clase_con_asistencias", CON_ASISTENCIAS)


@pytest.mark.parametrize(("nueva", "codigo", "mensaje"), [
    (LUNES, "fecha_sin_cambio", "Selecciona una fecha distinta de la fecha actual de la clase."),
    (SABADO, "fecha_no_lectiva", "La nueva fecha no es un día lectivo del calendario del colegio."),
    (FERIADO, "fecha_no_lectiva", "La nueva fecha no es un día lectivo del calendario del colegio."),
    (LUNES_EN_RECESO + timedelta(days=1), "fecha_en_receso", "La nueva fecha corresponde a un periodo de receso."),
    (LUNES_DEL_PA1, "fecha_de_otro_periodo", "La nueva fecha debe pertenecer al mismo periodo académico de la clase."),
    (LUNES_SIGUIENTE, "fecha_ocupada", "El grupo ya tiene una clase el 22/06/2026."),
])
def test_reprogramar_valida_la_fecha_nueva(client, cabeceras, entorno, los_lunes, nueva, codigo, mensaje):
    respuesta = reprogramar(client, cabeceras, entorno, LUNES, nueva)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (422, codigo, mensaje)
    assert respuesta.json()["errores"] == [{"campo": "fecha_nueva", "mensaje": mensaje}]


def test_reprogramar_a_una_adicional_existente_es_fecha_ocupada(client, db, entorno, cabeceras, los_lunes):
    registrar_asistencia(db, ana(entorno), MARTES)

    respuesta = reprogramar(client, cabeceras, entorno, LUNES, MARTES)

    assert motivo(respuesta) == "fecha_ocupada"


def test_reprogramar_errores_de_la_peticion(client, db, entorno, cabeceras, los_lunes):
    sin_motivo = reprogramar(client, cabeceras, entorno, LUNES, MARTES, motivo_="  ")
    sin_clase = reprogramar(client, cabeceras, entorno, MARTES, MIERCOLES)
    docente = client.post(f"/colegios/{ana(entorno).id_colegio}/clases/reprogramaciones", json={},
                          headers=cabeceras["Docente"])
    seccion_ajena = client.post(
        f"/colegios/{entorno.colegios['Lejano'].id_colegio}/clases/reprogramaciones",
        json={"id_grado": ana(entorno).id_grado, "id_seccion": ana(entorno).id_seccion, "fecha_original": "2026-06-15",
              "fecha_nueva": "2026-06-16", "motivo": "x"},
        headers=cabeceras["Supervisor"],
    )

    assert (sin_motivo.status_code, motivo(sin_motivo)) == (422, "validacion")
    assert (sin_clase.status_code, motivo(sin_clase)) == (404, "clase_no_programada")
    assert docente.status_code == 403
    assert (seccion_ajena.status_code, motivo(seccion_ajena)) == (422, "seccion_inexistente")
    assert db.exec(select(ClaseReprogramacion)).all() == []


def test_historial_errores(client, db, entorno, cabeceras, los_lunes):
    id_clase = reprogramar(client, cabeceras, entorno, LUNES, MARTES).json()["id_clase"]

    assert client.get("/clases/999/reprogramaciones", headers=cabeceras["Supervisor"]).status_code == 404
    assert client.get(f"/clases/{id_clase}/reprogramaciones").status_code == 401
    assert client.get(f"/clases/{id_clase}/reprogramaciones", headers=cabeceras["Directivo"]).status_code == 403
