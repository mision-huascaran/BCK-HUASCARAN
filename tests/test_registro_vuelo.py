"""Registro de Vuelo (CU031, CU032, T9): entrada (PA1 por grado, herencia V1),
sugerencia sobre la rúbrica, nivel del programa, avisos, escritura por PA y sus
integraciones (Lectura, Salud del Sistema, productividad, impacto del calendario)."""
import uuid
from datetime import date

import pytest
from sqlmodel import select

from app.models.evaluacion import NivelRubrica, RegistroVuelo, RubricaMensual
from app.models.organizacion import AlumnoPertenencia, AnioEscolar, PeriodoAcademico
from app.models.trazabilidad import Auditoria
from app.services import cambios, registro_vuelo
from app.services.calendario import pa_actual_de_colegios
from app.services.nivel_academico import nivel_academico
from tests.conftest import (
    a_las,
    crear_alumno,
    crear_permiso,
    decision_de_vuelo,
    motivo,
    rangos_relativos,
    seccion_de,
)
from tests.test_calendario_api import editar

# Miércoles. PA1 del 18/01 al 18/04 (culminado), PA2 del 18/05 al 17/07 (en curso), PA3 y
# PA4 futuros. El Docente tiene asignación en PA1 y PA2 para 1.º y 2.º del Andino.
HOY = date(2026, 6, 17)
CL, ALFA = "Comprensión Lectora", "Alfabetización"
# ids de nivel_razkids (el id es el orden): aa=1, A=2, C=4, E=6, F=7, G=8, Z=27.
AA, A, C, D, E, F, G, Z = 1, 2, 4, 5, 6, 7, 8, 27
SIN_AUTORIZACION = "No tienes autorización vigente para registrar o modificar este periodo."
TEXTO_TRASLADO = "Proviene del nivel colocado en el periodo anterior. Registrado antes del traslado."


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


def contexto(alumno) -> dict:
    return {"id_colegio": alumno.id_colegio, "id_grado": alumno.id_grado, "id_seccion": alumno.id_seccion,
            "id_programa": alumno.id_programa_actual}


def listar(client, cabecera, entorno, alumno="ana", **params):
    datos = contexto(entorno.alumnos[alumno])
    return client.get("/registro-vuelo", params={**datos, "id_anio_escolar": entorno.anio.id_anio_escolar, **params},
                      headers=cabecera)


def periodo_de_ana(client, cabecera, entorno, numero, alumno="ana") -> dict:
    [fila] = [i for i in listar(client, cabecera, entorno, alumno).json()["items"]
              if i["id_alumno"] == entorno.alumnos[alumno].id_alumno]
    return fila["periodos"][numero - 1]


def item(entorno, alumno="ana", periodo="vigente", version=None, id_operacion=None, **campos) -> dict:
    return {"id_operacion": str(id_operacion or uuid.uuid4()), "version": version,
            "id_alumno": entorno.alumnos[alumno].id_alumno,
            "id_periodo_academico": entorno.periodos[periodo].id_periodo_academico,
            **contexto(entorno.alumnos[alumno]), **campos}


def guardar(client, cabecera, *items):
    return client.put("/registro-vuelo", json=list(items), headers=cabecera)


def rubrica(db, entorno, alumno, periodo, mes, fluidez, comprension):
    """Rúbrica mensual completa del alumno (valores 1-4 de Comprensión Lectora / Alfabetización)."""
    alumno = entorno.alumnos[alumno]

    def nivel(dimension, valor):
        return db.exec(select(NivelRubrica).where(
            NivelRubrica.id_programa == alumno.id_programa_actual, NivelRubrica.dimension == dimension,
            NivelRubrica.valor == valor,
        )).one().id_nivel_rubrica

    fila = RubricaMensual(
        id_alumno=alumno.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=mes, **contexto(alumno),
        id_periodo_academico=entorno.periodos[periodo].id_periodo_academico,
        id_nivel_fluidez=nivel("Fluidez", fluidez), id_nivel_comprension=nivel("Comprensión", comprension),
        id_docente=entorno.id_docente, fecha_registro=a_las(HOY), creado_por=1, creado_en=a_las(HOY),
    )
    db.add(fila)
    db.commit()
    return fila


# ── GET: estructura, entrada y escritura por PA ─────────────────────────────────────

