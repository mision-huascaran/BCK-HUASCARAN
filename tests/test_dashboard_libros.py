"""Dashboard D5: consolidado de libros leídos (CU034).

Hoy es miércoles 17/06/2026 (reloj fijo). El año escolar del calendario general va del
18/01/2026 (inicio del PA1) al 14/11/2026 (fin del PR4). Los grados no tienen día de clase
salvo que el test lo fije: entonces las semanas con clase habitual pasada sin tabla son
registros esperados que faltan. La clase de referencia de cada tabla es una habitual con
fila, así que su semana también es esperada.
"""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.evaluacion import LecturaSemanal, LecturaSemanalAlumno
from app.models.organizacion import AlumnoPertenencia, ColegioGrado, Docente
from app.services.dashboard.libros import ETIQUETA_PARCIAL
from tests.conftest import (
    a_las, clase_guardada, crear_alumno, crear_colegio, fijar_periodo, motivo, registrar_asistencia, seccion_de,
    semana_de,
)

HOY = date(2026, 6, 17)
LUNES = 1
LSN = {"lsn_titulo": "Libro A", "lsn_nota": 5, "lsn_maximo": 5}


@pytest.fixture(autouse=True)
def fecha_fija(reloj):
    reloj.fijar(a_las(HOY, 9))


# ── Preparación ─────────────────────────────────────────────────────────────────────

def _get(client, cabeceras, entorno, ruta="libros", rol="Supervisor", **params):
    if "id_colegios" not in params:
        params["todos_los_colegios"] = True
    params["id_anio_escolar"] = entorno.anio.id_anio_escolar
    return client.get(f"/dashboard/{ruta}", params=params, headers=cabeceras[rol])


def _ok(respuesta) -> dict:
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _libros(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, **params))


def _colegio_detalle(client, cabeceras, entorno, colegio="Andino", **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, f"libros/colegios/{entorno.colegios[colegio].id_colegio}", **params))


def _tabla(db, fecha, filas, estado="normal"):
    """Tabla del grupo y subprograma del primer alumno con la clase de esa fecha de
    referencia. `filas`: (alumno, asistencia o None, campos de la fila)."""
    lista = [alumno for alumno, _, _ in filas]
    clase = clase_guardada(db, lista[0], fecha, lista)
    for alumno, asistencia, _ in filas:
        if asistencia is not None:
            registrar_asistencia(db, alumno, fecha, asistencia, lista)
    semana = semana_de(db, fecha)
    ahora = tiempo.ahora_utc()
    firma = {"id_docente": db.exec(select(Docente)).first().id_docente, "fecha_registro": ahora, "creado_por": 1,
             "creado_en": ahora}
    base = lista[0]
    tabla = LecturaSemanal(
        id_colegio=base.id_colegio, id_grado=base.id_grado, id_seccion=base.id_seccion,
        id_programa=base.id_programa_actual, id_anio_escolar=semana.id_anio_escolar, mes=fecha.month,
        id_semana=semana.id_semana, id_clase_referencia=clase.id_clase, estado=estado,
        motivo_no_realizada="Sin sesión" if estado == "no_realizada" else None, **firma,
    )
    db.add(tabla)
    db.flush()
    for alumno, _, campos in filas:
        db.add(LecturaSemanalAlumno(id_lectura_semanal=tabla.id, id_alumno=alumno.id_alumno, **campos, **firma))
    db.commit()
    return tabla


def _leyo(db, alumno, fecha, lsn=False, lsl=None, asistencia="presente"):
    """Tabla de un solo alumno con lo que leyó esa semana."""
    return _tabla(db, fecha, [(alumno, asistencia, {**(LSN if lsn else {}), "cantidad_lsl": lsl})])


def _companeros(db, entorno, cuantos, alumno="ana"):
    base = entorno.alumnos[alumno]
    colegio = next(c for c in entorno.colegios.values() if c.id_colegio == base.id_colegio)
    nuevos = [
        crear_alumno(db, f"Compa{i}", f"Prueba{i}", colegio, base.id_grado, base.id_programa_actual, 1,
                     desde=entorno.anio.fecha_inicio)
        for i in range(cuantos)
    ]
    db.commit()
    return nuevos


