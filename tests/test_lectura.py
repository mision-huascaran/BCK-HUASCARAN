"""Seguimiento de Lectura (CU023 a CU026, T7): tablas por grupo, subprograma y semana con
su clase de referencia, filas, "No realizada", /semanas y sus integraciones (detalle
del alumno en test_alumnos, Salud del Sistema, productividad e impacto del calendario)."""
import uuid
from datetime import date

import pytest
from sqlmodel import select

from app.models.evaluacion import Asistencia, Clase, ClaseAlumno, LecturaSemanal, LecturaSemanalAlumno
from app.models.organizacion import ColegioGrado, DocenteColegioGrado
from app.models.trazabilidad import Auditoria
from app.services import cambios, lectura_service
from app.services.calendario import pa_actual_de_colegios
from app.services.calendario_gestion import _registros
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    clase_guardada,
    crear_alumno,
    crear_particular,
    crear_permiso,
    motivo,
    rangos_en_receso,
    registrar_asistencia,
    semana_de,
)

# Miércoles. El PA2 del general va del lunes 18 de mayo al viernes 17 de julio; el PA1,
# del 18 de enero al 18 de abril. 1.º y 2.º del Andino tienen clase los lunes.
HOY = date(2026, 6, 17)
LUNES, MARTES, JUEVES = date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 18)
LUNES_ANTERIOR, MIERCOLES_ANTERIOR = date(2026, 6, 8), date(2026, 6, 10)
LUNES_SIGUIENTE = date(2026, 6, 22)
LUNES_DEL_PA1 = date(2026, 4, 13)
COMPRENSION, ALFABETIZACION = "Comprensión Lectora", "Alfabetización"

MENSAJE_SIN_ASISTENCIA = (
    "El estudiante debe tener asistencia Presente o Tardanza en la clase de referencia para registrar su lectura."
)
MENSAJE_LSN_INCOMPLETO = "Para registrar un LSN, completa el título, la nota obtenida y el puntaje máximo."
MENSAJE_LSN_INVALIDO = "Selecciona un máximo de 5 o 10 e ingresa una nota entre 0 y ese máximo."


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture(autouse=True)
def calendario(miercoles, db, entorno):
    """Clase los lunes en 1.º y 2.º del Andino; Beto (Alfabetización) también está en 2.º."""
    for grado in ("1.º", "2.º"):
        fila = db.get(ColegioGrado, (entorno.colegios["Andino"].id_colegio, entorno.grados[grado]))
        fila.dia_clase = 1
        db.add(fila)
    entorno.alumnos["beto"] = crear_alumno(
        db, "Beto", "Alfa", entorno.colegios["Andino"], entorno.grados["2.º"], entorno.programas[ALFABETIZACION],
        1, desde=entorno.anio.fecha_inicio,
    )
    db.commit()


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


def filtro(db, entorno, fecha=LUNES, programa=COMPRENSION, grado="2.º", mes=None) -> dict:
    ana = entorno.alumnos["ana"]
    return {
        "id_colegio": ana.id_colegio, "id_grado": entorno.grados[grado], "id_seccion": _seccion(db, entorno, grado),
        "id_programa": entorno.programas[programa], "id_anio_escolar": entorno.anio.id_anio_escolar,
        "mes": mes or fecha.month, "id_semana": semana_de(db, fecha).id_semana,
    }


def _seccion(db, entorno, grado) -> int:
    alumno = next(a for a in entorno.alumnos.values()
                  if a.id_grado == entorno.grados[grado] and a.id_colegio == entorno.colegios["Andino"].id_colegio)
    return alumno.id_seccion


def consultar(client, cabecera, db, entorno, **kwargs):
    params = filtro(db, entorno, **kwargs)
    db.commit()
    return client.get("/lectura/tablas", params=params, headers=cabecera)


def crear(client, cabecera, db, entorno, id_operacion=None, clase=None, **kwargs):
    cuerpo = {"id_operacion": str(id_operacion or uuid.uuid4()), **filtro(db, entorno, **kwargs), **(clase or {})}
    db.commit()
    return client.post("/lectura/tablas", json=cuerpo, headers=cabecera)