def test_listado_periodos_estados_y_escritura_por_pa(client, entorno, doc, actividad):
    cuerpo = listar(client, doc, entorno).json()

    assert [(p["nombre"], p["estado"]) for p in cuerpo["periodos"]] == [
        ("PA1", "culminado"), ("PA2", "en_curso"), ("PA3", "futuro"), ("PA4", "futuro"),
    ]
    assert [(p["escritura"]["permitida"], p["escritura"]["motivo"]) for p in cuerpo["periodos"]] == [
        (False, SIN_AUTORIZACION), (True, None),
        (False, "No se puede registrar un periodo académico que todavía no empieza."),
        (False, "No se puede registrar un periodo académico que todavía no empieza."),
    ]
    [ana] = cuerpo["items"]
    assert (ana["nombre"], ana["avance_anual"], ana["inactivo"], cuerpo["aviso"]) == ("Ana Torres", 0, False, None)
    pa1, pa2 = ana["periodos"][:2]
    assert pa1["entrada"] == {"nivel": {"id": C, "letra": "C"}, "origen": "grado",
                              "texto": "Nivel inicial según el grado del alumno.", "de_otro_colegio": False}
    assert pa2["entrada"] == {"nivel": None, "origen": "pendiente_periodo_anterior",
                              "texto": "Pendiente del periodo anterior", "de_otro_colegio": False}
    assert (pa2["sugerencia"]["texto"], pa2["nivel_colocado"], pa2["incompleto"]) == ("Pendiente de evaluar", None, True)


@pytest.mark.parametrize(("grado", "letra"), [
    ("1.º", "aa"), ("2.º", "C"), ("3.º", "F"), ("4.º", "J"), ("5.º", "M"), ("6.º", "Q"),
])
def test_entrada_del_pa1_por_grado(client, db, entorno, cabeceras, grado, letra):
    programa = ALFA if grado == "1.º" else CL
    entorno.alumnos["nuevo"] = crear_alumno(
        db, "Nico", "Nuevo", entorno.colegios["Lejano"], entorno.grados[grado], entorno.programas[programa], 1,
        desde=entorno.anio.fecha_inicio,
    )
    db.commit()

    pa1 = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 1, alumno="nuevo")

    assert pa1["entrada"]["nivel"]["letra"] == letra


def test_herencia_del_pa_anterior_y_v1_con_cambio_de_colegio(client, db, entorno, cabeceras):
    """La entrada del PA2 es la decisión del PA1 aunque haya sido en otro colegio (con el
    aviso del traslado); si hay dos en el PA1, la más reciente."""
    ana, segundo = entorno.alumnos["ana"], entorno.grados["2.º"]
    lejano = entorno.colegios["Lejano"]
    decision_de_vuelo(db, ana, entorno.periodos["pasado"], D, fecha=a_las(date(2026, 3, 2)))
    decision_de_vuelo(db, ana, entorno.periodos["pasado"], E, fecha=a_las(date(2026, 4, 10)),
                      id_colegio=lejano.id_colegio, id_seccion=seccion_de(db, lejano, segundo).id_seccion)

    pa2 = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)

    assert pa2["entrada"] == {"nivel": {"id": E, "letra": "E"}, "origen": "periodo_anterior",
                              "texto": TEXTO_TRASLADO, "de_otro_colegio": True}


def _decision_en_lejano(db, entorno, nivel=F):
    """Decisión de Ana en el PA1 en el Colegio Lejano, antes de trasladarse al Andino."""
    lejano, segundo = entorno.colegios["Lejano"], entorno.grados["2.º"]
    return decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], nivel,
                             id_colegio=lejano.id_colegio, id_seccion=seccion_de(db, lejano, segundo).id_seccion)


def test_traslado_hereda_la_entrada_con_aviso(client, db, entorno, cabeceras):
    _decision_en_lejano(db, entorno)

    entrada = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["entrada"]

    assert entrada == {"nivel": {"id": F, "letra": "F"}, "origen": "periodo_anterior", "texto": TEXTO_TRASLADO,
                       "de_otro_colegio": True}


def test_mismo_colegio_no_es_traslado(client, db, entorno, cabeceras):
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], F)

    entrada = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["entrada"]

    assert (entrada["nivel"]["letra"], entrada["de_otro_colegio"]) == ("F", False)
    assert entrada["texto"] == "Proviene del nivel colocado en el periodo anterior."


