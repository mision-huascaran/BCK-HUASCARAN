"""Dashboard D3: asistencia de alumnos (CU033 paso 8, CU035 paso 7).

Hoy es miércoles 17/06/2026 (reloj fijo). El PA2 del calendario general va del 18/05 al
17/07, así que empieza a mitad de mayo. Los grados no tienen día de clase salvo que el
test lo fije: sin él no hay habituales calculadas, solo las clases con fila.
"""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.models.organizacion import AlumnoPertenencia, ColegioGrado
from app.services.dashboard.asistencia import MENSAJE_SIN_INFORMACION
from app.services.programacion import fechas_del_dia
from tests.conftest import (
    a_las, agregar_no_lectivo, clase_guardada, crear_alumno, crear_particular, motivo, rangos_en_receso,
    registrar_asistencia, seccion_de,
)

HOY = date(2026, 6, 17)
LUNES, MIERCOLES = 1, 3


@pytest.fixture(autouse=True)
def fecha_fija(reloj):
    reloj.fijar(a_las(HOY, 9))


# ── Preparación ─────────────────────────────────────────────────────────────────────

def _get(client, cabeceras, entorno, ruta="asistencia", periodo="vigente", rol="Supervisor", **params):
    if "id_colegios" not in params:
        params["todos_los_colegios"] = True
    params.update(id_anio_escolar=entorno.anio.id_anio_escolar,
                  id_periodo_academico=entorno.periodos[periodo].id_periodo_academico)
    return client.get(f"/dashboard/{ruta}", params=params, headers=cabeceras[rol])


def _ok(respuesta) -> dict:
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _asistencia(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, **params))


def _detalle(client, cabeceras, entorno, colegio="Andino", **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, "asistencia/clases",
                    id_colegio=entorno.colegios[colegio].id_colegio, **params))


def _dia_clase(db, entorno, dia, colegio="Andino", grado="2.º"):
    fila = db.get(ColegioGrado, (entorno.colegios[colegio].id_colegio, entorno.grados[grado]))
    fila.dia_clase = dia
    db.add(fila)
    db.commit()


def _companeros(db, entorno, cuantos, alumno="ana"):
    """Alumnos en el grupo de `alumno` desde el inicio del año."""
    base = entorno.alumnos[alumno]
    colegio = next(c for c in entorno.colegios.values() if c.id_colegio == base.id_colegio)
    nuevos = [
        crear_alumno(db, f"Compa{i}", "Prueba", colegio, base.id_grado, base.id_programa_actual, 1,
                     desde=entorno.anio.fecha_inicio)
        for i in range(cuantos)
    ]
    db.commit()
    return nuevos


def _clase(db, lista, fecha, estados):
    """Clase del grupo del primer alumno con esa lista; `estados` en el orden de la lista
    (None = sin marcar)."""
    clase_guardada(db, lista[0], fecha, lista)
    for alumno, estado in zip(lista, estados):
        if estado is not None:
            registrar_asistencia(db, alumno, fecha, estado, lista)
    db.commit()


def _lunes(desde, hasta):
    return fechas_del_dia(LUNES, desde, hasta)


def _resumen(datos):
    return datos["resumen"]


# ── Fórmula ─────────────────────────────────────────────────────────────────────────

def test_formula_sobre_el_conjunto_no_promedio(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    grupo = [ana, *_companeros(db, entorno, 3)]
    _clase(db, grupo, HOY - timedelta(days=9), ["presente", "presente", "presente", "ausente"])  # 75 %
    _clase(db, [ana], HOY - timedelta(days=2), ["ausente"])  # 0 %
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["presentes"], resumen["ausentes"]) == (3, 2)
    assert resumen["porcentaje"] == 60.0  # 3 / 5, no (75 + 0) / 2
    assert resumen["porcentaje"] != (75.0 + 0.0) / 2
    assert (resumen["clases_dictadas"], resumen["asistencias_esperadas"], resumen["asistencias_completadas"]) == (2, 5, 5)


def test_tardanza_cuenta_y_sin_marcar_no_entra_al_denominador(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    grupo = [ana, *_companeros(db, entorno, 2)]
    _clase(db, grupo, HOY - timedelta(days=9), ["presente", "tardanza", None])
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["presentes"], resumen["tardanzas"], resumen["ausentes"], resumen["sin_marcar"]) == (1, 1, 0, 1)
    assert resumen["porcentaje"] == 100.0
    assert (resumen["asistencias_esperadas"], resumen["asistencias_completadas"]) == (3, 2)


