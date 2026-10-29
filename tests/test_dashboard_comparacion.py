"""Dashboard D4: comparación de resultados de aprendizaje (CU033, paso 7).

El entorno: hoy en el PA2 ("vigente"). En Comprensión Lectora están Ana (Andino, 2.º) y
Mario (Lejano, 3.º); Luis (Andino, 1.º) está en Alfabetización. Cada test agrega alumnos al
grupo que necesita y les registra la decisión del Registro de Vuelo del PA2 con una letra
que cae en el nivel del programa buscado según la escala de su grado.
"""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.evaluacion import EscalaNivelDescriptivo, NivelRazkids
from app.models.organizacion import ColegioPrograma
from tests.conftest import crear_alumno, crear_colegio, decision_de_vuelo, motivo, rubrica_mensual, seccion_de

ALFA, CL = "Alfabetización", "Comprensión Lectora"
INICIO, EN_PROCESO, LOGRADO, AVANZADO = "Inicio", "En proceso", "Logrado", "Avanzado"

# ── Preparación ─────────────────────────────────────────────────────────────────────


def _get(client, cabeceras, entorno, programa=CL, rol="Supervisor", **params):
    if "id_colegios" not in params:
        params["todos_los_colegios"] = True
    if "id_grados" not in params and "todos_los_grados" not in params:
        params["todos_los_grados"] = True
    params.update(id_anio_escolar=entorno.anio.id_anio_escolar,
                  id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico)
    params.setdefault("id_programa", entorno.programas.get(programa, programa))
    return client.get("/dashboard/comparacion-resultados", params=params, headers=cabeceras[rol])


def _comparar(client, cabeceras, entorno, **params) -> dict:
    respuesta = _get(client, cabeceras, entorno, **params)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _colegio(entorno, nombre):
    return entorno.colegios[nombre]


def _alumnos(db, entorno, colegio, grado, cuantos, programa=CL):
    """Alumnos nuevos del grupo desde el inicio del año."""
    nuevos = [
        crear_alumno(db, f"{colegio.nombre[8:]}{grado}{i}", "Prueba", colegio, entorno.grados[grado],
                     entorno.programas[programa], 1, desde=entorno.anio.fecha_inicio)
        for i in range(cuantos)
    ]
    db.commit()
    return nuevos


def _letra_del_nivel(db, id_grado, nivel) -> int:
    """La primera letra que cae en ese nivel del programa en la escala del grado."""
    return db.exec(select(EscalaNivelDescriptivo.id_nivel_rk_desde).where(
        EscalaNivelDescriptivo.id_grado == id_grado, EscalaNivelDescriptivo.nivel == nivel,
    )).one()


def _evaluar(db, entorno, alumnos, niveles):
    """Decisión del PA2 por alumno con el nivel del programa dado (None = pendiente)."""
    for alumno, nivel in zip(alumnos, niveles):
        if nivel is not None:
            decision_de_vuelo(db, alumno, entorno.periodos["vigente"], _letra_del_nivel(db, alumno.id_grado, nivel))


def _fila(ranking, colegio, grado=None):
    return next(f for f in ranking if f["colegio"]["nombre"] == colegio and (grado is None or f["grado"]["nombre"] == grado))


def _resumen(fila) -> tuple:
    return fila["posicion"], fila["indice"], fila["evaluados"], fila["pendientes"]


def _quitar_programa(db, entorno, colegio, programa=CL):
    db.delete(db.get(ColegioPrograma, (_colegio(entorno, colegio).id_colegio, entorno.programas[programa])))
    db.commit()


# ── Índice ──────────────────────────────────────────────────────────────────────────

def test_indice_exacto_a_mano(client, cabeceras, db, entorno):
    andino = _colegio(entorno, "Andino")
    grupo = [entorno.alumnos["ana"], *_alumnos(db, entorno, andino, "2.º", 3)]
    _evaluar(db, entorno, grupo, [INICIO, INICIO, LOGRADO, AVANZADO])
    datos = _comparar(client, cabeceras, entorno, id_colegios=[andino.id_colegio])
    fila = datos["ranking"][0]
    assert _resumen(fila) == (1, 2.25, 4, 0)  # (2·1 + 3 + 4) / 4
    assert (fila["provisional"], fila["evaluacion_incompleta"], fila["grado"]) == (False, False, None)
    assert [(n["codigo"], n["alumnos"], n["porcentaje"]) for n in fila["distribucion"]] == [
        (INICIO, 2, 50.0), (EN_PROCESO, 0, 0.0), (LOGRADO, 1, 25.0), (AVANZADO, 1, 25.0),
    ]
    assert datos["escala"] == [
        {"codigo": n, "nombre": n, "valor_indice": v}
        for v, n in enumerate((INICIO, EN_PROCESO, LOGRADO, AVANZADO), start=1)
    ]
    assert (datos["vista"], datos["resultado"], datos["subprograma"]["nombre"]) == ("resumen_colegio", "global", CL)