def test_otro_anio_escolar_no_hereda(client, db, entorno, cabeceras):
    otro = AnioEscolar(nombre="Otro", fecha_inicio=date(2025, 3, 1), fecha_fin=date(2025, 12, 20),
                       creado_por=1, creado_en=a_las(HOY))
    db.add(otro)
    db.flush()
    pa_otro = PeriodoAcademico(id_anio_escolar=otro.id_anio_escolar, numero=1, creado_por=1, creado_en=a_las(HOY))
    db.add(pa_otro)
    db.commit()
    decision_de_vuelo(db, entorno.alumnos["ana"], pa_otro, F)

    pa1, pa2 = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 1), periodo_de_ana(
        client, cabeceras["Supervisor"], entorno, 2)

    assert pa1["entrada"]["origen"] == "grado"
    assert (pa2["entrada"]["nivel"], pa2["entrada"]["origen"]) == (None, "pendiente_periodo_anterior")


def test_guardar_en_pa2_conserva_el_registro_de_entrada_de_otro_colegio(client, db, entorno, doc, actividad):
    anterior = _decision_en_lejano(db, entorno)

    [resultado] = guardar(client, doc, item(entorno, id_nivel_colocado=G)).json()

    assert resultado["estado"] == "aceptada", resultado
    assert resultado["registro"]["entrada"]["de_otro_colegio"] is True
    fila = db.exec(select(RegistroVuelo).where(RegistroVuelo.id_periodo_academico ==
                                               entorno.periodos["vigente"].id_periodo_academico)).one()
    assert (fila.id_registro_entrada, fila.id_nivel_entrada_al_decidir) == (anterior.id, F)


def test_el_docente_del_nuevo_colegio_no_edita_la_decision_original(client, db, entorno, doc, actividad):
    anterior = _decision_en_lejano(db, entorno)
    del_lejano = {"id_colegio": anterior.id_colegio, "id_grado": anterior.id_grado,
                  "id_seccion": anterior.id_seccion, "id_programa": anterior.id_programa}

    respuesta = guardar(client, doc, item(entorno, periodo="pasado", id_nivel_colocado=G, **del_lejano))
    consulta = client.get("/registro-vuelo", params={**del_lejano, "id_anio_escolar": entorno.anio.id_anio_escolar},
                          headers=doc)

    [resultado] = respuesta.json()
    assert (resultado["estado"], resultado["motivo"]) == ("rechazada", "aula_fuera_de_alcance")
    assert (consulta.status_code, motivo(consulta)) == (403, "aula_fuera_de_alcance")
    db.expire_all()
    assert db.get(RegistroVuelo, anterior.id).id_nivel_colocado == F


def test_sugerencia_con_y_sin_rubrica_completa(client, db, entorno, cabeceras):
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], E)
    sin_rubrica = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["sugerencia"]
    rubrica(db, entorno, "ana", "vigente", 5, 1, 1)
    junio = rubrica(db, entorno, "ana", "vigente", 6, 3, 3)

    sugerencia = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["sugerencia"]

    assert (sin_rubrica["texto"], sin_rubrica["accion"], sin_rubrica["rubrica"]) == ("Pendiente de evaluar", None, None)
    assert sugerencia == {
        "accion": "subir", "nivel": {"id": F, "letra": "F"}, "texto": "Subir nivel", "resultado_decimas": 30,
        "excepcion": None, "rubrica": {"id": junio.id, "mes": "2026-06", "fluidez": "Logrado", "comprension": "Logrado",
                                       "resultado_decimas": 30},
    }


def test_sugerencia_sin_entrada_queda_pendiente(client, db, entorno, cabeceras):
    rubrica(db, entorno, "ana", "vigente", 6, 3, 3)

    sugerencia = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["sugerencia"]

    assert (sugerencia["texto"], sugerencia["accion"], sugerencia["rubrica"]["resultado_decimas"]) == (
        "Pendiente de evaluar", None, 30,
    )