def fila(entorno, alumno="ana", version=1, **campos) -> dict:
    return {"id_operacion": str(uuid.uuid4()), "version": version, "id_alumno": entorno.alumnos[alumno].id_alumno,
            **campos}


def guardar(client, cabecera, id_tabla, *filas):
    return client.put(f"/lectura/tablas/{id_tabla}/filas", json=list(filas), headers=cabecera)


@pytest.fixture
def tabla(client, db, entorno, doc, actividad) -> dict:
    """La tabla de 2.º, Comprensión Lectora, de esta semana (clase del lunes); Ana presente."""
    respuesta = crear(client, doc, db, entorno)
    assert respuesta.status_code == 201, respuesta.text
    registrar_asistencia(db, entorno.alumnos["ana"], LUNES, "presente")
    return respuesta.json()


# ── Consulta (CU023, CU024) ─────────────────────────────────────────────────────────

def test_get_sin_tabla_sugiere_la_habitual(client, db, entorno, doc, actividad):
    respuesta = consultar(client, doc, db, entorno)

    assert (respuesta.status_code, motivo(respuesta)) == (404, "tabla_no_existe")
    assert respuesta.json()["clase_referencia_sugerida"] == {
        "id_clase": None, "fecha": LUNES.isoformat(), "tipo": "habitual", "reprogramada": False,
    }
    assert respuesta.json()["escritura"]["permitida"] is True


def test_get_semana_sin_clase_y_semana_futura(client, db, entorno, doc, actividad):
    agregar_no_lectivo(db, entorno.calendario, LUNES_ANTERIOR)

    sin_clase = consultar(client, doc, db, entorno, fecha=LUNES_ANTERIOR)
    futura = consultar(client, doc, db, entorno, fecha=LUNES_SIGUIENTE)

    assert (sin_clase.status_code, motivo(sin_clase)) == (422, "semana_sin_clase")
    assert futura.json()["escritura"] == {
        "permitida": False, "motivo": "No es posible adelantar registros. Seleccione la semana actual o una anterior.",
        "id_permiso": None, "aviso_permiso": None,
    }


def test_get_con_tabla_filas_de_la_lista_historica_del_programa(client, db, entorno, cabeceras, tabla):
    supervisor = consultar(client, cabeceras["Supervisor"], db, entorno).json()

    assert [f["nombre"] for f in supervisor["filas"]] == ["Ana Torres"]
    assert supervisor["filas"][0] == {
        "id_alumno": entorno.alumnos["ana"].id_alumno, "nombre": "Ana Torres", "inactivo": False,
        "asistencia": "presente", "nivel_academico": None, "lsn_titulo": None, "lsn_nota": None, "lsn_maximo": None,
        "cantidad_lsl": None, "total_libros": 0, "observacion": None, "estado_fila": "pendiente",
        "revisar_asistencia": False, "version": 1,
    }
    assert supervisor["escritura"] == {
        "permitida": False, "motivo": "El Supervisor consulta la lectura semanal en solo lectura.",
        "id_permiso": None, "aviso_permiso": None,
    }


@pytest.mark.parametrize(("rol", "codigo"), [("Directivo", 403), (None, 401)])
def test_get_roles(client, db, entorno, cabeceras, rol, codigo):
    assert consultar(client, cabeceras.get(rol, {}), db, entorno).status_code == codigo


def test_get_fuera_de_alcance_programa_y_semana_inexistentes(client, db, entorno, doc):
    lejano = {**filtro(db, entorno), "id_colegio": entorno.colegios["Lejano"].id_colegio}
    db.commit()
    sin_programa = {**filtro(db, entorno), "id_programa": 999}
    sin_semana = {**filtro(db, entorno), "id_semana": 999}
    db.commit()

    def get(params):
        return client.get("/lectura/tablas", params=params, headers=doc)

    assert (get(lejano).status_code, motivo(get(lejano))) == (403, "aula_fuera_de_alcance")
    assert (get(sin_programa).status_code, motivo(get(sin_programa))) == (422, "programa_inexistente")
    assert (get(sin_semana).status_code, motivo(get(sin_semana))) == (404, "semana_no_encontrada")


# ── Crear la tabla (CU023) ──────────────────────────────────────────────────────────

