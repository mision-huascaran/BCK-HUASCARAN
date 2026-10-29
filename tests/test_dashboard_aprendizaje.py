"""Dashboard D2: distribución por subprograma y resultados de aprendizaje (CU033, CU035).

El entorno: hoy en el PA2 ("vigente"), PA1 ("pasado") terminado y PA3 ("futuro") sin
iniciar. Luis (Andino, 1.º, Alfabetización), Ana (Andino, 2.º, Comprensión Lectora) y
Mario (Lejano, 3.º, Comprensión Lectora) pertenecen a su grupo desde el inicio del año.
"""
from datetime import timedelta

import pytest
from sqlmodel import col, select

from app.core import tiempo
from app.models.evaluacion import NIVELES_DESCRIPTIVOS, NivelRazkids, NivelRubrica, RubricaMensual
from app.models.organizacion import AlumnoPertenencia, ColegioPrograma
from app.services.dashboard.aprendizaje import MENSAJE_SIN_EVALUACIONES, MENSAJE_SIN_INFORMACION
from app.services.motor_sugerencias import COMPRENSION, FLUIDEZ
from app.services.nivel_descriptivo import nivel_descriptivo
from app.services.rubrica_service import ultima_rubrica_completa, ultimas_rubricas_completas
from tests.conftest import crear_colegio, crear_particular, decision_de_vuelo, motivo, rangos_en_receso, seccion_de

ALFA, CL = "Alfabetización", "Comprensión Lectora"

# ── Preparación ─────────────────────────────────────────────────────────────────────


def _get(client, cabeceras, entorno, ruta, periodo="vigente", rol="Supervisor", **params):
    if "id_colegios" not in params:
        params["todos_los_colegios"] = True
    params.update(id_anio_escolar=entorno.anio.id_anio_escolar,
                  id_periodo_academico=entorno.periodos[periodo].id_periodo_academico)
    return client.get(f"/dashboard/{ruta}", params=params, headers=cabeceras[rol])


def _ok(respuesta) -> dict:
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _distribucion(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, "distribucion-subprograma", **params))


def _resultados(client, cabeceras, entorno, **params) -> dict:
    return _ok(_get(client, cabeceras, entorno, "resultados-aprendizaje", **params))


def _por_subprograma(datos) -> dict[str, int]:
    return {s["subprograma"]["nombre"]: s["alumnos"] for s in datos["subprogramas"]}


def _subprograma(datos, nombre) -> dict:
    return next(s for s in datos["subprogramas"] if s["subprograma"]["nombre"] == nombre)


def _distribucion_de(datos, nombre, resultado) -> dict:
    return next(d for d in _subprograma(datos, nombre)["distribuciones"] if d["resultado"] == resultado)


def _conteo(distribucion) -> dict[str, int]:
    return {n["nombre"]: n["alumnos"] for n in distribucion["niveles"] if n["alumnos"]}


def _mover(db, alumno, desde, id_programa):
    """El alumno pasa a otro subprograma (en su mismo colegio, grado y sección) desde esa fecha."""
    actual = db.exec(select(AlumnoPertenencia).where(
        AlumnoPertenencia.id_alumno == alumno.id_alumno, col(AlumnoPertenencia.hasta).is_(None),
    )).one()
    actual.hasta = desde
    db.add(actual)
    db.add(AlumnoPertenencia(
        id_alumno=alumno.id_alumno, id_colegio=actual.id_colegio, id_grado=actual.id_grado,
        id_seccion=actual.id_seccion, id_programa=id_programa, desde=desde, creado_por=1,
        creado_en=tiempo.ahora_utc(),
    ))
    db.commit()


def _nivel_rubrica(db, id_programa, dimension, valor):
    if valor is None:
        return None
    return db.exec(select(NivelRubrica.id_nivel_rubrica).where(
        NivelRubrica.id_programa == id_programa, NivelRubrica.dimension == dimension, NivelRubrica.valor == valor,
    )).one()