def test_excepciones_aa_y_z_sobre_la_entrada(client, db, entorno, cabeceras):
    rubrica(db, entorno, "luis", "pasado", 4, 1, 1)  # 10: bajar
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], Z)
    rubrica(db, entorno, "ana", "vigente", 6, 4, 4)  # 40: subir

    en_aa = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 1, alumno="luis")["sugerencia"]
    en_z = periodo_de_ana(client, cabeceras["Supervisor"], entorno, 2)["sugerencia"]

    assert (en_aa["accion"], en_aa["texto"], en_aa["excepcion"], en_aa["nivel"]) == (
        "mantener", "Mantener, nivel mínimo", "aa", {"id": AA, "letra": "aa"},
    )
    assert (en_z["accion"], en_z["texto"], en_z["excepcion"], en_z["nivel"]["letra"]) == (
        "mantener", "Mantener, nivel máximo", "Z", "Z",
    )


def test_nivel_del_programa_avance_anual_e_inactivos(client, db, entorno, cabeceras):
    ana = entorno.alumnos["ana"]
    decision_de_vuelo(db, ana, entorno.periodos["pasado"], C)
    decision_de_vuelo(db, ana, entorno.periodos["vigente"], G)
    ines = crear_alumno(db, "Inés", "Inactiva", entorno.colegios["Andino"], ana.id_grado, ana.id_programa_actual, 1,
                        desde=entorno.anio.fecha_inicio)
    intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == ines.id_alumno)).one()
    intervalo.hasta, ines.activo = date(2026, 5, 4), False
    db.add(intervalo)
    db.add(ines)
    db.commit()

    cuerpo = listar(client, cabeceras["Supervisor"], entorno).json()

    assert [(i["nombre"], i["inactivo"], i["avance_anual"]) for i in cuerpo["items"]] == [
        ("Inés Inactiva", True, 0), ("Ana Torres", False, 50),
    ]
    # 2.º: Logrado desde F, Avanzado desde G.
    assert [p["nivel_programa"] for p in cuerpo["items"][1]["periodos"]] == ["Inicio", "Avanzado", None, None]


def test_sin_alumnos(client, entorno, cabeceras):
    cuerpo = listar(client, cabeceras["Supervisor"], entorno, id_programa=entorno.programas[ALFA]).json()

    assert (cuerpo["items"], cuerpo["aviso"]) == ([], "No hay alumnos que correspondan a este grupo en el año seleccionado.")


def test_roles_y_alcance(client, entorno, cabeceras):
    supervisor = listar(client, cabeceras["Supervisor"], entorno).json()

    assert {p["escritura"]["motivo"] for p in supervisor["periodos"]} == {
        "El Supervisor consulta el Registro de Vuelo en solo lectura.",
    }
    assert listar(client, cabeceras["Directivo"], entorno).status_code == 403
    assert listar(client, {}, entorno).status_code == 401
    fuera = listar(client, cabeceras["Docente"], entorno, alumno="mario")
    assert (fuera.status_code, motivo(fuera)) == (403, "aula_fuera_de_alcance")
    assert guardar(client, cabeceras["Supervisor"], item(entorno, id_nivel_colocado=C)).status_code == 403
    assert guardar(client, cabeceras["Directivo"], item(entorno, id_nivel_colocado=C)).status_code == 403
    assert guardar(client, {}, item(entorno, id_nivel_colocado=C)).status_code == 401


# ── PUT ─────────────────────────────────────────────────────────────────────────────

def test_guardar_registra_la_decision_con_su_contexto(client, db, entorno, doc, actividad):
    anterior = decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], E)
    referencia = rubrica(db, entorno, "ana", "vigente", 6, 3, 3)

    [resultado] = guardar(client, doc, item(entorno, id_nivel_colocado=F, id_nivel_proyeccion=G,
                                            observacion=" Lee con fluidez. ")).json()

    assert resultado["estado"] == "aceptada", resultado
    registro = resultado["registro"]
    assert (registro["nivel_colocado"], registro["proyeccion"], registro["observacion"], registro["version"]) == (
        {"id": F, "letra": "F"}, {"id": G, "letra": "G"}, "Lee con fluidez.", 1,
    )
    assert (registro["incompleto"], registro["aviso"], registro["nivel_programa"]) == (False, None, "Logrado")
    fila = db.exec(select(RegistroVuelo).where(RegistroVuelo.id_periodo_academico ==
                                               entorno.periodos["vigente"].id_periodo_academico)).one()
    assert (fila.id_rubrica_referencia, fila.rubrica_version, fila.resultado_decimas, fila.id_nivel_entrada_al_decidir) == (
        referencia.id, 1, 30, E,
    )
    assert fila.id_registro_entrada == anterior.id
    assert fila.id_actividad == uuid.UUID(actividad["id"])
    assert cambios.cambios_por_actividad(db, [fila.id_actividad])[fila.id_actividad] == {"registro_vuelo": 1}