def test_clase_incompleta_vuelve_provisional_el_resumen(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    grupo = [ana, *_companeros(db, entorno, 1)]
    _clase(db, grupo, HOY - timedelta(days=9), ["presente", None])
    _clase(db, grupo, HOY - timedelta(days=2), ["presente", "ausente"])
    datos = _asistencia(client, cabeceras, entorno)
    assert (_resumen(datos)["provisional"], _resumen(datos)["clases_incompletas"]) == (True, 1)
    andino = next(c for c in datos["por_colegio"] if c["colegio"]["nombre"] == "Colegio Andino")
    assert andino["provisional"] is True
    clases = _detalle(client, cabeceras, entorno)["grupos"][0]["clases"]
    assert [(c["estado"], c["sin_marcar"], c["lista_historica"]) for c in clases] == [
        ("registro_incompleto", 1, 2), ("completa", 0, 2),
    ]
    assert [c["porcentaje"] for c in clases] == [100.0, 50.0]


def test_sin_estados_sin_informacion_y_no_cero(client, cabeceras, entorno):
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["porcentaje"], resumen["mensaje"]) == (None, MENSAJE_SIN_INFORMACION)
    assert resumen["clases_dictadas"] == 0


def test_solo_ausentes_es_un_cero_confirmado(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    grupo = [ana, *_companeros(db, entorno, 1)]
    _clase(db, grupo, HOY - timedelta(days=9), ["ausente", None])
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["porcentaje"], resumen["mensaje"]) == (0.0, None)


# ── Clases esperadas ────────────────────────────────────────────────────────────────

def test_habitual_sin_fila_y_sin_asistencias_es_sin_registro(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    esperadas = _lunes(entorno.inicio("vigente"), HOY - timedelta(days=1))
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["clases_sin_registro"], resumen["clases_dictadas"]) == (len(esperadas), 0)
    assert resumen["provisional"] is False
    sin_registro = _detalle(client, cabeceras, entorno)["grupos"][0]["clases_sin_registro"]
    assert [c["fecha"] for c in sin_registro] == [f.isoformat() for f in esperadas]
    assert all(c["id_clase"] is None and c["tipo"] == "habitual" for c in sin_registro)


def test_la_clase_de_hoy_sin_asistencias_no_es_sin_registro(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, MIERCOLES)
    pasadas = fechas_del_dia(MIERCOLES, entorno.inicio("vigente"), HOY - timedelta(days=1))
    sin_registro = _detalle(client, cabeceras, entorno)["grupos"][0]["clases_sin_registro"]
    assert HOY.isoformat() not in [c["fecha"] for c in sin_registro]
    assert len(sin_registro) == len(pasadas)


def test_la_clase_de_hoy_con_asistencias_es_dictada(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, MIERCOLES)
    _clase(db, [entorno.alumnos["ana"]], HOY, ["presente"])
    assert _resumen(_asistencia(client, cabeceras, entorno))["clases_dictadas"] == 1


def test_adicional_solo_cuenta_con_asistencias(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    clase_guardada(db, ana, HOY - timedelta(days=8), tipo="adicional")
    db.commit()
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["clases_dictadas"], resumen["clases_sin_registro"]) == (0, 0)
    registrar_asistencia(db, ana, HOY - timedelta(days=8), "presente")
    assert _resumen(_asistencia(client, cabeceras, entorno))["clases_dictadas"] == 1


