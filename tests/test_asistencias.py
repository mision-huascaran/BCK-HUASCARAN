"""Asistencia por clase (CU022), su % en el detalle del alumno (CU015), el hook de listas
históricas (CU014) y el contador de Salud del Sistema de Asistencias (CU012)."""
import uuid
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.evaluacion import Asistencia, Clase, ClaseAlumno
from app.models.organizacion import AlumnoPertenencia, ColegioGrado, DocenteColegioGrado
from app.models.trazabilidad import Auditoria, OperacionSincronizacion
from app.services import pertenencia, salud_sistema
from app.services.calendario import pa_actual_de_colegios
from app.core.errores import ErrorNegocio
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    asignar,
    cabecera_de,
    clase_guardada,
    crear_alumno,
    crear_docente,
    crear_particular,
    crear_permiso,
    iniciar_actividad,
    iso,
    motivo,
    rangos_en_receso,
    registrar_asistencia,
)

# Miércoles. El PA2 del general va del lunes 18 de mayo al viernes 17 de julio; el PA1,
# del 18 de enero al 18 de abril; el PR1, del 19 de abril al 17 de mayo.
HOY = date(2026, 6, 17)
LUNES, MARTES = date(2026, 6, 15), date(2026, 6, 16)
LUNES_ANTERIOR = date(2026, 6, 8)
SABADO, FERIADO, MANANA = date(2026, 6, 13), date(2026, 6, 12), date(2026, 6, 18)
LUNES_DEL_PA1 = date(2026, 4, 13)
EN_RECESO = date(2026, 5, 5)
FUERA_DE_PERIODO = date(2026, 1, 5)
SIN_HABILITACION = (
    "No tienes una habilitación vigente para registrar o corregir asistencias en la fecha seleccionada."
)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    """Fija el reloj antes de armar el entorno (el calendario es relativo a hoy)."""
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture(autouse=True)
def calendario(miercoles, db, entorno):
    """El viernes 12 es feriado; 1.º y 2.º del Andino y 3.º del Lejano tienen clase los lunes."""
    agregar_no_lectivo(db, entorno.calendario, FERIADO)
    for colegio, grado in (("Andino", "1.º"), ("Andino", "2.º"), ("Lejano", "3.º")):
        fila = db.get(ColegioGrado, (entorno.colegios[colegio].id_colegio, entorno.grados[grado]))
        fila.dia_clase = 1
        db.add(fila)
    db.commit()


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


def grupo(db, entorno, colegio="Andino", grado="2.º") -> dict:
    alumno = next(a for a in entorno.alumnos.values()
                  if a.id_colegio == entorno.colegios[colegio].id_colegio and a.id_grado == entorno.grados[grado])
    return {"id_colegio": alumno.id_colegio, "id_grado": alumno.id_grado, "id_seccion": alumno.id_seccion}


def consultar(client, cabecera, db, entorno, fecha, **grupo_):
    params = {**grupo(db, entorno, **grupo_), "fecha": fecha.isoformat()}
    return client.get("/asistencias", params=params, headers=cabecera)


def registro(entorno, alumno="ana", estado="presente", **campos) -> dict:
    return {"id_operacion": str(uuid.uuid4()), "version": None, "id_alumno": entorno.alumnos[alumno].id_alumno,
            "estado": estado, **campos}


def guardar(client, cabecera, db, entorno, fecha, registros, grupo_=None, **extra):
    cuerpo = {**grupo(db, entorno, **(grupo_ or {})), "fecha": fecha.isoformat(), "registros": registros, **extra}
    return client.put("/asistencias", json=cuerpo, headers=cabecera)


def reprogramar(client, cabeceras, db, entorno, original, nueva, **grupo_):
    datos = grupo(db, entorno, **grupo_)
    cuerpo = {"id_grado": datos["id_grado"], "id_seccion": datos["id_seccion"], "fecha_original": original.isoformat(),
              "fecha_nueva": nueva.isoformat(), "motivo": "Actuación"}
    respuesta = client.post(f"/colegios/{datos['id_colegio']}/clases/reprogramaciones", json=cuerpo,
                            headers=cabeceras["Supervisor"])
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def filas(db) -> list[Asistencia]:
    db.expire_all()
    return db.exec(select(Asistencia).order_by(Asistencia.id)).all()