def test_igual_a_la_entrada_es_una_decision(client, db, entorno, doc, actividad):
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], E)

    [resultado] = guardar(client, doc, item(entorno, id_nivel_colocado=E)).json()

    assert (resultado["registro"]["nivel_colocado"]["letra"], resultado["registro"]["incompleto"]) == ("E", False)


def test_aviso_si_se_corrige_el_pa_anterior_despues_de_decidir(client, db, entorno, doc, actividad):
    anterior = decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], E)
    guardar(client, doc, item(entorno, id_nivel_colocado=F))
    anterior = db.get(RegistroVuelo, anterior.id)
    anterior.id_nivel_colocado = D
    db.add(anterior)
    db.commit()

    pa2 = periodo_de_ana(client, doc, entorno, 2)

    assert (pa2["entrada"]["nivel"]["letra"], pa2["aviso_entrada_cambio"], pa2["aviso"]) == (
        "D", True,
        "El nivel de entrada cambió por una corrección del periodo anterior. Revisa la decisión de este periodo.",
    )


def test_conflicto_de_version_y_cambio_solo_de_sugerencia(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, id_nivel_colocado=F))
    [vieja] = guardar(client, doc, item(entorno, id_nivel_colocado=G)).json()
    rubrica(db, entorno, "ana", "vigente", 6, 2, 2)  # cambia solo la sugerencia
    [observacion] = guardar(client, doc, item(entorno, version=1, observacion="Bien")).json()

    assert (vieja["estado"], vieja["motivo"], vieja["detail"]) == (
        "rechazada", "modificacion_concurrente",
        "El registro de este alumno y periodo fue modificado desde que lo consultaste. Revisa la versión actual antes "
        "de guardar nuevamente.",
    )
    assert vieja["registro"]["nivel_colocado"]["letra"] == "F"
    assert (observacion["estado"], observacion["registro"]["version"]) == ("aceptada", 2)


def test_sin_cambios_no_escribe(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, id_nivel_colocado=F, observacion="Bien"))
    antes = len(db.exec(select(Auditoria)).all())

    [igual] = guardar(client, doc, item(entorno, version=1, id_nivel_colocado=F, observacion="Bien ")).json()

    assert (igual["estado"], igual["registro"]["version"]) == ("aceptada", 1)
    assert len(db.exec(select(Auditoria)).all()) == antes


def test_proyeccion_y_observacion_se_borran_pero_el_colocado_no(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, id_nivel_colocado=F, id_nivel_proyeccion=G, observacion="Bien"))

    [borrado] = guardar(client, doc, item(entorno, version=1, id_nivel_proyeccion=None, observacion=None)).json()
    [sin_colocado] = guardar(client, doc, item(entorno, version=2, id_nivel_colocado=None)).json()

    assert (borrado["registro"]["proyeccion"], borrado["registro"]["observacion"]) == (None, None)
    assert (sin_colocado["estado"], sin_colocado["motivo"]) == ("rechazada", "nivel_colocado_obligatorio")


@pytest.mark.parametrize("campo", ["id_nivel_colocado", "id_nivel_proyeccion"])
@pytest.mark.parametrize("nivel", [28, 29, 0])
def test_z1_z2_y_niveles_inexistentes(client, entorno, doc, actividad, campo, nivel):
    [resultado] = guardar(client, doc, item(entorno, **{campo: nivel})).json()

    assert (resultado["motivo"], resultado["detail"]) == ("nivel_invalido", "Selecciona un nivel válido entre aa y Z.")


@pytest.mark.parametrize("calculado", [{"entrada": {"letra": "C"}}, {"sugerencia": "subir"}, {"nivel_programa": "x"}])
def test_campos_calculados_no_se_aceptan(client, entorno, doc, actividad, calculado):
    respuesta = guardar(client, doc, item(entorno, id_nivel_colocado=C, **calculado))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