def test_crear_con_la_habitual_congela_la_lista_y_filtra_por_programa(client, db, entorno, doc, actividad):
    respuesta = crear(client, doc, db, entorno)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["estado"], cuerpo["version"], cuerpo["mes"]) == ("normal", 1, 6)
    assert (cuerpo["clase_referencia"]["fecha"], cuerpo["clase_referencia"]["tipo"]) == (LUNES.isoformat(), "habitual")
    assert [f["id_alumno"] for f in cuerpo["filas"]] == [entorno.alumnos["ana"].id_alumno]
    db.expire_all()
    clase = db.get(Clase, cuerpo["clase_referencia"]["id_clase"])
    assert clase.lista_congelada_en is not None
    # La lista congelada tiene a los dos subprogramas; la tabla, solo al suyo.
    lista = {c.id_alumno for c in db.exec(select(ClaseAlumno).where(ClaseAlumno.id_clase == clase.id_clase))}
    assert lista == {entorno.alumnos["ana"].id_alumno, entorno.alumnos["beto"].id_alumno}
    alfabetizacion = crear(client, doc, db, entorno, programa=ALFABETIZACION)
    assert [f["nombre"] for f in alfabetizacion.json()["filas"]] == ["Beto Alfa"]


def test_crear_es_idempotente_y_una_tabla_repetida_es_409(client, db, entorno, doc, actividad):
    id_operacion = uuid.uuid4()
    primera = crear(client, doc, db, entorno, id_operacion=id_operacion)
    reintento = crear(client, doc, db, entorno, id_operacion=id_operacion)
    repetida = crear(client, doc, db, entorno)

    assert (primera.status_code, reintento.status_code) == (201, 201)
    assert reintento.json()["id"] == primera.json()["id"]
    assert (repetida.status_code, motivo(repetida)) == (409, "tabla_existente")
    assert repetida.json()["detail"] == "Ya existe una tabla de lectura semanal para los filtros seleccionados."
    assert repetida.json()["id"] == primera.json()["id"]
    assert len(db.exec(select(LecturaSemanal)).all()) == 1


def test_crear_con_la_reprogramada(client, db, entorno, cabeceras, actividad):
    datos = filtro(db, entorno)
    cuerpo = {"id_grado": datos["id_grado"], "id_seccion": datos["id_seccion"],
              "fecha_original": LUNES_ANTERIOR.isoformat(), "fecha_nueva": MIERCOLES_ANTERIOR.isoformat(),
              "motivo": "Actuación"}
    reprogramada = client.post(f"/colegios/{datos['id_colegio']}/clases/reprogramaciones", json=cuerpo,
                               headers=cabeceras["Supervisor"])
    assert reprogramada.status_code == 201, reprogramada.text

    respuesta = crear(client, cabeceras["Docente"], db, entorno, fecha=LUNES_ANTERIOR)

    assert respuesta.json()["clase_referencia"] == {
        "id_clase": reprogramada.json()["id_clase"], "fecha": MIERCOLES_ANTERIOR.isoformat(), "tipo": "habitual",
        "reprogramada": True,
    }


def test_crear_con_una_adicional_explicita(client, db, entorno, doc, actividad):
    ana, beto = entorno.alumnos["ana"], entorno.alumnos["beto"]
    adicional = clase_guardada(db, ana, MARTES, lista=[ana, beto], tipo="adicional")
    db.commit()

    respuesta = crear(client, doc, db, entorno, clase={"id_clase_referencia": adicional.id_clase})

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["clase_referencia"]["tipo"] == "adicional"


def test_crear_sin_habitual_exige_elegir_la_adicional(client, db, entorno, doc, actividad):
    agregar_no_lectivo(db, entorno.calendario, LUNES_ANTERIOR)
    clase_guardada(db, entorno.alumnos["ana"], MIERCOLES_ANTERIOR, tipo="adicional")
    db.commit()

    sin_elegir = crear(client, doc, db, entorno, fecha=LUNES_ANTERIOR)
    por_fecha = crear(client, doc, db, entorno, fecha=LUNES_ANTERIOR,
                      clase={"fecha_clase_referencia": MIERCOLES_ANTERIOR.isoformat()})

    assert (sin_elegir.status_code, motivo(sin_elegir)) == (422, "clase_referencia_requerida")
    assert por_fecha.status_code == 201