def _rubrica(db, entorno, alumno, periodo, mes, fluidez=None, comprension=None, id_programa=None):
    """Evaluación mensual del alumno guardada en ese PA, sin validar."""
    programa = id_programa or alumno.id_programa_actual
    ahora = tiempo.ahora_utc()
    fila = RubricaMensual(
        id_alumno=alumno.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=mes,
        id_colegio=alumno.id_colegio, id_grado=alumno.id_grado, id_seccion=alumno.id_seccion, id_programa=programa,
        id_periodo_academico=entorno.periodos[periodo].id_periodo_academico,
        id_nivel_fluidez=_nivel_rubrica(db, programa, FLUIDEZ, fluidez),
        id_nivel_comprension=_nivel_rubrica(db, programa, COMPRENSION, comprension),
        id_docente=entorno.id_docente, fecha_registro=ahora, creado_por=1, creado_en=ahora,
    )
    db.add(fila)
    db.commit()
    return fila


def _letra(db, letra) -> int:
    return db.exec(select(NivelRazkids.id_nivel_rk).where(NivelRazkids.letra == letra)).one()


# ── Distribución por subprograma ────────────────────────────────────────────────────

def test_distribucion_cantidades_y_porcentajes(client, cabeceras, entorno):
    datos = _distribucion(client, cabeceras, entorno)
    assert datos["total"] == 3
    assert datos["sin_datos"] is False
    assert [(s["subprograma"]["nombre"], s["alumnos"], s["porcentaje"]) for s in datos["subprogramas"]] == [
        (ALFA, 1, 33.3), (CL, 2, 66.7),
    ]
    assert datos["contexto"]["periodo"]["nombre"] == "Segundo periodo"


def test_distribucion_muestra_los_dos_subprogramas_aunque_uno_tenga_cero(client, cabeceras, entorno):
    datos = _distribucion(client, cabeceras, entorno, id_colegios=[entorno.colegios["Lejano"].id_colegio])
    assert _por_subprograma(datos) == {ALFA: 0, CL: 1}
    assert [s["porcentaje"] for s in datos["subprogramas"]] == [0.0, 100.0]


def test_distribucion_alumno_inactivo_hoy_que_pertenecia_al_corte(client, cabeceras, db, entorno):
    luis = entorno.alumnos["luis"]
    salida = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == luis.id_alumno)).one()
    salida.hasta = entorno.fin("pasado") + timedelta(days=5)
    luis.activo = False
    db.add_all([salida, luis])
    db.commit()
    assert _por_subprograma(_distribucion(client, cabeceras, entorno, periodo="pasado")) == {ALFA: 1, CL: 2}
    assert _por_subprograma(_distribucion(client, cabeceras, entorno)) == {ALFA: 0, CL: 2}


def test_distribucion_traslado_despues_del_corte_cuenta_en_el_grupo_del_corte(client, cabeceras, db, entorno):
    _mover(db, entorno.alumnos["ana"], entorno.fin("pasado") + timedelta(days=3), entorno.programas[ALFA])
    assert _por_subprograma(_distribucion(client, cabeceras, entorno, periodo="pasado")) == {ALFA: 1, CL: 2}


def test_distribucion_traslado_antes_del_corte_cuenta_en_el_nuevo(client, cabeceras, db, entorno):
    _mover(db, entorno.alumnos["ana"], entorno.fin("pasado") - timedelta(days=3), entorno.programas[ALFA])
    assert _por_subprograma(_distribucion(client, cabeceras, entorno, periodo="pasado")) == {ALFA: 2, CL: 1}


def test_distribucion_alumno_que_aun_no_pertenecia_al_corte_no_cuenta(client, cabeceras, db, entorno):
    mario = db.exec(select(AlumnoPertenencia).where(
        AlumnoPertenencia.id_alumno == entorno.alumnos["mario"].id_alumno
    )).one()
    mario.desde = entorno.fin("pasado") + timedelta(days=1)
    db.add(mario)
    db.commit()
    assert _distribucion(client, cabeceras, entorno, periodo="pasado")["total"] == 2


# ── Fechas de corte ─────────────────────────────────────────────────────────────────

def _fechas(datos) -> dict[str, str]:
    return {f["colegio"]["nombre"]: f["fecha"] for f in datos["fechas_corte"]}


def test_corte_pa_terminado_es_su_fin(client, cabeceras, entorno):
    fin = entorno.fin("pasado").isoformat()
    datos = _distribucion(client, cabeceras, entorno, periodo="pasado")
    assert _fechas(datos) == {"Colegio Andino": fin, "Colegio Lejano": fin}
    assert datos["colegios_sin_informacion"] == []