# ── GET: fecha ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("fecha", "codigo", "mensaje"), [
    (MANANA, "fecha_futura", "No se pueden registrar asistencias para fechas futuras."),
    (EN_RECESO, "fecha_en_receso", "La fecha seleccionada corresponde a un periodo de receso."),
    (FERIADO, "fecha_no_lectiva", "Día no lectivo: Feriado. No se permite registrar asistencias para esta fecha."),
    (SABADO, "fecha_no_lectiva", "Día no lectivo: fin de semana. No se permite registrar asistencias para esta fecha."),
    (FUERA_DE_PERIODO, "fecha_fuera_de_periodo", "La fecha seleccionada no pertenece a un periodo académico configurado."),
])
def test_get_valida_la_fecha_con_el_calendario_del_colegio(client, db, entorno, cabeceras, fecha, codigo, mensaje):
    respuesta = consultar(client, cabeceras["Supervisor"], db, entorno, fecha)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (422, codigo, mensaje)


def test_get_roles_y_alcance(client, db, entorno, cabeceras):
    assert consultar(client, {}, db, entorno, LUNES).status_code == 401
    assert consultar(client, cabeceras["Directivo"], db, entorno, LUNES).status_code == 403
    ajeno = consultar(client, cabeceras["Docente"], db, entorno, LUNES, colegio="Lejano", grado="3.º")
    assert (ajeno.status_code, motivo(ajeno)) == (403, "aula_fuera_de_alcance")


# ── GET: la clase ───────────────────────────────────────────────────────────────────

def test_get_clase_programada_con_la_lista_del_grupo(client, db, entorno, doc):
    cuerpo = consultar(client, doc, db, entorno, LUNES).json()

    assert cuerpo["clase"] == {"id_clase": None, "tipo": "habitual", "etiqueta": "Clase programada",
                               "fecha_original": LUNES.isoformat(), "fecha": LUNES.isoformat(), "aviso": None}
    assert cuerpo["alumnos"] == [{
        "id_alumno": entorno.alumnos["ana"].id_alumno, "nombre": "Ana Torres",
        "id_programa": entorno.programas["Comprensión Lectora"], "inactivo": False, "estado": None,
        "observacion": None, "version": None,
    }]


def test_lista_sin_distinguir_mayusculas_ni_tildes(client, db, entorno, doc, actividad):
    """La lista del grupo (sin clase guardada) y la lista congelada salen en el mismo orden."""
    for nombre, apellidos in (("Zoe", "Ñahui"), ("Iris", "álvarez"), ("Abel", "ALVA"), ("Bea", "Ávila")):
        crear_alumno(db, nombre, apellidos, entorno.colegios["Andino"], entorno.grados["2.º"],
                     entorno.programas["Comprensión Lectora"], 1, desde=entorno.anio.fecha_inicio)
    db.commit()
    esperado = ["Abel ALVA", "Iris álvarez", "Bea Ávila", "Zoe Ñahui", "Ana Torres"]

    del_grupo = [a["nombre"] for a in consultar(client, doc, db, entorno, LUNES).json()["alumnos"]]
    assert guardar(client, doc, db, entorno, LUNES, [registro(entorno)]).status_code == 200
    congelada = [a["nombre"] for a in consultar(client, doc, db, entorno, LUNES).json()["alumnos"]]

    assert (del_grupo, congelada) == (esperado, esperado)


def test_get_sin_clase_programada(client, db, entorno, doc):
    clase = consultar(client, doc, db, entorno, MARTES).json()["clase"]

    assert (clase["etiqueta"], clase["tipo"], clase["aviso"]) == (None, None, "No hay una clase programada para esta fecha.")


def test_get_clase_reprogramada_y_su_fecha_original(client, db, entorno, cabeceras, doc):
    reprogramar(client, cabeceras, db, entorno, LUNES, MARTES)

    trasladada = consultar(client, doc, db, entorno, MARTES).json()["clase"]
    original = consultar(client, doc, db, entorno, LUNES).json()["clase"]

    assert (trasladada["etiqueta"], trasladada["fecha_original"], trasladada["fecha"]) == (
        "Clase reprogramada", LUNES.isoformat(), MARTES.isoformat())
    assert trasladada["aviso"] == "Esta clase estaba programada para el 15/06/2026 y fue trasladada al 16/06/2026."
    assert original["etiqueta"] is None
    assert original["aviso"] == (
        "No hay una clase programada para esta fecha. La clase prevista para esta fecha fue trasladada al "
        "16/06/2026. Registrar una clase adicional no completa la asistencia de la clase trasladada."
    )