@pytest.mark.parametrize(("kwargs", "codigo", "mensaje"), [
    ({"fecha": LUNES_SIGUIENTE}, "semana_futura",
     "No es posible adelantar registros. Seleccione la semana actual o una anterior."),
    ({"grado": "1.º"}, "sin_alumnos",
     "No hay estudiantes que pertenecieran a este grupo en la fecha de la clase seleccionada."),
])
def test_crear_rechazos_con_mensaje(client, db, entorno, doc, actividad, kwargs, codigo, mensaje):
    respuesta = crear(client, doc, db, entorno, **kwargs)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (422, codigo, mensaje)
    assert db.exec(select(LecturaSemanal)).all() == []


def test_crear_semana_sin_clase_clase_futura_y_clase_de_otra_semana(client, db, entorno, doc, actividad):
    agregar_no_lectivo(db, entorno.calendario, LUNES_ANTERIOR)
    futura = clase_guardada(db, entorno.alumnos["ana"], JUEVES, tipo="adicional")
    otra = clase_guardada(db, entorno.alumnos["ana"], date(2026, 6, 1))
    db.commit()

    sin_clase = crear(client, doc, db, entorno, fecha=LUNES_ANTERIOR)
    clase_futura = crear(client, doc, db, entorno, clase={"id_clase_referencia": futura.id_clase})
    de_otra_semana = crear(client, doc, db, entorno, clase={"id_clase_referencia": otra.id_clase})

    assert motivo(sin_clase) == "semana_sin_clase"
    assert (clase_futura.status_code, motivo(clase_futura)) == (422, "clase_no_realizada")
    assert (de_otra_semana.status_code, motivo(de_otra_semana)) == (422, "clase_fuera_de_la_semana")


def test_crear_sin_habilitacion_sin_actividad_y_roles(client, db, entorno, cabeceras):
    doc = cabeceras["Docente"]
    sin_actividad = crear(client, doc, db, entorno)
    supervisor = crear(client, cabeceras["Supervisor"], db, entorno)
    directivo = crear(client, cabeceras["Directivo"], db, entorno)

    assert (sin_actividad.status_code, motivo(sin_actividad), sin_actividad.json()["detail"]) == (
        422, "actividad_requerida", "Debes iniciar una actividad de trabajo para registrar o modificar información.",
    )
    assert (supervisor.status_code, directivo.status_code) == (403, 403)


def test_crear_en_un_pa_pasado_con_permiso(client, db, entorno, doc, actividad):
    sin_habilitacion = crear(client, doc, db, entorno, fecha=LUNES_DEL_PA1)
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º")
    con_permiso = crear(client, doc, db, entorno, fecha=LUNES_DEL_PA1)

    assert (sin_habilitacion.status_code, sin_habilitacion.json()["detail"]) == (
        422, "No tienes una habilitación vigente para crear la tabla de lectura de esta semana.",
    )
    assert con_permiso.status_code == 201, con_permiso.text
    assert db.get(LecturaSemanal, con_permiso.json()["id"]).id_permiso == permiso.id_permiso
    assert con_permiso.json()["escritura"]["id_permiso"] == permiso.id_permiso


def test_crear_con_la_asignacion_retroactiva_en_el_receso(client, db, entorno, doc, actividad):
    """El PA2 del colegio terminó ayer: con la asignación retroactiva del PA2 todavía se
    registra la semana de su último lunes."""
    crear_particular(db, entorno, rangos_en_receso(HOY), colegio="Andino")
    for asignacion in db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_periodo_academico == entorno.periodos["vigente"].id_periodo_academico
    )):
        asignacion.retroactiva = True
        db.add(asignacion)
    db.commit()

    assert crear(client, doc, db, entorno).status_code == 201


# ── Filas (CU024, CU025) ────────────────────────────────────────────────────────────

def test_fila_completada_total_y_auditoria(client, db, entorno, doc, tabla):
    resultado = guardar(client, doc, tabla["id"], fila(entorno, lsn_titulo="  El zorro ", lsn_nota=0, lsn_maximo=5,
                                                        cantidad_lsl=3, observacion="Bien"))

    [item] = resultado.json()
    assert item["estado"] == "aceptada", item
    assert (item["registro"]["lsn_titulo"], item["registro"]["total_libros"]) == ("El zorro", 4)
    assert (item["registro"]["estado_fila"], item["registro"]["version"]) == ("completada", 2)
    auditados = {a.campo for a in db.exec(select(Auditoria).where(Auditoria.tabla == "lectura_semanal_alumno"))}
    assert auditados == {"lsn_titulo", "lsn_nota", "lsn_maximo", "cantidad_lsl", "observacion"}