def test_corte_pa_en_curso_es_hoy(client, cabeceras, entorno):
    hoy = entorno.hoy.isoformat()
    assert _fechas(_distribucion(client, cabeceras, entorno)) == {"Colegio Andino": hoy, "Colegio Lejano": hoy}


def test_corte_pa_sin_iniciar_va_a_colegios_sin_informacion(client, cabeceras, entorno):
    datos = _distribucion(client, cabeceras, entorno, periodo="futuro")
    assert datos["fechas_corte"] == []
    assert [(c["colegio"]["nombre"], c["motivo"]) for c in datos["colegios_sin_informacion"]] == [
        ("Colegio Andino", "periodo_no_iniciado"), ("Colegio Lejano", "periodo_no_iniciado"),
    ]
    assert datos["sin_datos"] is True


def test_dos_calendarios_dos_fechas_de_corte(client, cabeceras, db, entorno):
    fin_lejano = entorno.hoy - timedelta(days=5)
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy, fin_pa2=fin_lejano))
    assert _fechas(_distribucion(client, cabeceras, entorno)) == {
        "Colegio Andino": entorno.hoy.isoformat(), "Colegio Lejano": fin_lejano.isoformat(),
    }


# ── Sin datos ───────────────────────────────────────────────────────────────────────

def _colegio_vacio(db, entorno):
    vacio = crear_colegio(db, "Colegio Vacío", sorted(entorno.grados.values()), sorted(entorno.programas.values()), 1)
    db.commit()
    return vacio.id_colegio


def test_sin_alumnos_no_inventa_ceros(client, cabeceras, db, entorno):
    id_vacio = _colegio_vacio(db, entorno)
    distribucion = _distribucion(client, cabeceras, entorno, id_colegios=[id_vacio])
    assert (distribucion["sin_datos"], distribucion["total"], distribucion["subprogramas"]) == (True, None, [])
    assert distribucion["mensaje"] == MENSAJE_SIN_INFORMACION
    resultados = _resultados(client, cabeceras, entorno, id_colegios=[id_vacio])
    assert (resultados["sin_datos"], resultados["subprogramas"]) == (True, [])
    assert resultados["mensaje"] == MENSAJE_SIN_INFORMACION


# ── Resultados: rúbrica ─────────────────────────────────────────────────────────────

def test_rubrica_usa_la_ultima_completa_del_pa(client, cabeceras, db, entorno):
    ana = entorno.alumnos["ana"]
    _rubrica(db, entorno, ana, "vigente", mes=5, fluidez=1, comprension=1)
    _rubrica(db, entorno, ana, "vigente", mes=6, fluidez=3, comprension=2)
    _rubrica(db, entorno, ana, "vigente", mes=7, fluidez=4)  # más reciente pero incompleta
    _rubrica(db, entorno, ana, "pasado", mes=3, fluidez=4, comprension=4)  # completa de otro PA
    datos = _resultados(client, cabeceras, entorno)
    fluidez, comprension = _distribucion_de(datos, CL, "fluidez"), _distribucion_de(datos, CL, "comprension")
    assert (_conteo(fluidez), _conteo(comprension)) == ({"Logrado": 1}, {"En proceso": 1})
    assert (fluidez["evaluados"], fluidez["pendientes"]) == (1, 1)
    assert (comprension["evaluados"], comprension["pendientes"]) == (1, 1)
    assert fluidez["fuente"] == "rubrica"


def test_rubrica_sin_ninguna_completa_queda_pendiente(client, cabeceras, db, entorno):
    _rubrica(db, entorno, entorno.alumnos["ana"], "vigente", mes=6, fluidez=3)
    _rubrica(db, entorno, entorno.alumnos["mario"], "pasado", mes=3, fluidez=2, comprension=2)
    fluidez = _distribucion_de(_resultados(client, cabeceras, entorno), CL, "fluidez")
    assert (fluidez["evaluados"], fluidez["pendientes"]) == (0, 2)