def test_get_clase_adicional(client, db, entorno, doc, actividad):
    guardar(client, doc, db, entorno, MARTES, [registro(entorno)], adicional=True)

    clase = consultar(client, doc, db, entorno, MARTES).json()["clase"]

    assert (clase["tipo"], clase["etiqueta"], clase["aviso"]) == ("adicional", "Clase adicional", None)


def test_get_lista_congelada_no_cambia_con_la_pertenencia(client, db, entorno, doc, actividad):
    """Después de la primera asistencia, un alumno que entra al grupo no aparece en esa
    clase, y uno de la lista que hoy está inactivo sigue (con `inactivo`)."""
    guardar(client, doc, db, entorno, LUNES, [registro(entorno)])
    nora = crear_alumno(db, "Nora", "Zea", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Alfabetización"], 1, desde=LUNES_ANTERIOR)
    ana = entorno.alumnos["ana"]
    ana.activo = False
    db.add(ana)
    db.commit()

    congelada = consultar(client, doc, db, entorno, LUNES).json()["alumnos"]
    calculada = consultar(client, doc, db, entorno, LUNES_ANTERIOR).json()["alumnos"]

    assert [(a["nombre"], a["inactivo"], a["estado"]) for a in congelada] == [("Ana Torres", True, "presente")]
    assert {a["id_alumno"] for a in calculada} == {ana.id_alumno, nora.id_alumno}


# ── GET: escritura ──────────────────────────────────────────────────────────────────

def test_escritura_con_asignacion_y_actividad(client, db, entorno, doc):
    sin_actividad = consultar(client, doc, db, entorno, LUNES).json()["escritura"]
    iniciar_actividad(client, doc)
    con_actividad = consultar(client, doc, db, entorno, LUNES).json()["escritura"]

    assert (sin_actividad["permitida"], sin_actividad["motivo"]) == (
        False, "Debes iniciar una actividad de trabajo para registrar o modificar información.")
    assert con_actividad == {"permitida": True, "motivo": None, "id_permiso": None, "aviso_permiso": None}


def test_escritura_supervisor_siempre_solo_lectura(client, db, entorno, cabeceras):
    escritura = consultar(client, cabeceras["Supervisor"], db, entorno, LUNES).json()["escritura"]

    assert escritura["permitida"] is False


def test_escritura_sin_habilitacion_en_un_pa_pasado(client, db, entorno, doc, actividad):
    escritura = consultar(client, doc, db, entorno, LUNES_DEL_PA1).json()["escritura"]

    assert escritura == {"permitida": False, "motivo": SIN_HABILITACION, "id_permiso": None, "aviso_permiso": None}


def test_escritura_con_permiso_de_correccion(client, db, entorno, doc, actividad):
    fin = a_las(HOY, 18, 30)
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="asistencias", fin=fin)

    escritura = consultar(client, doc, db, entorno, LUNES_DEL_PA1).json()["escritura"]

    assert escritura == {
        "permitida": True, "motivo": None, "id_permiso": permiso.id_permiso,
        "aviso_permiso": "Corrección excepcional habilitada para PA1 hasta el 17/06/2026 a las 18:30.",
    }


def test_escritura_con_asignacion_retroactiva(client, db, entorno):
    crear_particular(db, entorno, rangos_en_receso(HOY))
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    fila = db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == otro.id_docente)).one()
    fila.retroactiva = True
    db.commit()
    cabecera = cabecera_de(db, otro)
    iniciar_actividad(client, cabecera)

    escritura = consultar(client, cabecera, db, entorno, LUNES, colegio="Lejano", grado="3.º").json()["escritura"]
    guardado = guardar(client, cabecera, db, entorno, LUNES, [registro(entorno, "mario")],
                       grupo_={"colegio": "Lejano", "grado": "3.º"}).json()

    assert escritura["permitida"] is True
    assert guardado["resultados"][0]["estado"] == "aceptada"


# ── PUT ─────────────────────────────────────────────────────────────────────────────