def _colegio_de(datos, nombre):
    return next(c for c in datos["colegios"] if c["colegio"]["nombre"] == nombre)


def _mes(meses, anio, mes):
    return next(m for m in meses if (m["anio"], m["mes"]) == (anio, mes))


def _dia_clase(db, entorno, dia, colegio="Andino", grado="2.º"):
    fila = db.get(ColegioGrado, (entorno.colegios[colegio].id_colegio, entorno.grados[grado]))
    fila.dia_clase = dia
    db.add(fila)
    db.commit()


# ── Suma ────────────────────────────────────────────────────────────────────────────

def test_suma_lsn_y_lsl_con_relecturas(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    _leyo(db, ana, date(2026, 3, 9), lsn=True, lsl=1)
    _leyo(db, ana, date(2026, 3, 16), lsn=True, lsl=1)  # el mismo título, otra semana
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["total"], andino["estado"], andino["alumnos_incluidos"], andino["promedio"]) == (4, "completo", 1, 4.0)


def test_lsl_null_no_suma_ni_es_cero(client, cabeceras, db, entorno):
    _leyo(db, entorno.alumnos["ana"], date(2026, 3, 9), lsl=None)
    datos = _libros(client, cabeceras, entorno)
    assert (datos["resumen"]["total"], datos["resumen"]["estado"]) == (None, "sin_informacion")
    assert datos["colegios"] == []
    assert {(c["colegio"]["nombre"], c["motivo"]) for c in datos["colegios_sin_informacion"]} == {
        ("Colegio Andino", "sin_informacion"), ("Colegio Lejano", "sin_alumnos"),
    }


def test_lsl_cero_es_un_cero_confirmado_con_posicion(client, cabeceras, db, entorno):
    _leyo(db, entorno.alumnos["ana"], date(2026, 3, 9), lsl=0)
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["posicion"], andino["total"], andino["estado"], andino["promedio"]) == (1, 0, "completo", 0.0)


def test_no_realizada_aporta_cero_confirmado(client, cabeceras, db, entorno):
    _tabla(db, date(2026, 3, 9), [(entorno.alumnos["ana"], None, {})], estado="no_realizada")
    datos = _libros(client, cabeceras, entorno)
    andino = _colegio_de(datos, "Colegio Andino")
    assert (andino["total"], andino["estado"], andino["provisional"]) == (0, "completo", False)
    assert _mes(andino["meses"], 2026, 3) == {"anio": 2026, "mes": 3, "estado": "completo", "total": 0, "en_curso": False}


# ── Meses ───────────────────────────────────────────────────────────────────────────

def test_mes_de_la_clase_de_referencia_con_su_anio(client, cabeceras, db, entorno):
    tabla = _leyo(db, entorno.alumnos["ana"], date(2026, 3, 30), lsn=True, lsl=2)
    tabla.mes = 4  # el mes guardado en la tabla no manda
    db.add(tabla)
    db.commit()
    meses = _libros(client, cabeceras, entorno)["meses"]
    assert (_mes(meses, 2026, 3)["total"], _mes(meses, 2026, 4)["estado"]) == (3, "sin_informacion")


def test_calendario_que_cruza_dos_anios(client, cabeceras, db, entorno):
    fijar_periodo(db, entorno.calendario, "PA1", inicio=date(2025, 12, 1))
    _leyo(db, entorno.alumnos["ana"], date(2025, 12, 9), lsl=2)
    meses = _libros(client, cabeceras, entorno)["meses"]
    assert [(m["anio"], m["mes"]) for m in meses][:3] == [(2025, 12), (2026, 1), (2026, 2)]
    assert (meses[0]["estado"], meses[0]["total"]) == ("completo", 2)
    assert (meses[-1]["anio"], meses[-1]["mes"]) == (2026, 11)