def test_reprogramada_cuenta_una_vez_en_su_fecha_vigente(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    ana = entorno.alumnos["ana"]
    lunes = _lunes(entorno.inicio("vigente"), HOY - timedelta(days=1))
    movido, martes = lunes[-2], lunes[-2] + timedelta(days=1)
    clase_guardada(db, ana, martes, fecha_original=movido)
    registrar_asistencia(db, ana, martes, "presente")
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert (resumen["clases_dictadas"], resumen["clases_sin_registro"]) == (1, len(lunes) - 1)
    grupo = _detalle(client, cabeceras, entorno)["grupos"][0]
    assert [(c["fecha"], c["reprogramada"]) for c in grupo["clases"]] == [(martes.isoformat(), True)]
    assert movido.isoformat() not in [c["fecha"] for c in grupo["clases_sin_registro"]]


def test_reprogramada_sin_asistencias_es_sin_registro_en_su_fecha_nueva(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    ana = entorno.alumnos["ana"]
    movido = _lunes(entorno.inicio("vigente"), HOY - timedelta(days=1))[0]
    martes = movido + timedelta(days=1)
    clase = clase_guardada(db, ana, martes, fecha_original=movido)
    db.commit()
    sin_registro = _detalle(client, cabeceras, entorno)["grupos"][0]["clases_sin_registro"][0]
    assert sin_registro == {"id_clase": clase.id_clase, "fecha": martes.isoformat(), "tipo": "habitual",
                            "reprogramada": True, "fecha_original": movido.isoformat()}


def test_dia_no_lectivo_y_receso_no_generan_clases(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    lunes = _lunes(entorno.inicio("vigente"), HOY - timedelta(days=1))
    agregar_no_lectivo(db, entorno.calendario, lunes[1])
    resumen = _resumen(_asistencia(client, cabeceras, entorno))
    assert resumen["clases_sin_registro"] == len(lunes) - 1
    # PA1 terminado: solo sus lunes, ninguno del receso (PR1) que le sigue.
    pasado = _resumen(_asistencia(client, cabeceras, entorno, periodo="pasado"))
    assert pasado["clases_sin_registro"] == len(_lunes(entorno.inicio("pasado"), entorno.fin("pasado")))


def test_lista_historica_no_cambia_si_el_alumno_cambia_de_grupo(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    fecha = HOY - timedelta(days=9)
    _clase(db, [ana, otro], fecha, ["presente", "presente"])
    pertenencia = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == otro.id_alumno)).one()
    pertenencia.hasta = fecha + timedelta(days=1)
    db.add(pertenencia)
    db.commit()
    clase = _detalle(client, cabeceras, entorno)["grupos"][0]["clases"][0]
    assert (clase["lista_historica"], clase["presentes"]) == (2, 2)


# ── Serie mensual ───────────────────────────────────────────────────────────────────

def test_mensual_meses_del_periodo_con_estado_y_anio(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    _clase(db, [ana], date(2026, 5, 25), ["presente"])
    _clase(db, [ana], date(2026, 5, 11), ["ausente"])  # en el PR1: fuera del PA2
    mensual = _asistencia(client, cabeceras, entorno)["mensual"]
    assert entorno.inicio("vigente") == date(2026, 5, 18)
    assert [(m["anio"], m["mes"], m["estado"], m["porcentaje"]) for m in mensual] == [
        (2026, 5, "con_datos", 100.0), (2026, 6, "en_curso", None), (2026, 7, "futuro", None),
    ]
    assert mensual[2] == {"anio": 2026, "mes": 7, "estado": "futuro", "porcentaje": None, "presentes": None,
                          "tardanzas": None, "ausentes": None, "sin_marcar": None, "provisional": None}
    assert (mensual[1]["presentes"], mensual[1]["provisional"]) == (0, False)


def test_mensual_mes_pasado_sin_estados_es_sin_informacion(client, cabeceras, db, entorno):
    _clase(db, [entorno.alumnos["ana"]], HOY - timedelta(days=1), ["tardanza"])
    mensual = _asistencia(client, cabeceras, entorno)["mensual"]
    assert [(m["mes"], m["estado"], m["porcentaje"]) for m in mensual] == [
        (5, "sin_informacion", None), (6, "en_curso", 100.0), (7, "futuro", None),
    ]


# ── Por colegio ─────────────────────────────────────────────────────────────────────

def test_por_colegio_con_calendarios_distintos(client, cabeceras, db, entorno):
    fin_lejano = HOY - timedelta(days=10)
    crear_particular(db, entorno, rangos_en_receso(HOY, fin_pa2=fin_lejano))
    mario, ana = entorno.alumnos["mario"], entorno.alumnos["ana"]
    _clase(db, [mario], fin_lejano - timedelta(days=2), ["ausente"])
    _clase(db, [mario], HOY - timedelta(days=5), ["presente"])  # en el PR2 de Lejano: no cuenta
    _clase(db, [ana], HOY - timedelta(days=5), ["presente"])
    datos = _asistencia(client, cabeceras, entorno)
    assert [(c["colegio"]["nombre"], c["porcentaje"], c["clases_dictadas"]) for c in datos["por_colegio"]] == [
        ("Colegio Andino", 100.0, 1), ("Colegio Lejano", 0.0, 1),
    ]
    assert (_resumen(datos)["porcentaje"], _resumen(datos)["clases_dictadas"]) == (50.0, 2)


def test_colegios_con_el_pa_sin_iniciar(client, cabeceras, entorno):
    datos = _asistencia(client, cabeceras, entorno, periodo="futuro")
    assert [(c["colegio"]["nombre"], c["motivo"]) for c in datos["colegios_sin_informacion"]] == [
        ("Colegio Andino", "periodo_no_iniciado"), ("Colegio Lejano", "periodo_no_iniciado"),
    ]
    assert [c["porcentaje"] for c in datos["por_colegio"]] == [None, None]
    assert _asistencia(client, cabeceras, entorno)["colegios_sin_informacion"] == []


# ── Detalle por clase ───────────────────────────────────────────────────────────────

def test_detalle_por_grado_y_seccion(client, cabeceras, db, entorno):
    luis, ana = entorno.alumnos["luis"], entorno.alumnos["ana"]
    _clase(db, [ana], HOY - timedelta(days=2), ["presente"])
    _clase(db, [ana], HOY - timedelta(days=9), ["ausente"])
    _clase(db, [luis], HOY - timedelta(days=3), ["tardanza"])
    grupos = _detalle(client, cabeceras, entorno)["grupos"]
    assert [(g["grado"]["nombre"], g["seccion"]["nombre"]) for g in grupos] == [
        ("1.º", seccion_de(db, entorno.colegios["Andino"], luis.id_grado).nombre),
        ("2.º", seccion_de(db, entorno.colegios["Andino"], ana.id_grado).nombre),
    ]
    assert [c["fecha"] for c in grupos[1]["clases"]] == [
        (HOY - timedelta(days=9)).isoformat(), (HOY - timedelta(days=2)).isoformat(),
    ]
    assert grupos[1]["resumen"]["porcentaje"] == 50.0
    solo_segundo = _detalle(client, cabeceras, entorno, id_grado=ana.id_grado)["grupos"]
    assert [g["grado"]["nombre"] for g in solo_segundo] == ["2.º"]


def test_detalle_colegio_fuera_de_la_seleccion(client, cabeceras, entorno):
    respuesta = _get(client, cabeceras, entorno, "asistencia/clases",
                     id_colegios=[entorno.colegios["Andino"].id_colegio],
                     id_colegio=entorno.colegios["Lejano"].id_colegio)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0]["campo"] == "id_colegio"


def test_detalle_colegio_inactivo_fuera_del_alcance(client, cabeceras, db, entorno):
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()
    respuesta = _get(client, cabeceras, entorno, "asistencia/clases", id_colegio=lejano.id_colegio)
    assert (respuesta.status_code, motivo(respuesta)) == (403, "colegio_fuera_de_alcance")


def test_detalle_seccion_de_otro_colegio(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otra = seccion_de(db, entorno.colegios["Lejano"], ana.id_grado)
    respuesta = _get(client, cabeceras, entorno, "asistencia/clases",
                     id_colegio=entorno.colegios["Andino"].id_colegio, id_seccion=otra.id_seccion)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0]["campo"] == "id_seccion"


# ── Roles ───────────────────────────────────────────────────────────────────────────

def test_directivo_ve_el_resumen_y_no_el_detalle(client, cabeceras, entorno):
    assert _get(client, cabeceras, entorno, rol="Directivo").status_code == 200
    detalle = _get(client, cabeceras, entorno, "asistencia/clases", rol="Directivo",
                   id_colegio=entorno.colegios["Andino"].id_colegio)
    assert (detalle.status_code, motivo(detalle)) == (403, "sin_permiso")


@pytest.mark.parametrize("ruta", ["asistencia", "asistencia/clases"])
def test_docente_no_tiene_asistencia_del_dashboard(client, cabeceras, entorno, ruta):
    respuesta = _get(client, cabeceras, entorno, ruta, rol="Docente", id_colegio=entorno.colegios["Andino"].id_colegio)
    assert respuesta.status_code == 403


def test_sin_token(client, entorno):
    assert client.get("/dashboard/asistencia", params={"id_anio_escolar": entorno.anio.id_anio_escolar}).status_code == 401
