"""Dashboard D6: evolución anual y comparación entre colegios (CU033 paso 9, CU035).

Hoy es miércoles 17/06/2026 (reloj fijo). El año del entorno ("2026") va del 18/01/2026
al 14/11/2026 según su calendario general y hoy está en su PA2; Luis, Ana y Mario
pertenecen a su grupo desde el inicio de ese año. Algunos tests agregan un año anterior
("2025") con su calendario general.
"""
from datetime import date, timedelta

import pytest
from sqlmodel import col, select

from app.core import tiempo
from app.models.evaluacion import NivelRazkids
from app.models.organizacion import AlumnoPertenencia, AnioEscolar, ColegioPrograma
from app.services.dashboard.cobertura import MENSAJE_UN_SOLO_ANIO
from app.services.dashboard.comparacion_colegios import MENSAJE_UN_COLEGIO, MENSAJE_UNO_CON_DATOS
from tests.conftest import (
    a_las, crear_alumno, crear_calendario, crear_colegio, decision_de_vuelo, motivo, registrar_asistencia,
    seccion_de, tabla_lectura,
)

HOY = date(2026, 6, 17)
CL, ALFA = "Comprensión Lectora", "Alfabetización"
RANGOS_2025 = {
    "PA1": (date(2025, 3, 3), date(2025, 5, 30)), "PR1": (date(2025, 5, 31), date(2025, 6, 15)),
    "PA2": (date(2025, 6, 16), date(2025, 8, 29)), "PR2": (date(2025, 8, 30), date(2025, 9, 7)),
    "PA3": (date(2025, 9, 8), date(2025, 10, 31)), "PR3": (date(2025, 11, 1), date(2025, 11, 2)),
    "PA4": (date(2025, 11, 3), date(2025, 11, 14)), "PR4": (date(2025, 11, 15), date(2025, 11, 20)),
}


@pytest.fixture(autouse=True)
def fecha_fija(reloj):
    reloj.fijar(a_las(HOY, 9))


# ── Preparación ─────────────────────────────────────────────────────────────────────

def _get(client, cabeceras, entorno, ruta, rol="Supervisor", **params):
    if "id_colegios" not in params:
        params["todos_los_colegios"] = True
    params.setdefault("id_anio_escolar", entorno.anio.id_anio_escolar)
    return client.get(f"/dashboard/{ruta}", params=params, headers=cabeceras[rol])


def _ok(respuesta) -> dict:
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _evolucion(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, "evolucion-anual", **params))


def _comparar(client, cabeceras, entorno, indicador, **params) -> dict:
    if indicador != "promedio_libros":
        params.setdefault("id_periodo_academico", entorno.periodos["vigente"].id_periodo_academico)
    return _ok(_get(client, cabeceras, entorno, "comparacion-colegios", indicador=indicador, **params))


def _anio_2025(db, entorno) -> AnioEscolar:
    anio = AnioEscolar(nombre="2025", fecha_inicio=date(2025, 2, 1), fecha_fin=date(2025, 11, 20),
                       creado_por=1, creado_en=tiempo.ahora_utc())
    db.add(anio)
    db.flush()
    crear_calendario(db, anio.id_anio_escolar, RANGOS_2025, 1)
    db.commit()
    return anio


def _pertenecio(db, alumno, colegio, desde, hasta, id_programa=None, id_grado=None):
    """Intervalo de pertenencia cerrado del alumno en un colegio."""
    grado = id_grado or alumno.id_grado
    db.add(AlumnoPertenencia(
        id_alumno=alumno.id_alumno, id_colegio=colegio.id_colegio, id_grado=grado,
        id_seccion=seccion_de(db, colegio, grado).id_seccion, id_programa=id_programa or alumno.id_programa_actual,
        desde=desde, hasta=hasta, creado_por=1, creado_en=tiempo.ahora_utc(),
    ))
    db.commit()


def _vigente(db, alumno) -> AlumnoPertenencia:
    return db.exec(select(AlumnoPertenencia).where(
        AlumnoPertenencia.id_alumno == alumno.id_alumno, col(AlumnoPertenencia.hasta).is_(None),
    )).one()


def _cerrar(db, alumno, hasta):
    vigente = _vigente(db, alumno)
    vigente.hasta = hasta
    db.add(vigente)
    db.commit()


def _anio(datos, nombre):
    return next(a for a in datos["anios"] if a["anio_escolar"]["nombre"] == nombre)