def test_meses_futuros_y_en_curso(client, cabeceras, db, entorno):
    _leyo(db, entorno.alumnos["ana"], HOY - timedelta(days=2), lsl=1)
    meses = _libros(client, cabeceras, entorno)["meses"]
    assert _mes(meses, 2026, 6) == {"anio": 2026, "mes": 6, "estado": "completo", "total": 1, "en_curso": True}
    assert _mes(meses, 2026, 7) == {"anio": 2026, "mes": 7, "estado": "futuro", "total": None, "en_curso": False}
    assert _mes(meses, 2026, 5)["estado"] == "sin_informacion"


# ── Alumnos incluidos y promedio ────────────────────────────────────────────────────

def test_promedio_con_la_lista_historica(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    c1, c2, c3 = _companeros(db, entorno, 3)
    _tabla(db, date(2026, 3, 9), [
        (ana, "presente", {**LSN, "cantidad_lsl": 2}),
        (c1, "presente", {**LSN, "cantidad_lsl": 2}),
        (c2, "ausente", {}),
        (c3, "ausente", {}),
    ])
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["total"], andino["alumnos_incluidos"], andino["promedio"]) == (6, 4, 1.5)  # no 6 / 2 = 3


def test_semana_esperada_sin_tabla_incompleta_y_sus_alumnos_cuentan(client, cabeceras, db, entorno):
    _dia_clase(db, entorno, LUNES)
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _tabla(db, date(2026, 6, 8), [(ana, "presente", {"cantidad_lsl": 3}), (otro, "presente", {"cantidad_lsl": 1})])
    datos = _libros(client, cabeceras, entorno)
    andino = _colegio_de(datos, "Colegio Andino")
    assert (andino["estado"], andino["provisional"], andino["total"]) == ("incompleto", True, 4)
    assert (datos["resumen"]["estado"], datos["resumen"]["etiqueta"]) == ("incompleto", ETIQUETA_PARCIAL)
    # Junio: la semana del 8 tiene tabla y la del 1 falta (la del 15 todavía no vence).
    assert _mes(andino["meses"], 2026, 6)["estado"] == "incompleto"
    assert _mes(andino["meses"], 2026, 5)["estado"] == "sin_informacion"  # solo semanas faltantes
    assert andino["alumnos_incluidos"] == 2
    # Un alumno que solo está por pertenencia en las semanas sin tabla también cuenta.
    _companeros(db, entorno, 1)
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["alumnos_incluidos"], andino["total"]) == (3, 4)


def test_fila_pendiente_de_una_semana_vencida_es_incompleta(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _tabla(db, date(2026, 3, 9), [(ana, "presente", {"cantidad_lsl": 2}), (otro, None, {})])
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["estado"], andino["total"], andino["alumnos_incluidos"], andino["promedio"]) == (
        "incompleto", 2, 2, 1.0,
    )


def test_tabla_de_la_semana_en_curso_no_falta(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _tabla(db, HOY - timedelta(days=2), [(ana, "presente", {"cantidad_lsl": 2}), (otro, None, {})])
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["estado"], andino["total"]) == ("completo", 2)


def test_sin_ninguna_cantidad_sin_informacion(client, cabeceras, entorno):
    datos = _libros(client, cabeceras, entorno)
    assert datos["resumen"] == {"total": None, "estado": "sin_informacion", "provisional": False,
                                "alumnos_incluidos": 0, "promedio": None, "etiqueta": None}
    assert datos["colegios"] == []
    assert [c["motivo"] for c in datos["colegios_sin_informacion"]] == ["sin_alumnos", "sin_alumnos"]


# ── Ranking ─────────────────────────────────────────────────────────────────────────