def test_resumen_por_colegio_no_promedia_los_grados(client, cabeceras, db, entorno):
    andino = _colegio(entorno, "Andino")
    _evaluar(db, entorno, [entorno.alumnos["ana"]], [AVANZADO])  # 2.º: 4
    tercero = _alumnos(db, entorno, andino, "3.º", 3)
    _evaluar(db, entorno, tercero, [INICIO] * 3)  # 3.º: 1, 1, 1
    resumen = _comparar(client, cabeceras, entorno, id_colegios=[andino.id_colegio])
    assert resumen["ranking"][0]["indice"] == 1.75  # 7 / 4, no (4 + 1) / 2 = 2.5
    detalle = _comparar(client, cabeceras, entorno, id_colegios=[andino.id_colegio], vista="detalle_grado")
    assert [(f["grado"]["nombre"], f["posicion"], f["indice"]) for f in detalle["ranking"]] == [
        ("2.º", 1, 4.0), ("3.º", 2, 1.0),
    ]


def test_orden_por_el_valor_exacto_aunque_redondee_igual(client, cabeceras, db, entorno):
    # Lejano: 8 Inicio + 5 En proceso = 18 / 13 = 1,3846. Andino: 5 + 3 = 11 / 8 = 1,375.
    # Los dos se muestran 1,38; Lejano va primero aunque alfabéticamente va después.
    lejano = [entorno.alumnos["mario"], *_alumnos(db, entorno, _colegio(entorno, "Lejano"), "3.º", 12)]
    _evaluar(db, entorno, lejano, [INICIO] * 8 + [EN_PROCESO] * 5)
    andino = [entorno.alumnos["ana"], *_alumnos(db, entorno, _colegio(entorno, "Andino"), "2.º", 7)]
    _evaluar(db, entorno, andino, [INICIO] * 5 + [EN_PROCESO] * 3)
    ranking = _comparar(client, cabeceras, entorno)["ranking"]
    assert [(f["colegio"]["nombre"], f["posicion"], f["indice"]) for f in ranking] == [
        ("Colegio Lejano", 1, 1.38), ("Colegio Andino", 2, 1.38),
    ]