def _por_colegio(anio) -> dict[str, int]:
    return {c["colegio"]["nombre"]: c["alumnos_atendidos"] for c in anio["colegios"]}


def _letra(db, posicion=5) -> int:
    return db.exec(select(NivelRazkids.id_nivel_rk).order_by(NivelRazkids.id_nivel_rk)).all()[posicion]


# ── Cobertura ───────────────────────────────────────────────────────────────────────

def test_alumno_que_cambio_de_colegio_cuenta_una_vez_en_el_total(client, cabeceras, db, entorno):
    ana, lejano = entorno.alumnos["ana"], entorno.colegios["Lejano"]
    cambio = date(2026, 4, 1)
    _cerrar(db, ana, cambio)
    _pertenecio(db, ana, lejano, cambio, None)
    vacio = crear_colegio(db, "Colegio Vacío", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    db.commit()
    anio = _anio(_evolucion(client, cabeceras, entorno), entorno.anio.nombre)
    assert (anio["alumnos_atendidos"], anio["colegios_atendidos"]) == (3, 2)  # Luis, Ana y Mario
    assert _por_colegio(anio) == {"Colegio Andino": 2, "Colegio Lejano": 2}
    assert vacio.nombre not in _por_colegio(anio)


def test_alumno_inactivo_hoy_que_pertenecio_cuenta(client, cabeceras, db, entorno):
    luis = entorno.alumnos["luis"]
    _cerrar(db, luis, date(2026, 3, 1))
    luis.activo = False
    db.add(luis)
    db.commit()
    assert _anio(_evolucion(client, cabeceras, entorno), entorno.anio.nombre)["alumnos_atendidos"] == 3


def test_no_se_suman_los_pa_ni_los_cambios_de_subprograma(client, cabeceras, db, entorno):
    # Ana estuvo los cuatro PA y además cambió de subprograma: sigue siendo una alumna.
    ana, andino = entorno.alumnos["ana"], entorno.colegios["Andino"]
    _cerrar(db, ana, date(2026, 5, 1))
    _pertenecio(db, ana, andino, date(2026, 5, 1), None, id_programa=entorno.programas[ALFA])
    anio = _anio(_evolucion(client, cabeceras, entorno), entorno.anio.nombre)
    assert (anio["alumnos_atendidos"], _por_colegio(anio)["Colegio Andino"]) == (3, 2)


def test_un_solo_anio_con_informacion(client, cabeceras, entorno):
    datos = _evolucion(client, cabeceras, entorno)
    assert [(a["anio_escolar"]["nombre"], a["parcial"]) for a in datos["anios"]] == [(entorno.anio.nombre, True)]
    assert datos["mensaje"] == MENSAJE_UN_SOLO_ANIO


def test_anio_completo_y_anio_parcial(client, cabeceras, db, entorno):
    anterior = _anio_2025(db, entorno)
    _pertenecio(db, entorno.alumnos["mario"], entorno.colegios["Lejano"], date(2025, 3, 3), date(2025, 11, 21))
    datos = _evolucion(client, cabeceras, entorno)
    assert [(a["anio_escolar"]["nombre"], a["parcial"], a["alumnos_atendidos"]) for a in datos["anios"]] == [
        ("2025", False, 1), (entorno.anio.nombre, True, 3),
    ]
    assert datos["mensaje"] is None
    assert datos["contexto"]["intervalo"]["desde"] == "2025-03-03"
    # Hasta el año seleccionado: con 2025 elegido, solo 2025.
    solo = _evolucion(client, cabeceras, entorno, id_anio_escolar=anterior.id_anio_escolar)
    assert [a["anio_escolar"]["nombre"] for a in solo["anios"]] == ["2025"]
    assert solo["mensaje"] == MENSAJE_UN_SOLO_ANIO


def test_anio_sin_alumnos_atendidos_no_aparece(client, cabeceras, db, entorno):
    _anio_2025(db, entorno)
    assert [a["anio_escolar"]["nombre"] for a in _evolucion(client, cabeceras, entorno)["anios"]] == [entorno.anio.nombre]


def test_libros_por_anio_sin_ceros_inventados(client, cabeceras, db, entorno):
    _anio_2025(db, entorno)
    _pertenecio(db, entorno.alumnos["mario"], entorno.colegios["Lejano"], date(2025, 3, 3), date(2025, 11, 21))
    ana, fecha = entorno.alumnos["ana"], date(2026, 3, 9)
    registrar_asistencia(db, ana, fecha, "presente")
    tabla_lectura(db, ana, fecha, cantidad_lsl=3)
    datos = _evolucion(client, cabeceras, entorno)
    assert _anio(datos, "2025")["libros"] == {"total": None, "estado": "sin_informacion"}
    assert _anio(datos, entorno.anio.nombre)["libros"] == {"total": 3, "estado": "completo"}


# ── Comparación: el mismo valor que su bloque ───────────────────────────────────────

def _valores(datos) -> dict[str, float]:
    return {f["colegio"]["nombre"]: f["valor"] for f in datos["ranking"]}


def test_nivel_global_igual_a_la_comparacion_de_resultados(client, cabeceras, db, entorno):
    vigente = entorno.periodos["vigente"]
    decision_de_vuelo(db, entorno.alumnos["ana"], vigente, _letra(db, 12))
    decision_de_vuelo(db, entorno.alumnos["mario"], vigente, _letra(db, 3))
    bloque = _ok(_get(client, cabeceras, entorno, "comparacion-resultados", id_programa=entorno.programas[CL],
                      todos_los_grados=True, id_periodo_academico=vigente.id_periodo_academico))
    datos = _comparar(client, cabeceras, entorno, "nivel_global_comprension")
    assert _valores(datos) == {f["colegio"]["nombre"]: f["indice"] for f in bloque["ranking"]}
    assert [(f["colegio"]["nombre"], f["posicion"]) for f in datos["ranking"]] == [
        (f["colegio"]["nombre"], f["posicion"]) for f in bloque["ranking"]
    ]
    assert datos["ranking"][0]["poblacion"] == {"descripcion": "alumnos evaluados", "cantidad": 1}
    assert (datos["intervalo"], datos["mensaje"]) == ("periodo", None)


def test_nivel_global_de_alfabetizacion_con_un_solo_colegio_con_datos(client, cabeceras, db, entorno):
    decision_de_vuelo(db, entorno.alumnos["luis"], entorno.periodos["vigente"], _letra(db))
    db.delete(db.get(ColegioPrograma, (entorno.colegios["Lejano"].id_colegio, entorno.programas[ALFA])))
    db.commit()
    datos = _comparar(client, cabeceras, entorno, "nivel_global_alfabetizacion")
    assert [(f["colegio"]["nombre"], f["posicion"]) for f in datos["ranking"]] == [("Colegio Andino", None)]
    assert datos["sin_informacion"] == [{"colegio": {"id": entorno.colegios["Lejano"].id_colegio,
                                                     "nombre": "Colegio Lejano"}, "motivo": "no_aplica"}]
    assert datos["mensaje"] == MENSAJE_UNO_CON_DATOS


def test_asistencia_igual_al_bloque_de_asistencia(client, cabeceras, db, entorno):
    ana, mario = entorno.alumnos["ana"], entorno.alumnos["mario"]
    registrar_asistencia(db, ana, HOY - timedelta(days=9), "presente")
    registrar_asistencia(db, ana, HOY - timedelta(days=2), "ausente")
    registrar_asistencia(db, ana, HOY - timedelta(days=1), "tardanza")
    registrar_asistencia(db, mario, HOY - timedelta(days=3), "presente")
    bloque = _ok(_get(client, cabeceras, entorno, "asistencia",
                      id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico))
    datos = _comparar(client, cabeceras, entorno, "asistencia")
    assert _valores(datos) == {c["colegio"]["nombre"]: c["porcentaje"] for c in bloque["por_colegio"]}
    assert [(f["colegio"]["nombre"], f["posicion"], f["poblacion"]["cantidad"]) for f in datos["ranking"]] == [
        ("Colegio Lejano", 1, 1), ("Colegio Andino", 2, 3),
    ]


def test_promedio_de_libros_igual_al_consolidado(client, cabeceras, db, entorno):
    ana, mario = entorno.alumnos["ana"], entorno.alumnos["mario"]
    for alumno, fecha, cantidad in ((ana, date(2026, 3, 9), 3), (mario, date(2026, 3, 10), 5)):
        registrar_asistencia(db, alumno, fecha, "presente")
        tabla_lectura(db, alumno, fecha, cantidad_lsl=cantidad)
    bloque = _ok(_get(client, cabeceras, entorno, "libros"))
    datos = _comparar(client, cabeceras, entorno, "promedio_libros")
    assert _valores(datos) == {c["colegio"]["nombre"]: c["promedio"] for c in bloque["colegios"]}
    assert (datos["intervalo"], datos["ranking"][0]["poblacion"]["descripcion"]) == ("anio", "alumnos incluidos")


# ── Empates, provisional y casos sin comparación ────────────────────────────────────

def test_empates_y_provisional_con_advertencias(client, cabeceras, db, entorno):
    bajo = crear_colegio(db, "Colegio Bajo", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    beto = crear_alumno(db, "Beto", "Bajo", bajo, entorno.grados["2.º"], entorno.programas[CL], 1,
                        desde=entorno.anio.fecha_inicio)
    otro = crear_alumno(db, "Otro", "Bajo", bajo, entorno.grados["2.º"], entorno.programas[CL], 1,
                        desde=entorno.anio.fecha_inicio)
    db.commit()
    registrar_asistencia(db, entorno.alumnos["ana"], HOY - timedelta(days=2), "presente")
    registrar_asistencia(db, entorno.alumnos["mario"], HOY - timedelta(days=2), "presente")
    fecha = HOY - timedelta(days=3)
    registrar_asistencia(db, beto, fecha, "presente", lista=[beto, otro])
    registrar_asistencia(db, otro, fecha, "ausente", lista=[beto, otro])
    registrar_asistencia(db, beto, HOY - timedelta(days=1), "ausente", lista=[beto, otro])  # otro sin marcar
    ranking = _comparar(client, cabeceras, entorno, "asistencia")["ranking"]
    assert [(f["colegio"]["nombre"], f["posicion"], f["provisional"], f["advertencias"]) for f in ranking] == [
        ("Colegio Andino", 1, False, []), ("Colegio Lejano", 1, False, []),
        ("Colegio Bajo", 3, True, ["registro_incompleto"]),
    ]


def test_un_solo_colegio_sin_posicion(client, cabeceras, db, entorno):
    registrar_asistencia(db, entorno.alumnos["ana"], HOY - timedelta(days=2), "presente")
    datos = _comparar(client, cabeceras, entorno, "asistencia", id_colegios=[entorno.colegios["Andino"].id_colegio])
    assert ([f["posicion"] for f in datos["ranking"]], datos["mensaje"]) == ([None], MENSAJE_UN_COLEGIO)


def test_varios_colegios_sin_datos_van_aparte(client, cabeceras, db, entorno):
    registrar_asistencia(db, entorno.alumnos["ana"], HOY - timedelta(days=2), "presente")
    datos = _comparar(client, cabeceras, entorno, "asistencia")
    assert [(f["colegio"]["nombre"], f["posicion"]) for f in datos["ranking"]] == [("Colegio Andino", None)]
    assert [(c["colegio"]["nombre"], c["motivo"]) for c in datos["sin_informacion"]] == [
        ("Colegio Lejano", "sin_estados_registrados"),
    ]
    assert datos["mensaje"] == MENSAJE_UNO_CON_DATOS


# ── Periodo según el indicador ──────────────────────────────────────────────────────

def test_promedio_de_libros_no_admite_periodo(client, cabeceras, entorno):
    respuesta = _get(client, cabeceras, entorno, "comparacion-colegios", indicador="promedio_libros",
                     id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico)
    assert (respuesta.status_code, respuesta.json()["errores"][0]["campo"]) == (422, "id_periodo_academico")


@pytest.mark.parametrize("indicador", ["nivel_global_alfabetizacion", "nivel_global_comprension", "asistencia"])
def test_indicadores_del_periodo_exigen_el_pa(client, cabeceras, entorno, indicador):
    respuesta = _get(client, cabeceras, entorno, "comparacion-colegios", indicador=indicador)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0] == {"campo": "id_periodo_academico",
                                              "mensaje": "Selecciona un periodo académico."}


def test_indicador_inexistente(client, cabeceras, entorno):
    assert _get(client, cabeceras, entorno, "comparacion-colegios", indicador="puntaje").status_code == 422


# ── Roles ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol, esperado", [("Supervisor", 200), ("Directivo", 200), ("Docente", 403)])
def test_roles(client, cabeceras, entorno, rol, esperado):
    assert _get(client, cabeceras, entorno, "evolucion-anual", rol=rol).status_code == esperado
    comparacion = _get(client, cabeceras, entorno, "comparacion-colegios", rol=rol, indicador="promedio_libros")
    assert comparacion.status_code == esperado
    if esperado == 403:
        assert motivo(comparacion) == "sin_permiso"