def test_fila_lsl_cero_completa_y_sin_lsl_queda_pendiente(client, db, entorno, doc, tabla):
    solo_lsn = guardar(client, doc, tabla["id"], fila(entorno, lsn_titulo="A", lsn_nota=10, lsn_maximo=10)).json()[0]
    cero = guardar(client, doc, tabla["id"], fila(entorno, version=2, cantidad_lsl=0)).json()[0]

    assert (solo_lsn["registro"]["estado_fila"], solo_lsn["registro"]["total_libros"]) == ("pendiente", 1)
    assert (cero["registro"]["estado_fila"], cero["registro"]["total_libros"]) == ("completada", 1)


@pytest.mark.parametrize(("campos", "codigo", "mensaje"), [
    ({"lsn_titulo": "El zorro"}, "lsn_incompleto", MENSAJE_LSN_INCOMPLETO),
    ({"lsn_titulo": "   ", "lsn_nota": 3, "lsn_maximo": 5}, "lsn_incompleto", MENSAJE_LSN_INCOMPLETO),
    ({"lsn_nota": 3, "lsn_maximo": 5}, "lsn_incompleto", MENSAJE_LSN_INCOMPLETO),
    ({"lsn_titulo": "A", "lsn_nota": 3, "lsn_maximo": 7}, "lsn_invalido", MENSAJE_LSN_INVALIDO),
    ({"lsn_titulo": "A", "lsn_nota": 6, "lsn_maximo": 5}, "lsn_invalido", MENSAJE_LSN_INVALIDO),
    ({"lsn_titulo": "A", "lsn_nota": -1, "lsn_maximo": 5}, "lsn_invalido", MENSAJE_LSN_INVALIDO),
])
def test_fila_lsn_todo_o_nada_y_rango(client, db, entorno, doc, tabla, campos, codigo, mensaje):
    [item] = guardar(client, doc, tabla["id"], fila(entorno, **campos)).json()

    assert (item["estado"], item["motivo"], item["detail"]) == ("rechazada", codigo, mensaje)


@pytest.mark.parametrize("cantidad", [-1, 2.5, "dos"])
def test_fila_cantidad_lsl_invalida_es_422(client, db, entorno, doc, tabla, cantidad):
    respuesta = guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=cantidad))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


@pytest.mark.parametrize("estado", [None, "ausente"])
def test_fila_lectura_exige_presente_o_tardanza_pero_la_observacion_no(client, db, entorno, doc, tabla, estado):
    asistencia = db.exec(select(Asistencia)).one()
    if estado is None:
        db.delete(asistencia)
    else:
        asistencia.estado = estado
        db.add(asistencia)
    db.commit()

    [lectura] = guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=2)).json()
    [observacion] = guardar(client, doc, tabla["id"], fila(entorno, observacion="Enfermo")).json()

    assert (lectura["estado"], lectura["motivo"], lectura["detail"]) == ("rechazada", "sin_asistencia", MENSAJE_SIN_ASISTENCIA)
    assert observacion["estado"] == "aceptada"
    assert observacion["registro"]["estado_fila"] == ("pendiente" if estado is None else "no_aplica")


def test_fila_tardanza_permite_registrar(client, db, entorno, doc, tabla):
    asistencia = db.exec(select(Asistencia)).one()
    asistencia.estado = "tardanza"
    db.add(asistencia)
    db.commit()

    [item] = guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1)).json()

    assert item["registro"]["estado_fila"] == "completada"


def test_fila_revisar_asistencia_si_paso_a_ausente(client, db, entorno, doc, tabla):
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=2))
    asistencia = db.exec(select(Asistencia)).one()
    asistencia.estado = "ausente"
    db.add(asistencia)
    db.commit()

    [actual] = consultar(client, doc, db, entorno).json()["filas"]
    [borrar] = guardar(client, doc, tabla["id"], fila(entorno, version=2, cantidad_lsl=None)).json()

    assert (actual["estado_fila"], actual["revisar_asistencia"], actual["cantidad_lsl"]) == ("no_aplica", True, 2)
    assert (borrar["estado"], borrar["registro"]["revisar_asistencia"]) == ("aceptada", False)