def test_empate_exacto_comparte_posicion_y_salta(client, cabeceras, db, entorno):
    bajo = crear_colegio(db, "Colegio Bajo", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    db.commit()
    _evaluar(db, entorno, [entorno.alumnos["ana"], entorno.alumnos["mario"]], [LOGRADO, LOGRADO])
    _evaluar(db, entorno, _alumnos(db, entorno, bajo, "2.º", 1), [INICIO])
    ranking = _comparar(client, cabeceras, entorno)["ranking"]
    assert [(f["colegio"]["nombre"], f["posicion"], f["indice"]) for f in ranking] == [
        ("Colegio Andino", 1, 3.0), ("Colegio Lejano", 1, 3.0), ("Colegio Bajo", 3, 1.0),
    ]


def test_pendientes_fuera_del_divisor_y_posicion_provisional(client, cabeceras, db, entorno):
    andino = _colegio(entorno, "Andino")
    grupo = [entorno.alumnos["ana"], *_alumnos(db, entorno, andino, "2.º", 2)]
    _evaluar(db, entorno, grupo, [LOGRADO, AVANZADO, None])
    fila = _comparar(client, cabeceras, entorno, id_colegios=[andino.id_colegio])["ranking"][0]
    assert _resumen(fila) == (1, 3.5, 2, 1)  # (3 + 4) / 2: el pendiente no vale 0
    assert (fila["total_a_evaluar"], fila["evaluacion_incompleta"], fila["provisional"]) == (3, True, True)
    assert [n["porcentaje"] for n in fila["distribucion"]] == [0.0, 0.0, 50.0, 50.0]


def test_grupo_sin_evaluaciones_va_aparte(client, cabeceras, db, entorno):
    _evaluar(db, entorno, [entorno.alumnos["ana"]], [LOGRADO])
    datos = _comparar(client, cabeceras, entorno)
    assert [f["colegio"]["nombre"] for f in datos["ranking"]] == ["Colegio Andino"]
    lejano = _colegio(entorno, "Lejano")
    assert datos["sin_evaluaciones"] == [{
        "colegio": {"id": lejano.id_colegio, "nombre": lejano.nombre}, "grado": None,
        "total_a_evaluar": 1, "evaluados": 0, "pendientes": 1,
    }]


def test_subprograma_no_configurado_es_no_aplica(client, cabeceras, db, entorno):
    _quitar_programa(db, entorno, "Lejano")
    _evaluar(db, entorno, [entorno.alumnos["ana"], entorno.alumnos["mario"]], [LOGRADO, LOGRADO])
    datos = _comparar(client, cabeceras, entorno)
    assert [f["colegio"]["nombre"] for f in datos["ranking"]] == ["Colegio Andino"]
    assert (datos["sin_evaluaciones"], [g["colegio"]["nombre"] for g in datos["no_aplica"]]) == ([], ["Colegio Lejano"])
    assert datos["no_aplica"][0]["grado"] is None
    detalle = _comparar(client, cabeceras, entorno, vista="detalle_grado", id_grados=[entorno.grados["3.º"],
                                                                                        entorno.grados["4.º"]])
    assert [(g["colegio"]["nombre"], g["grado"]["nombre"]) for g in detalle["no_aplica"]] == [
        ("Colegio Lejano", "3.º"), ("Colegio Lejano", "4.º"),
    ]


def test_grupo_configurado_sin_alumnos_no_aparece(client, cabeceras, db, entorno):
    bajo = crear_colegio(db, "Colegio Bajo", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    db.commit()
    datos = _comparar(client, cabeceras, entorno, id_colegios=[bajo.id_colegio])
    assert (datos["ranking"], datos["sin_evaluaciones"], datos["no_aplica"]) == ([], [], [])


# ── Detalle por grado ───────────────────────────────────────────────────────────────

def test_detalle_por_grado_indice_propio_y_desempate(client, cabeceras, db, entorno):
    andino = _colegio(entorno, "Andino")
    tercero = _alumnos(db, entorno, andino, "3.º", 2)
    _evaluar(db, entorno, [entorno.alumnos["ana"], entorno.alumnos["mario"], *tercero],
             [LOGRADO, LOGRADO, AVANZADO, EN_PROCESO])
    ranking = _comparar(client, cabeceras, entorno, vista="detalle_grado")["ranking"]
    # Los tres con índice 3: por colegio y, dentro de Andino, por grado.
    assert [(f["colegio"]["nombre"], f["grado"]["nombre"], f["posicion"], f["indice"]) for f in ranking] == [
        ("Colegio Andino", "2.º", 1, 3.0), ("Colegio Andino", "3.º", 1, 3.0), ("Colegio Lejano", "3.º", 1, 3.0),
    ]


# ── Grados ──────────────────────────────────────────────────────────────────────────

def test_solo_cuentan_los_grados_elegidos(client, cabeceras, db, entorno):
    _evaluar(db, entorno, [entorno.alumnos["ana"], entorno.alumnos["mario"]], [AVANZADO, INICIO])
    datos = _comparar(client, cabeceras, entorno, id_grados=[entorno.grados["3.º"]])
    assert [(f["colegio"]["nombre"], f["indice"]) for f in datos["ranking"]] == [("Colegio Lejano", 1.0)]
    assert datos["grados"] == [{"id": entorno.grados["3.º"], "nombre": "3.º"}]


@pytest.mark.parametrize("grados, mensaje", [
    ({"todos_los_grados": False}, "Selecciona al menos un grado."),
    ({"todos_los_grados": True, "id_grados": [1]}, "Elige todos los grados o una lista de grados, no ambas cosas."),
    ({"id_grados": [1, 1]}, "No repitas grados en la selección."),
    ({"id_grados": [999]}, "Uno de los grados seleccionados no existe."),
])
def test_seleccion_de_grados_invalida(client, cabeceras, entorno, grados, mensaje):
    respuesta = _get(client, cabeceras, entorno, **grados)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"] == [{"campo": "id_grados", "mensaje": mensaje}]


def test_subprograma_inexistente(client, cabeceras, entorno):
    respuesta = _get(client, cabeceras, entorno, programa=999)
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0]["campo"] == "id_programa"


# ── Otras vistas ────────────────────────────────────────────────────────────────────

def test_dimension_sin_indice_en_orden_alfabetico(client, cabeceras, db, entorno):
    vigente = entorno.periodos["vigente"]
    rubrica_mensual(db, entorno.alumnos["mario"], vigente, 6, fluidez=4, comprension=4)
    rubrica_mensual(db, entorno.alumnos["ana"], vigente, 6, fluidez=1, comprension=2)
    datos = _comparar(client, cabeceras, entorno, resultado="fluidez")
    assert [(f["colegio"]["nombre"], f["posicion"], f["indice"], f["provisional"]) for f in datos["ranking"]] == [
        ("Colegio Andino", None, None, None), ("Colegio Lejano", None, None, None),
    ]
    assert [(n["nombre"], n["valor_indice"]) for n in datos["escala"]] == [
        ("Inicio", None), ("En proceso", None), ("Logrado", None), ("Avanzado", None),
    ]
    assert [(n["codigo"], n["alumnos"]) for n in datos["ranking"][1]["distribucion"] if n["alumnos"]] == [("Avanzado", 1)]
    comprension = _comparar(client, cabeceras, entorno, resultado="comprension")
    assert [(n["codigo"], n["alumnos"]) for n in comprension["ranking"][0]["distribucion"] if n["alumnos"]] == [
        ("En proceso", 1),
    ]


def test_fluidez_de_alfabetizacion_incluye_pre_inicio(client, cabeceras, db, entorno):
    rubrica_mensual(db, entorno.alumnos["luis"], entorno.periodos["vigente"], 6, fluidez=0, comprension=1)
    datos = _comparar(client, cabeceras, entorno, programa=ALFA, resultado="fluidez")
    assert [n["nombre"] for n in datos["escala"]] == ["Pre-inicio", "Inicio", "En proceso", "Logrado", "Avanzado"]
    assert datos["ranking"][0]["distribucion"][0] == {"codigo": "Pre-inicio", "alumnos": 1, "porcentaje": 100.0}


def test_nivel_alfabetico_con_toda_la_escala(client, cabeceras, db, entorno):
    letras = list(db.exec(select(NivelRazkids).order_by(NivelRazkids.id_nivel_rk)).all())
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["vigente"], letras[3].id_nivel_rk)
    datos = _comparar(client, cabeceras, entorno, resultado="nivel_alfabetico")
    assert [n["codigo"] for n in datos["escala"]] == [letra.letra for letra in letras]
    assert (datos["escala"][0]["codigo"], datos["escala"][-1]["codigo"]) == ("aa", "Z")
    fila = datos["ranking"][0]
    assert (fila["indice"], fila["posicion"]) == (None, None)
    assert [(n["codigo"], n["alumnos"]) for n in fila["distribucion"] if n["alumnos"]] == [(letras[3].letra, 1)]
    assert [g["colegio"]["nombre"] for g in datos["sin_evaluaciones"]] == ["Colegio Lejano"]