def test_put_crea_la_clase_y_congela_la_lista(client, db, entorno, doc, actividad):
    respuesta = guardar(client, doc, db, entorno, LUNES, [registro(entorno)])

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    [resultado] = cuerpo["resultados"]
    assert (resultado["estado"], resultado["registro"]["estado"], resultado["registro"]["version"]) == (
        "aceptada", "presente", 1)
    db.expire_all()
    clase = db.get(Clase, cuerpo["clase"]["id_clase"])
    assert (clase.tipo, clase.fecha, clase.lista_congelada_en is not None) == ("habitual", LUNES, True)
    assert [c.id_alumno for c in db.exec(select(ClaseAlumno)).all()] == [entorno.alumnos["ana"].id_alumno]
    [fila] = filas(db)
    assert (str(fila.id_actividad), fila.id_permiso) == (actividad["id"], None)


def test_put_por_id_clase(client, db, entorno, doc, actividad):
    id_clase = guardar(client, doc, db, entorno, LUNES, [registro(entorno)]).json()["clase"]["id_clase"]

    respuesta = client.put("/asistencias", json={"id_clase": id_clase, "registros": [registro(entorno, estado="ausente")]},
                           headers=doc)

    assert respuesta.json()["resultados"][0]["registro"]["estado"] == "ausente"
    assert filas(db)[0].version == 2


def test_put_adicional_exige_la_marca(client, db, entorno, doc, actividad):
    sin_marca = guardar(client, doc, db, entorno, MARTES, [registro(entorno)]).json()
    con_marca = guardar(client, doc, db, entorno, MARTES, [registro(entorno)], adicional=True).json()

    assert sin_marca["resultados"][0]["motivo"] == "sin_clase_programada"
    assert con_marca["resultados"][0]["estado"] == "aceptada"
    assert (con_marca["clase"]["tipo"], con_marca["clase"]["etiqueta"]) == ("adicional", "Clase adicional")


def test_put_idempotente_por_id_de_operacion(client, db, entorno, doc, actividad):
    marca = registro(entorno)

    primera = guardar(client, doc, db, entorno, LUNES, [marca]).json()
    segunda = guardar(client, doc, db, entorno, LUNES, [{**marca, "estado": "ausente"}]).json()

    assert primera["resultados"] == segunda["resultados"]
    assert [f.estado for f in filas(db)] == ["presente"]
    assert len(db.exec(select(Auditoria).where(Auditoria.tabla == "asistencia")).all()) == 1


def test_put_gana_la_captura_mas_reciente(client, db, entorno, doc, actividad, miercoles):
    miercoles.avanzar(minutes=30)
    reciente = registro(entorno, realizado_en=iso(a_las(HOY, 9, 20)))
    antigua = registro(entorno, estado="ausente", realizado_en=iso(a_las(HOY, 9, 10)))

    guardar(client, doc, db, entorno, LUNES, [reciente])
    [resultado] = guardar(client, doc, db, entorno, LUNES, [antigua]).json()["resultados"]

    assert (resultado["estado"], resultado["motivo"], resultado["registro"]["estado"]) == (
        "aceptada", "registro_mas_reciente", "presente")
    assert [f.estado for f in filas(db)] == ["presente"]
    operacion = db.get(OperacionSincronizacion, uuid.UUID(antigua["id_operacion"]))
    assert operacion.estado == "descartada"


def test_put_version_es_informativa(client, db, entorno, doc, actividad):
    guardar(client, doc, db, entorno, LUNES, [registro(entorno)])

    [resultado] = guardar(client, doc, db, entorno, LUNES, [registro(entorno, "ana", "tardanza", version=7)]).json()[
        "resultados"]

    assert (resultado["estado"], resultado["registro"]["estado"]) == ("aceptada", "tardanza")


def test_put_alumno_fuera_de_la_clase(client, db, entorno, doc, actividad):
    [resultado] = guardar(client, doc, db, entorno, LUNES, [registro(entorno, "luis")]).json()["resultados"]

    assert (resultado["estado"], resultado["motivo"]) == ("rechazada", "alumno_fuera_de_la_clase")


def test_put_rechazos_de_fecha_y_de_habilitacion(client, db, entorno, doc, actividad):
    futura = guardar(client, doc, db, entorno, MANANA, [registro(entorno)]).json()["resultados"][0]
    pasada = guardar(client, doc, db, entorno, LUNES_DEL_PA1, [registro(entorno)]).json()["resultados"][0]

    assert (futura["estado"], futura["motivo"]) == ("rechazada", "fecha_futura")
    assert (pasada["motivo"], pasada["detail"]) == ("sin_habilitacion", SIN_HABILITACION)
    assert db.exec(select(Clase)).all() == []