def test_fila_alumno_fuera_de_la_tabla(client, db, entorno, doc, tabla):
    [beto] = guardar(client, doc, tabla["id"], fila(entorno, alumno="beto", cantidad_lsl=1)).json()

    assert (beto["motivo"], beto["detail"]) == (
        "alumno_fuera_de_la_tabla", "El estudiante no corresponde a la clase de referencia de esta tabla semanal.",
    )


def test_fila_conflicto_de_version_no_sobrescribe(client, db, entorno, doc, tabla):
    primera = guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=2)).json()[0]
    segunda = guardar(client, doc, tabla["id"], fila(entorno, version=1, cantidad_lsl=5)).json()[0]

    assert primera["estado"] == "aceptada"
    assert (segunda["estado"], segunda["motivo"]) == ("rechazada", "modificacion_concurrente")
    assert (segunda["registro"]["cantidad_lsl"], segunda["registro"]["version"]) == (2, 2)
    db.expire_all()
    assert db.exec(select(LecturaSemanalAlumno)).one().cantidad_lsl == 2


def test_fila_sin_cambios_no_escribe_ni_audita(client, db, entorno, doc, tabla):
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=0))
    antes = len(db.exec(select(Auditoria)).all())

    [igual] = guardar(client, doc, tabla["id"], fila(entorno, version=1, cantidad_lsl=0, observacion="  ")).json()

    assert (igual["estado"], igual["registro"]["version"]) == ("aceptada", 2)
    assert len(db.exec(select(Auditoria)).all()) == antes


def test_fila_sin_actividad_y_roles(client, db, entorno, cabeceras, tabla, actividad):
    doc = cabeceras["Docente"]
    client.post(f"/actividades/{actividad['id']}/finalizar", json={}, headers=doc)
    [sin_actividad] = guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1)).json()

    assert (sin_actividad["motivo"], sin_actividad["detail"]) == (
        "actividad_requerida", "Debes iniciar una actividad de trabajo para registrar o modificar información.",
    )
    assert guardar(client, cabeceras["Supervisor"], tabla["id"], fila(entorno)).status_code == 403
    assert guardar(client, cabeceras["Directivo"], tabla["id"], fila(entorno)).status_code == 403
    assert guardar(client, doc, 999, fila(entorno)).status_code == 404


def test_fila_con_permiso_y_sin_habilitacion(client, db, entorno, doc, actividad):
    """En un PA pasado se escribe con el permiso de corrección (queda en la fila); revocado
    el permiso, se rechaza con el mensaje del CU."""
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º")
    creada = crear(client, doc, db, entorno, fecha=LUNES_DEL_PA1).json()
    registrar_asistencia(db, entorno.alumnos["ana"], LUNES_DEL_PA1, "presente")

    [con_permiso] = guardar(client, doc, creada["id"], fila(entorno, cantidad_lsl=1)).json()
    permiso.revocado_en, permiso.revocado_por = permiso.inicio, entorno.supervisor.id_usuario
    db.add(permiso)
    db.commit()
    [sin_habilitacion] = guardar(client, doc, creada["id"], fila(entorno, version=2, cantidad_lsl=2)).json()

    assert con_permiso["estado"] == "aceptada"
    db.expire_all()
    assert db.exec(select(LecturaSemanalAlumno)).one().id_permiso == permiso.id_permiso
    assert (sin_habilitacion["motivo"], sin_habilitacion["detail"]) == (
        "sin_habilitacion", "No tienes una habilitación vigente para registrar o corregir la lectura de esta semana.",
    )


# ── Cambiar la clase de referencia (CU024) ──────────────────────────────────────────