def test_rubrica_de_otro_subprograma_queda_pendiente(client, cabeceras, db, entorno):
    # Ana pasó a Alfabetización antes de hoy; su rúbrica completa del PA es de Comprensión.
    ana = entorno.alumnos["ana"]
    _rubrica(db, entorno, ana, "vigente", mes=6, fluidez=3, comprension=3)
    _mover(db, ana, entorno.hoy - timedelta(days=1), entorno.programas[ALFA])
    fluidez = _distribucion_de(_resultados(client, cabeceras, entorno), ALFA, "fluidez")
    assert (fluidez["evaluados"], fluidez["pendientes"]) == (0, 2)


def test_rubrica_niveles_de_la_escala_en_orden(client, cabeceras, db, entorno):
    datos = _resultados(client, cabeceras, entorno)
    assert [n["nombre"] for n in _distribucion_de(datos, ALFA, "fluidez")["niveles"]] == [
        "Pre-inicio", "Inicio", "En proceso", "Logrado", "Avanzado",
    ]
    assert [n["nombre"] for n in _distribucion_de(datos, CL, "comprension")["niveles"]] == [
        "Inicio", "En proceso", "Logrado", "Avanzado",
    ]


def test_ultimas_rubricas_en_bloque_equivale_a_la_individual(db, entorno):
    luis, ana, mario = (entorno.alumnos[n] for n in ("luis", "ana", "mario"))
    _rubrica(db, entorno, luis, "vigente", mes=5, fluidez=0, comprension=1)
    _rubrica(db, entorno, luis, "vigente", mes=6, fluidez=2)
    _rubrica(db, entorno, ana, "vigente", mes=6, fluidez=3, comprension=2)
    _rubrica(db, entorno, ana, "vigente", mes=7, fluidez=4, comprension=4)
    _rubrica(db, entorno, mario, "vigente", mes=6, comprension=2)
    _rubrica(db, entorno, mario, "pasado", mes=3, fluidez=2, comprension=2)
    vigente = entorno.periodos["vigente"].id_periodo_academico
    ids = [luis.id_alumno, ana.id_alumno, mario.id_alumno]
    en_bloque = ultimas_rubricas_completas(db, ids, vigente)
    assert {a: r.mes for a, r in en_bloque.items()} == {luis.id_alumno: 5, ana.id_alumno: 7}
    for id_alumno in ids:
        individual = ultima_rubrica_completa(db, id_alumno, vigente)
        assert (individual.id if individual else None) == (en_bloque[id_alumno].id if id_alumno in en_bloque else None)
    assert ultimas_rubricas_completas(db, [], vigente) == {}


# ── Resultados: global ──────────────────────────────────────────────────────────────

def test_global_usa_la_decision_vigente_del_registro_de_vuelo(client, cabeceras, db, entorno):
    ana, vigente = entorno.alumnos["ana"], entorno.periodos["vigente"]
    antes = tiempo.ahora_utc() - timedelta(days=2)
    decision_de_vuelo(db, ana, vigente, _letra(db, "A"), fecha=antes)
    # V1: la vigente es la más reciente, en cualquier grupo (aquí, en Lejano).
    lejano = entorno.colegios["Lejano"]
    decision_de_vuelo(db, ana, vigente, _letra(db, "J"), id_colegio=lejano.id_colegio,
                      id_seccion=seccion_de(db, lejano, ana.id_grado).id_seccion)
    decision_de_vuelo(db, entorno.alumnos["mario"], vigente, None)  # sin nivel colocado
    glob = _distribucion_de(_resultados(client, cabeceras, entorno), CL, "global")
    assert _conteo(glob) == {nivel_descriptivo(db, ana.id_grado, _letra(db, "J")): 1}
    assert (glob["evaluados"], glob["pendientes"], glob["fuente"]) == (1, 1, "registro_vuelo")
    assert [n["nombre"] for n in glob["niveles"]] == list(NIVELES_DESCRIPTIVOS)


def test_global_con_el_grado_del_registro(client, cabeceras, db, entorno):
    # La decisión de Luis se tomó en 3.º: su nivel del programa sale de la escala de 3.º.
    luis, tercero = entorno.alumnos["luis"], entorno.grados["3.º"]
    letra = _letra(db, "E")
    assert nivel_descriptivo(db, tercero, letra) != nivel_descriptivo(db, luis.id_grado, letra)
    decision_de_vuelo(db, luis, entorno.periodos["vigente"], letra, id_grado=tercero)
    glob = _distribucion_de(_resultados(client, cabeceras, entorno), ALFA, "global")
    assert _conteo(glob) == {nivel_descriptivo(db, tercero, letra): 1}