def test_put_guarda_el_permiso_usado(client, db, entorno, doc, actividad):
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="asistencias")

    [resultado] = guardar(client, doc, db, entorno, LUNES_DEL_PA1, [registro(entorno)]).json()["resultados"]

    assert resultado["estado"] == "aceptada"
    assert filas(db)[0].id_permiso == permiso.id_permiso
    auditoria = db.exec(select(Auditoria).where(Auditoria.tabla == "asistencia")).one()
    assert auditoria.id_permiso == permiso.id_permiso


@pytest.fixture
def permiso_revocado_9_10(db, entorno, miercoles, actividad):
    """D69: permiso de Asistencias de 8:00 a 10:00, revocado a las 9:10; el reloj, a las 9:20."""
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="asistencias",
                            revocado_por=entorno.supervisor.id_usuario, revocado_en=a_las(HOY, 9, 10),
                            motivo_revocacion="Ya no corresponde")
    miercoles.avanzar(minutes=20)
    return permiso


def test_sincronizar_operacion_previa_a_la_revocacion(client, db, entorno, doc, actividad, permiso_revocado_9_10):
    marca = registro(entorno, realizado_en=iso(a_las(HOY, 9, 5)), id_actividad=actividad["id"])

    [resultado] = guardar(client, doc, db, entorno, LUNES_DEL_PA1, [marca]).json()["resultados"]

    assert resultado["estado"] == "aceptada"
    auditoria = db.exec(select(Auditoria).where(Auditoria.tabla == "asistencia")).one()
    assert auditoria.id_permiso == permiso_revocado_9_10.id_permiso


@pytest.mark.parametrize("campos", [
    pytest.param({"realizado_en": iso(a_las(HOY, 9, 15))}, id="sin_conexion_despues"),
    pytest.param({}, id="en_linea_despues"),
])
def test_operacion_posterior_a_la_revocacion(client, db, entorno, doc, actividad, permiso_revocado_9_10, campos):
    marca = registro(entorno, id_actividad=actividad["id"], **campos)

    [resultado] = guardar(client, doc, db, entorno, LUNES_DEL_PA1, [marca]).json()["resultados"]

    assert (resultado["estado"], resultado["motivo"]) == ("rechazada", "sin_habilitacion")
    assert filas(db) == []


def test_put_observacion_sin_estado_no_completa_la_fila(client, db, entorno, doc, actividad):
    marca = {"id_operacion": str(uuid.uuid4()), "id_alumno": entorno.alumnos["ana"].id_alumno, "observacion": "Llegó"}

    [resultado] = guardar(client, doc, db, entorno, LUNES, [marca]).json()["resultados"]

    assert (resultado["estado"], resultado["registro"]["estado"], resultado["registro"]["observacion"]) == (
        "aceptada", None, "Llegó")


def test_put_sin_actividad_rechaza_cada_registro(client, db, entorno, doc):
    [resultado] = guardar(client, doc, db, entorno, LUNES, [registro(entorno)]).json()["resultados"]

    assert resultado["motivo"] == "actividad_requerida"


def test_put_roles_y_validacion(client, db, entorno, cabeceras, doc):
    assert guardar(client, {}, db, entorno, LUNES, [registro(entorno)]).status_code == 401
    supervisor = guardar(client, cabeceras["Supervisor"], db, entorno, LUNES, [registro(entorno)])
    sin_clase = client.put("/asistencias", json={"registros": [registro(entorno)]}, headers=doc)
    vacio = guardar(client, doc, db, entorno, LUNES, [])
    ajeno = guardar(client, doc, db, entorno, LUNES, [registro(entorno, "mario")], grupo_={"colegio": "Lejano", "grado": "3.º"})

    assert (supervisor.status_code, motivo(supervisor)) == (403, "sin_permiso")
    assert (sin_clase.status_code, motivo(sin_clase)) == (422, "validacion")
    assert (vacio.status_code, motivo(vacio)) == (422, "validacion")
    assert (ajeno.status_code, motivo(ajeno)) == (403, "aula_fuera_de_alcance")
    sin_cambios = guardar(client, doc, db, entorno, LUNES, [
        {"id_operacion": str(uuid.uuid4()), "version": None, "id_alumno": entorno.alumnos["ana"].id_alumno}
    ])
    assert (sin_cambios.status_code, motivo(sin_cambios)) == (422, "validacion")
    assert [e["mensaje"] for e in sin_cambios.json()["errores"]] == ["Debes indicar el estado o la observación."]