def test_cambiar_clase_de_referencia(client, db, entorno, doc, tabla):
    ana, beto = entorno.alumnos["ana"], entorno.alumnos["beto"]
    adicional = clase_guardada(db, ana, MARTES, lista=[ana, beto], tipo="adicional")
    db.commit()
    url = f"/lectura/tablas/{tabla['id']}/clase-referencia"

    vieja = client.patch(url, json={"id_operacion": str(uuid.uuid4()), "version": 0, "id_clase": adicional.id_clase},
                         headers=doc)
    cambio = client.patch(url, json={"id_operacion": str(uuid.uuid4()), "version": 1, "id_clase": adicional.id_clase},
                          headers=doc)

    assert (vieja.status_code, motivo(vieja)) == (409, "modificacion_concurrente")
    assert cambio.status_code == 200, cambio.text
    assert (cambio.json()["clase_referencia"]["id_clase"], cambio.json()["version"]) == (adicional.id_clase, 2)
    # La asistencia ahora es la de la adicional (sin marcar).
    assert [(f["nombre"], f["asistencia"]) for f in cambio.json()["filas"]] == [("Ana Torres", None)]


def test_cambiar_clase_bloqueado_con_datos_o_no_realizada(client, db, entorno, cabeceras, tabla):
    doc = cabeceras["Docente"]
    url = f"/lectura/tablas/{tabla['id']}/clase-referencia"
    adicional = clase_guardada(db, entorno.alumnos["ana"], MARTES, tipo="adicional")
    db.commit()
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1))

    con_datos = client.patch(url, json={"id_operacion": str(uuid.uuid4()), "version": 1, "fecha": MARTES.isoformat()},
                             headers=doc)
    supervisor = client.patch(url, json={"id_operacion": str(uuid.uuid4()), "version": 1,
                                         "id_clase": adicional.id_clase}, headers=cabeceras["Supervisor"])
    ninguna = client.patch(url, json={"id_operacion": str(uuid.uuid4()), "version": 1}, headers=doc)

    assert (con_datos.status_code, motivo(con_datos)) == (409, "tabla_con_datos")
    assert supervisor.status_code == 403
    assert (ninguna.status_code, motivo(ninguna)) == (422, "validacion")


# ── No realizada (CU026) ────────────────────────────────────────────────────────────

def marcar(client, cabecera, id_tabla, version=1, motivo_=""):
    return client.post(f"/lectura/tablas/{id_tabla}/no-realizada",
                       json={"id_operacion": str(uuid.uuid4()), "version": version, "motivo": motivo_},
                       headers=cabecera)


def test_no_realizada_bloquea_lectura_y_observaciones(client, db, entorno, cabeceras, tabla):
    doc = cabeceras["Docente"]
    vacio = marcar(client, doc, tabla["id"], motivo_="   ")
    marcada = marcar(client, doc, tabla["id"], motivo_="Paro de transportistas")

    assert (vacio.status_code, vacio.json()["detail"]) == (
        422, "Es obligatorio registrar una justificación para indicar por qué no se realizó la lectura semanal.",
    )
    assert marcada.status_code == 200, marcada.text
    cuerpo = marcada.json()
    assert (cuerpo["estado"], cuerpo["motivo_no_realizada"], cuerpo["version"]) == (
        "no_realizada", "Paro de transportistas", 2,
    )
    assert [f["estado_fila"] for f in cuerpo["filas"]] == ["no_aplica"]
    [observacion] = guardar(client, doc, tabla["id"], fila(entorno, observacion="x")).json()
    assert (observacion["estado"], observacion["motivo"]) == ("rechazada", "lectura_no_realizada")
    cambio = client.patch(f"/lectura/tablas/{tabla['id']}/clase-referencia",
                          json={"id_operacion": str(uuid.uuid4()), "version": 2, "fecha": LUNES.isoformat()},
                          headers=doc)
    assert (cambio.status_code, motivo(cambio)) == (409, "lectura_no_realizada")
    assert marcar(client, cabeceras["Supervisor"], tabla["id"], 2, "x").status_code == 403


def test_no_realizada_con_datos_es_409_pero_las_observaciones_no_cuentan(client, db, entorno, doc, tabla):
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1, observacion="Bien"))
    con_datos = marcar(client, doc, tabla["id"], motivo_="Feria")
    guardar(client, doc, tabla["id"], fila(entorno, version=2, cantidad_lsl=None))

    con_observacion = marcar(client, doc, tabla["id"], motivo_="Feria")

    assert con_observacion.status_code == 200
    assert (con_datos.status_code, motivo(con_datos), con_datos.json()["detail"]) == (
        409, "tabla_con_datos",
        "No es posible marcar la lectura semanal como no realizada porque ya existen registros de lectura guardados "
        "para esta tabla.",
    )