def test_escritura_por_pa_y_permisos(client, db, entorno, doc, actividad):
    [sin_permiso] = guardar(client, doc, item(entorno, periodo="pasado", id_nivel_colocado=C)).json()
    crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="lectura")
    [otro_modulo] = guardar(client, doc, item(entorno, periodo="pasado", id_nivel_colocado=C)).json()
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="registro_vuelo")
    [con_permiso] = guardar(client, doc, item(entorno, periodo="pasado", id_nivel_colocado=C)).json()

    assert [(r["motivo"], r["detail"]) for r in (sin_permiso, otro_modulo)] == [("sin_habilitacion", SIN_AUTORIZACION)] * 2
    assert con_permiso["estado"] == "aceptada"
    pa1 = listar(client, doc, entorno).json()["periodos"][0]
    assert (pa1["escritura"]["permitida"], pa1["escritura"]["id_permiso"]) == (True, permiso.id_permiso)
    assert db.exec(select(RegistroVuelo)).one().id_permiso == permiso.id_permiso


def test_sin_actividad(client, entorno, doc):
    [sin_actividad] = guardar(client, doc, item(entorno, id_nivel_colocado=C)).json()

    assert (sin_actividad["motivo"], sin_actividad["detail"]) == (
        "actividad_requerida", "Debes iniciar una actividad de trabajo para registrar o modificar información.",
    )


def test_pa_futuro_y_alumno_fuera_del_grupo(client, entorno, doc, actividad):
    luis_en_segundo = {**item(entorno, "luis", id_nivel_colocado=C), **contexto(entorno.alumnos["ana"])}
    [futuro, fuera] = guardar(client, doc, item(entorno, periodo="futuro", id_nivel_colocado=C), luis_en_segundo).json()

    assert (futuro["motivo"], futuro["detail"]) == (
        "periodo_futuro", "No se puede registrar un periodo académico que todavía no empieza.",
    )
    assert fuera["motivo"] == "alumno_fuera_del_grupo"


def test_idempotencia(client, db, entorno, doc, actividad):
    id_operacion = uuid.uuid4()
    primera = guardar(client, doc, item(entorno, id_operacion=id_operacion, id_nivel_colocado=C)).json()
    reintento = guardar(client, doc, item(entorno, id_operacion=id_operacion, id_nivel_colocado=C)).json()

    assert primera == reintento
    assert len(db.exec(select(RegistroVuelo)).all()) == 1


# ── Integraciones ───────────────────────────────────────────────────────────────────

def test_nivel_academico_en_lectura(db, entorno):
    ana = entorno.alumnos["ana"]
    decision_de_vuelo(db, ana, entorno.periodos["pasado"], E)
    decision_de_vuelo(db, ana, entorno.periodos["vigente"], G)

    en_pa2 = nivel_academico(db, ana.id_alumno, ana.id_colegio, date(2026, 6, 15))
    en_pa1 = nivel_academico(db, ana.id_alumno, ana.id_colegio, date(2026, 4, 13))

    assert (en_pa2, en_pa1) == ("E", None)


def test_salud_cuenta_alumno_por_pa_vigente_con_nivel_colocado(db, entorno):
    ana, luis = entorno.alumnos["ana"], entorno.alumnos["luis"]
    pa = pa_actual_de_colegios(db, solo_activos=True)
    decision_de_vuelo(db, ana, entorno.periodos["pasado"], C)
    decision_de_vuelo(db, luis, entorno.periodos["vigente"], None)
    assert registro_vuelo.decisiones_confirmadas(db, pa) == 0

    decision_de_vuelo(db, ana, entorno.periodos["vigente"], E)

    assert registro_vuelo.decisiones_confirmadas(db, pa) == 1


def test_calendario_no_puede_dejar_una_decision_antes_de_su_pa(client, db, entorno, cabeceras):
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["vigente"], E)
    rangos = rangos_relativos(HOY)
    rangos["PR1"] = (rangos["PR1"][0], date(2026, 6, 21))
    rangos["PA2"] = (date(2026, 6, 22), rangos["PA2"][1])

    respuesta = editar(client, cabeceras["Supervisor"], entorno.calendario.id_calendario, rangos)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_incompatible")
    [incompatible] = respuesta.json()["incompatibilidades"]
    assert (incompatible["tipo_registro"], incompatible["motivo"], incompatible["fecha"]) == (
        "registro_vuelo", "antes_del_pa", HOY.isoformat(),
    )