# ── Un alumno, una vez ──────────────────────────────────────────────────────────────

def test_un_alumno_cuenta_una_vez_por_grupo(client, cabeceras, db, entorno):
    # Ana tiene dos decisiones del PA2 en grupos distintos (V1): cuenta una vez, con la vigente.
    ana, vigente, lejano = entorno.alumnos["ana"], entorno.periodos["vigente"], _colegio(entorno, "Lejano")
    decision_de_vuelo(db, ana, vigente, _letra_del_nivel(db, ana.id_grado, INICIO),
                      fecha=tiempo.ahora_utc() - timedelta(days=3))
    decision_de_vuelo(db, ana, vigente, _letra_del_nivel(db, ana.id_grado, AVANZADO), id_colegio=lejano.id_colegio,
                      id_seccion=seccion_de(db, lejano, ana.id_grado).id_seccion)
    fila = _fila(_comparar(client, cabeceras, entorno)["ranking"], "Colegio Andino")
    assert (fila["total_a_evaluar"], fila["evaluados"], fila["indice"]) == (1, 1, 4.0)


# ── Roles ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol", ["Directivo", "Docente"])
def test_solo_el_supervisor_compara(client, cabeceras, entorno, rol):
    respuesta = _get(client, cabeceras, entorno, rol=rol)
    assert (respuesta.status_code, motivo(respuesta)) == (403, "sin_permiso")