def test_no_realizada_sin_actividad(client, db, entorno, cabeceras, tabla, actividad):
    doc = cabeceras["Docente"]
    client.post(f"/actividades/{actividad['id']}/finalizar", json={}, headers=doc)

    respuesta = marcar(client, doc, tabla["id"], motivo_="Paro")

    assert (respuesta.status_code, respuesta.json()["detail"]) == (
        422, "Debes iniciar una actividad de trabajo para registrar o modificar información.",
    )


# ── /semanas ────────────────────────────────────────────────────────────────────────

def semanas(client, cabecera, db, entorno, mes, grado="2.º"):
    params = {k: v for k, v in filtro(db, entorno, grado=grado).items() if k in ("id_colegio", "id_grado", "id_seccion")}
    for lunes in (date(2026, 6, 1), LUNES_ANTERIOR, LUNES, LUNES_SIGUIENTE, date(2026, 6, 29)):
        semana_de(db, lunes)
    db.commit()
    params.update(id_anio_escolar=entorno.anio.id_anio_escolar, mes=mes)
    return client.get("/semanas", params=params, headers=cabecera)


def test_semanas_con_clase_del_mes(client, db, entorno, doc):
    agregar_no_lectivo(db, entorno.calendario, LUNES_ANTERIOR)

    junio = semanas(client, doc, db, entorno, 6).json()
    julio = semanas(client, doc, db, entorno, 7).json()

    assert [(s["fecha_inicio"], s["cerrada"]) for s in junio] == [
        ("2026-06-01", True), ("2026-06-15", False), ("2026-06-22", False), ("2026-06-29", False),
    ]
    assert set(junio[0]) == {"id", "id_semana", "numero_semana", "fecha_inicio", "fecha_fin", "cerrada"}
    # La semana del 29/06 al 03/07 tiene su clase en junio: no aparece en julio.
    assert "2026-06-29" not in [s["fecha_inicio"] for s in julio]


def test_semanas_alcance_y_anio(client, db, entorno, doc, cabeceras):
    lejano = semanas(client, doc, db, entorno, 6)
    params = {**lejano.request.url.params, "id_colegio": entorno.colegios["Lejano"].id_colegio}
    fuera = client.get("/semanas", params=params, headers=doc)
    sin_anio = client.get("/semanas", params={**lejano.request.url.params, "id_anio_escolar": 999}, headers=doc)

    assert (fuera.status_code, motivo(fuera)) == (403, "aula_fuera_de_alcance")
    assert (sin_anio.status_code, motivo(sin_anio)) == (404, "anio_no_encontrado")
    assert client.get("/semanas", params=dict(lejano.request.url.params), headers=cabeceras["Supervisor"]).json()
    directivo = client.get("/semanas", params=dict(lejano.request.url.params), headers=cabeceras["Directivo"])
    assert (directivo.status_code, motivo(directivo)) == (403, "sin_permiso")


# ── Integraciones ───────────────────────────────────────────────────────────────────

def test_salud_cuenta_completadas_y_no_aplica_del_pa_vigente(client, db, entorno, doc, tabla):
    pa = pa_actual_de_colegios(db, solo_activos=True)
    assert lectura_service.filas_confirmadas(db, pa) == 0
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1))
    alfabetizacion = crear(client, doc, db, entorno, programa=ALFABETIZACION).json()
    marcar(client, doc, alfabetizacion["id"], motivo_="Paro")

    assert lectura_service.filas_confirmadas(db, pa) == 2


def test_productividad_y_calendario(client, db, entorno, doc, tabla, actividad):
    guardar(client, doc, tabla["id"], fila(entorno, cantidad_lsl=1))
    conteos = cambios.cambios_por_actividad(db, [uuid.UUID(actividad["id"])])
    registros = _registros(db, {entorno.colegios["Andino"].id_colegio}, entorno.anio.id_anio_escolar)

    assert conteos[uuid.UUID(actividad["id"])] == {"seguimiento_lectura": 2}
    assert ("lectura", LUNES) in {(r.tipo, r.fecha) for r in registros}