def test_put_clase_inexistente(client, doc, actividad):
    respuesta = client.put("/asistencias", json={"id_clase": 999, "registros": [{"id_operacion": str(uuid.uuid4()),
                                                                                  "id_alumno": 1, "estado": "presente"}]},
                           headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (404, "clase_no_encontrada")


def test_put_cuenta_en_la_productividad_por_clase_y_alumno(client, db, entorno, doc, actividad):
    guardar(client, doc, db, entorno, LUNES, [registro(entorno)])
    guardar(client, doc, db, entorno, LUNES, [registro(entorno, estado="ausente")])

    detalle = client.get(f"/actividades/{actividad['id']}", headers=doc).json()

    assert detalle["cambios_por_modulo"] == [
        {"modulo": "asistencia", "total": 1, "creados": 1, "editados": 1, "activados": 0, "inactivados": 0}
    ]


# ── Hook de listas históricas (CU014) ───────────────────────────────────────────────

@pytest.fixture
def lista_del_lunes(client, db, entorno, doc, actividad):
    """La clase del lunes de 2.º con su lista congelada: solo Ana."""
    guardar(client, doc, db, entorno, LUNES, [registro(entorno)])


def editar(client, cabeceras, entorno, alumno, **cambios):
    return client.patch(f"/alumnos/{entorno.alumnos[alumno].id_alumno}", json=cambios, headers=cabeceras["Supervisor"])


def test_hook_sacar_del_grupo_a_quien_figura_en_una_lista(client, db, entorno, cabeceras, lista_del_lunes):
    cambio = editar(client, cabeceras, entorno, "ana", id_grado=entorno.grados["3.º"],
                    cambio_grupo_desde=LUNES_ANTERIOR.isoformat())
    inactivar = client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/desactivar",
                             json={"aplicar_desde": LUNES.isoformat()}, headers=cabeceras["Supervisor"])
    despues = editar(client, cabeceras, entorno, "ana", id_grado=entorno.grados["3.º"], cambio_grupo_desde=HOY.isoformat())

    assert (cambio.status_code, motivo(cambio)) == (409, "conflicto_listas_historicas")
    [clase] = cambio.json()["clases"]
    assert (clase["fecha"], clase["colegio"], clase["grado"], clase["seccion"]) == (
        LUNES.isoformat(), "Colegio Andino", "2.º", "Única")
    assert (inactivar.status_code, motivo(inactivar)) == (409, "conflicto_listas_historicas")
    assert despues.status_code == 200


def test_hook_entrar_a_un_grupo_con_listas_donde_no_figura(client, db, entorno, cabeceras, lista_del_lunes):
    respuesta = editar(client, cabeceras, entorno, "luis", id_grado=entorno.grados["2.º"],
                       id_programa=entorno.programas["Comprensión Lectora"], cambio_grupo_desde=LUNES.isoformat())

    assert (respuesta.status_code, motivo(respuesta)) == (409, "conflicto_listas_historicas")


def test_hook_alta_solo_choca_con_listas_posteriores(client, db, entorno, cabeceras, lista_del_lunes):
    """Un alumno nuevo puede entrar el mismo día de una lista ya congelada (entra desde la
    próxima clase), pero no antes de ella."""
    def alta(desde):
        cuerpo = {**grupo(db, entorno), "nombres": "Nora", "apellidos": "Nueva",
                  "id_programa": entorno.programas["Comprensión Lectora"], "pertenece_desde": desde.isoformat()}
        return client.post("/alumnos", json=cuerpo, headers=cabeceras["Supervisor"])

    antes = alta(LUNES_ANTERIOR)
    mismo_dia = alta(LUNES)

    assert (antes.status_code, motivo(antes)) == (409, "conflicto_listas_historicas")
    assert mismo_dia.status_code == 201
    clase = db.exec(select(Clase).where(Clase.fecha == LUNES)).one()
    assert db.get(ClaseAlumno, (clase.id_clase, mismo_dia.json()["id"])) is None