def test_ranking_por_promedio_exacto_con_empates(client, cabeceras, db, entorno):
    bajo = crear_colegio(db, "Colegio Bajo", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    alumno_bajo = crear_alumno(db, "Beto", "Bajo", bajo, entorno.grados["2.º"], entorno.programas["Comprensión Lectora"],
                               1, desde=entorno.anio.fecha_inicio)
    db.commit()
    _leyo(db, entorno.alumnos["ana"], date(2026, 3, 9), lsl=2)
    _leyo(db, entorno.alumnos["mario"], date(2026, 3, 10), lsl=2)
    _leyo(db, alumno_bajo, date(2026, 3, 11), lsl=1)
    colegios = _libros(client, cabeceras, entorno)["colegios"]
    assert [(c["colegio"]["nombre"], c["posicion"], c["promedio"]) for c in colegios] == [
        ("Colegio Andino", 1, 2.0), ("Colegio Lejano", 1, 2.0), ("Colegio Bajo", 3, 1.0),
    ]


# ── Grados, cambios de grupo y detalle ──────────────────────────────────────────────

def _pasa_a_tercero(db, entorno, alumno, desde):
    andino = entorno.colegios["Andino"]
    tercero = entorno.grados["3.º"]
    seccion = seccion_de(db, andino, tercero)
    actual = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == alumno.id_alumno)).one()
    actual.hasta = desde
    db.add(actual)
    db.add(AlumnoPertenencia(id_alumno=alumno.id_alumno, id_colegio=andino.id_colegio, id_grado=tercero,
                             id_seccion=seccion.id_seccion, id_programa=alumno.id_programa_actual, desde=desde,
                             creado_por=1, creado_en=tiempo.ahora_utc()))
    alumno.id_grado, alumno.id_seccion = tercero, seccion.id_seccion
    db.add(alumno)
    db.commit()