# ── Porcentajes ─────────────────────────────────────────────────────────────────────

def test_porcentajes_sobre_evaluados(client, cabeceras, db, entorno):
    vigente = entorno.periodos["vigente"]
    decision_de_vuelo(db, entorno.alumnos["ana"], vigente, _letra(db, "A"))
    datos = _resultados(client, cabeceras, entorno)
    glob = _distribucion_de(datos, CL, "global")
    assert {n["nombre"]: n["porcentaje"] for n in glob["niveles"]} == {
        "Inicio": 100.0, "En proceso": 0.0, "Logrado": 0.0, "Avanzado": 0.0,
    }
    assert (glob["base_porcentaje"], glob["mensaje"]) == ("evaluados", None)
    assert _subprograma(datos, CL)["total_a_evaluar"] == 2


def test_sin_evaluados_mensaje_y_porcentaje_null(client, cabeceras, entorno):
    datos = _resultados(client, cabeceras, entorno)
    for subprograma in datos["subprogramas"]:
        for distribucion in subprograma["distribuciones"]:
            assert distribucion["evaluados"] == 0
            assert distribucion["pendientes"] == subprograma["total_a_evaluar"]
            assert distribucion["mensaje"] == MENSAJE_SIN_EVALUACIONES
            assert all(n["alumnos"] == 0 and n["porcentaje"] is None for n in distribucion["niveles"])


# ── Subprogramas configurados ───────────────────────────────────────────────────────

def _quitar_programa(db, entorno, colegio, programa):
    db.delete(db.get(ColegioPrograma, (entorno.colegios[colegio].id_colegio, entorno.programas[programa])))
    db.commit()


def test_grupo_sin_el_subprograma_configurado_no_se_suma(client, cabeceras, db, entorno):
    _quitar_programa(db, entorno, "Lejano", CL)
    assert _por_subprograma(_distribucion(client, cabeceras, entorno)) == {ALFA: 1, CL: 1}
    assert _subprograma(_resultados(client, cabeceras, entorno), CL)["total_a_evaluar"] == 1


def test_subprograma_no_configurado_en_ningun_colegio_no_aparece(client, cabeceras, db, entorno):
    _quitar_programa(db, entorno, "Lejano", CL)
    _quitar_programa(db, entorno, "Andino", CL)
    datos = _resultados(client, cabeceras, entorno)
    assert [(s["subprograma"]["nombre"], s["total_a_evaluar"]) for s in datos["subprogramas"]] == [(ALFA, 1)]


# ── Roles y filtros ─────────────────────────────────────────────────────────────────

def test_directivo_ve_resultados_y_no_la_distribucion(client, cabeceras, entorno):
    assert _get(client, cabeceras, entorno, "resultados-aprendizaje", rol="Directivo").status_code == 200
    distribucion = _get(client, cabeceras, entorno, "distribucion-subprograma", rol="Directivo")
    assert (distribucion.status_code, motivo(distribucion)) == (403, "sin_permiso")


@pytest.mark.parametrize("ruta", ["resultados-aprendizaje", "distribucion-subprograma"])
def test_docente_no_tiene_dashboard(client, cabeceras, entorno, ruta):
    assert _get(client, cabeceras, entorno, ruta, rol="Docente").status_code == 403


@pytest.mark.parametrize("ruta", ["resultados-aprendizaje", "distribucion-subprograma"])
def test_bloques_del_periodo_exigen_el_pa(client, cabeceras, entorno, ruta):
    respuesta = client.get(f"/dashboard/{ruta}", headers=cabeceras["Supervisor"], params={
        "id_anio_escolar": entorno.anio.id_anio_escolar, "todos_los_colegios": True,
    })
    assert respuesta.status_code == 422
    assert respuesta.json()["errores"][0]["campo"] == "id_periodo_academico"


def test_ruta_provisional_de_aprendizaje_retirada(client, cabeceras):
    assert client.get("/dashboard/aprendizaje", headers=cabeceras["Supervisor"]).status_code == 404