def test_hook_inicio_de_pertenencia_posterior_a_una_lista(db, entorno, lista_del_lunes):
    ana = entorno.alumnos["ana"]
    intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == ana.id_alumno)).one()
    intervalo.desde = MARTES
    db.add(intervalo)

    with pytest.raises(ErrorNegocio) as error:
        pertenencia.validar_contra_listas_historicas(db, ana.id_alumno)

    assert error.value.motivo == "conflicto_listas_historicas"
    db.rollback()


# ── % de asistencia del alumno (CU015) ──────────────────────────────────────────────

def asistencia_de(client, cabecera, alumno) -> dict:
    return client.get(f"/alumnos/{alumno.id_alumno}/resumen", headers=cabecera).json()["asistencia"]


def test_porcentaje_excluye_listas_incompletas_y_cuenta_una_vez_la_reprogramada(client, db, entorno, cabeceras):
    ana = entorno.alumnos["ana"]
    nora = crear_alumno(db, "Nora", "Zea", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Alfabetización"], 1, desde=LUNES_ANTERIOR - timedelta(days=30))
    registrar_asistencia(db, ana, LUNES_ANTERIOR, "presente", lista=[ana, nora])
    registrar_asistencia(db, nora, LUNES_ANTERIOR, "ausente")
    registrar_asistencia(db, ana, LUNES - timedelta(days=14), "tardanza", lista=[ana, nora])  # a Nora le falta
    reprogramada = clase_guardada(db, ana, MARTES, fecha_original=LUNES)
    db.add(Asistencia(id_clase=reprogramada.id_clase, id_alumno=ana.id_alumno, estado="ausente",
                      id_docente=entorno.id_docente, fecha_registro=tiempo.ahora_utc(), creado_por=1,
                      creado_en=tiempo.ahora_utc()))
    db.commit()

    de_ana = asistencia_de(client, cabeceras["Supervisor"], ana)
    de_nora = asistencia_de(client, cabeceras["Supervisor"], nora)

    assert de_ana == {"porcentaje": 50.0, "asistidas": 1, "contabilizadas": 2}
    assert de_nora == {"porcentaje": 0.0, "asistidas": 0, "contabilizadas": 1}
    assert asistencia_de(client, cabeceras["Supervisor"], entorno.alumnos["luis"]) == {
        "porcentaje": None, "asistidas": 0, "contabilizadas": 0}


def test_porcentaje_cuenta_la_clase_adicional_completa(client, db, entorno, cabeceras, doc, actividad):
    """D100: una adicional creada por PUT /asistencias, con toda su lista marcada, cuenta."""
    respuesta = guardar(client, doc, db, entorno, MARTES, [registro(entorno)], adicional=True)
    assert respuesta.json()["clase"]["tipo"] == "adicional"
    lista = db.exec(select(ClaseAlumno).join(Clase).where(Clase.fecha == MARTES)).all()
    assert [f.id_alumno for f in lista] == [entorno.alumnos["ana"].id_alumno]

    de_ana = asistencia_de(client, cabeceras["Supervisor"], entorno.alumnos["ana"])

    assert de_ana == {"porcentaje": 100.0, "asistidas": 1, "contabilizadas": 1}


def test_porcentaje_no_cuenta_la_adicional_con_un_alumno_sin_marcar(client, db, entorno, cabeceras, doc, actividad):
    """CU015: una lista incompleta no cuenta para nadie, aunque sea de otro subprograma."""
    ana = entorno.alumnos["ana"]
    nora = crear_alumno(db, "Nora", "Zea", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Alfabetización"], 1, desde=LUNES_ANTERIOR - timedelta(days=30))
    db.commit()
    guardar(client, doc, db, entorno, MARTES, [registro(entorno)], adicional=True)

    sin_clases = {"porcentaje": None, "asistidas": 0, "contabilizadas": 0}
    assert asistencia_de(client, cabeceras["Supervisor"], ana) == sin_clases
    assert asistencia_de(client, cabeceras["Supervisor"], nora) == sin_clases


# ── Salud del Sistema (CU012) ───────────────────────────────────────────────────────

def test_salud_cuenta_alumno_por_clase_con_estado_u_observacion_del_pa_vigente(db, entorno):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES)
    registrar_asistencia(db, ana, LUNES_ANTERIOR, estado=None)
    registrar_asistencia(db, ana, LUNES_DEL_PA1)

    assert len(salud_sistema.asistencias_guardadas(db, pa_actual_de_colegios(db, solo_activos=True))) == 1