def test_cambio_de_grado_promedio_del_colegio_y_de_sus_grados(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    _leyo(db, ana, date(2026, 3, 9), lsn=True, lsl=2)  # 2.º: 3 libros
    _pasa_a_tercero(db, entorno, ana, date(2026, 4, 1))
    _leyo(db, ana, date(2026, 4, 6), lsl=1)  # 3.º: 1 libro
    andino = _colegio_de(_libros(client, cabeceras, entorno), "Colegio Andino")
    assert (andino["total"], andino["alumnos_incluidos"], andino["promedio"]) == (4, 1, 4.0)  # no (3 + 1) / 2
    grados = _colegio_detalle(client, cabeceras, entorno)["grados"]
    assert [(g["grado"]["nombre"], g["posicion"], g["total"], g["alumnos_incluidos"], g["promedio"]) for g in grados] == [
        ("2.º", 1, 3, 1, 3.0), ("3.º", 2, 1, 1, 1.0),
    ]
    # Las lecturas de marzo se quedan en 2.º aunque hoy esté en 3.º.
    assert _mes(grados[0]["alumnos"][0]["meses"], 2026, 3)["total"] == 3
    assert _mes(grados[1]["alumnos"][0]["meses"], 2026, 3)["estado"] == "sin_informacion"


def test_detalle_del_colegio_con_alumnos_por_nombre(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    otro = _companeros(db, entorno, 1)[0]
    _tabla(db, date(2026, 3, 9), [(ana, "presente", {"cantidad_lsl": 1}), (otro, "presente", {**LSN, "cantidad_lsl": 1})])
    datos = _colegio_detalle(client, cabeceras, entorno)
    assert (datos["colegio"]["nombre"], datos["resumen"]["total"]) == ("Colegio Andino", 3)
    grado = datos["grados"][0]
    assert [(a["alumno"]["nombre"], a["total"]) for a in grado["alumnos"]] == [
        ("Compa0 Prueba0", 2), ("Ana Torres", 1),
    ]
    assert datos["grados_sin_informacion"] == []


def test_detalle_grado_sin_informacion_va_aparte(client, cabeceras, db, entorno):
    _leyo(db, entorno.alumnos["ana"], date(2026, 3, 9), lsl=1)
    _leyo(db, entorno.alumnos["luis"], date(2026, 3, 10), lsl=None)
    datos = _colegio_detalle(client, cabeceras, entorno)
    assert [g["grado"]["nombre"] for g in datos["grados"]] == ["2.º"]
    assert [(g["grado"]["nombre"], g["posicion"], g["promedio"], g["total"]) for g in datos["grados_sin_informacion"]] == [
        ("1.º", None, None, None),
    ]


def test_detalle_de_un_colegio_fuera_de_la_seleccion(client, cabeceras, entorno):
    respuesta = _get(client, cabeceras, entorno, f"libros/colegios/{entorno.colegios['Lejano'].id_colegio}",
                     id_colegios=[entorno.colegios["Andino"].id_colegio])
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0]["campo"] == "id_colegio"


# ── Desglose ────────────────────────────────────────────────────────────────────────

def test_desglose_del_mes_de_un_alumno(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    primera = _leyo(db, ana, date(2026, 3, 9), lsn=True, lsl=1)
    _leyo(db, ana, date(2026, 3, 16), lsl=None)
    _leyo(db, ana, date(2026, 4, 6), lsl=5)
    datos = _ok(_get(client, cabeceras, entorno, "libros/desglose", id_alumno=ana.id_alumno, anio=2026, mes=3))
    assert (datos["alumno"], datos["total"]) == ({"id": ana.id_alumno, "nombre": "Ana Torres"}, 2)
    assert [(r["clase_referencia"]["fecha"], r["lsn"], r["lsl"], r["total"]) for r in datos["registros"]] == [
        ("2026-03-09", 1, 1, 2), ("2026-03-16", 0, None, None),
    ]
    registro = datos["registros"][0]
    assert (registro["id_lectura_semanal"], registro["subprograma"]["nombre"], registro["estado_tabla"]) == (
        primera.id, "Comprensión Lectora", "normal",
    )
    assert (registro["colegio"]["nombre"], registro["grado"]["nombre"]) == ("Colegio Andino", "2.º")


def test_desglose_alumno_de_otro_colegio(client, cabeceras, db, entorno):
    mario = entorno.alumnos["mario"]
    respuesta = _get(client, cabeceras, entorno, "libros/desglose", id_alumno=mario.id_alumno, anio=2026, mes=3,
                     id_colegios=[entorno.colegios["Andino"].id_colegio])
    assert (respuesta.status_code, respuesta.json()["errores"][0]["campo"]) == (422, "id_alumno")
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()
    respuesta = _get(client, cabeceras, entorno, "libros/desglose", id_alumno=mario.id_alumno, anio=2026, mes=3)
    assert (respuesta.status_code, motivo(respuesta)) == (403, "colegio_fuera_de_alcance")


# ── Roles ───────────────────────────────────────────────────────────────────────────

def test_directivo_ve_el_consolidado_sin_nombres(client, cabeceras, db, entorno):
    _leyo(db, entorno.alumnos["ana"], date(2026, 3, 9), lsl=1)
    respuesta = _get(client, cabeceras, entorno, rol="Directivo")
    assert respuesta.status_code == 200
    assert "Torres" not in respuesta.text
    detalle = _get(client, cabeceras, entorno, f"libros/colegios/{entorno.colegios['Andino'].id_colegio}",
                   rol="Directivo")
    desglose = _get(client, cabeceras, entorno, "libros/desglose", rol="Directivo",
                    id_alumno=entorno.alumnos["ana"].id_alumno, anio=2026, mes=3)
    assert (detalle.status_code, desglose.status_code) == (403, 403)


@pytest.mark.parametrize("ruta", ["libros", "libros/desglose"])
def test_docente_no_tiene_libros(client, cabeceras, entorno, ruta):
    respuesta = _get(client, cabeceras, entorno, ruta, rol="Docente", id_alumno=1, anio=2026, mes=3)
    assert respuesta.status_code == 403


def test_libros_es_anual_no_admite_pa(client, cabeceras, entorno):
    respuesta = _get(client, cabeceras, entorno, id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico)
    assert respuesta.status_code == 422


def test_rutas_provisionales_retiradas(client, cabeceras):
    assert client.get("/dashboard/lectura", headers=cabeceras["Supervisor"]).status_code == 404
