"""Salud del Sistema por registro (CU011, CU012, v2): unidades de conteo por módulo,
clave canónica del registro, avisos con `registro`, rechazos sin resolver y descarte."""
import uuid
from datetime import date

import pytest
from sqlmodel import select

from app.models.evaluacion import LecturaSemanalAlumno, RubricaMensual
from app.models.trazabilidad import OperacionPendiente, OperacionSincronizacion
from app.schemas.sincronizacion import ItemOperacion
from app.services import claves_registro, salud_sistema
from app.services.sincronizacion import Operacion, RechazoOperacion, procesar_operacion
from tests.conftest import (
    a_las,
    crear_alumno,
    decision_de_vuelo,
    iso,
    motivo,
    registrar_asistencia,
    semana_de,
    tabla_lectura,
)

# Miércoles. PA1 del general del 18/01 al 18/04 (culminado); PA2 del 18/05 al 17/07.
HOY = date(2026, 6, 17)
LUNES, LUNES_DEL_PA1 = date(2026, 6, 15), date(2026, 4, 13)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


def conteo(db) -> tuple[int, int]:
    db.expire_all()
    return salud_sistema.conteo(db)


def datos_asistencia(alumno, fecha=LUNES) -> dict:
    return {"id_colegio": alumno.id_colegio, "id_grado": alumno.id_grado, "id_seccion": alumno.id_seccion,
            "fecha": fecha.isoformat(), "id_alumno": alumno.id_alumno}


def clave_asistencia(alumno, fecha=LUNES) -> str:
    return claves_registro.asistencia(alumno.id_colegio, alumno.id_grado, alumno.id_seccion, fecha, alumno.id_alumno)


def aviso(modulo="asistencias", registro=None, realizado_en=None, id_operacion=None) -> dict:
    cuerpo = {"id_operacion": str(id_operacion or uuid.uuid4()), "id_dispositivo": "tablet-1", "modulo": modulo,
              "realizado_en": iso(realizado_en or a_las(HOY, 8))}
    if registro is not None:
        cuerpo["registro"] = registro
    return cuerpo


def informar(client, cabecera, *avisos):
    return client.post("/sincronizacion/pendientes", json=list(avisos), headers=cabecera)


def operar(db, usuario, clave, rechazar=False, realizado_en=None, id_operacion=None):
    """Una operación de asistencias con esa clave, aceptada o rechazada."""
    def aplicar(_contexto):
        if rechazar:
            raise RechazoOperacion("prueba", "Rechazada en la prueba.")
        return {"ok": True}

    item = ItemOperacion(id_operacion=id_operacion or uuid.uuid4(), realizado_en=realizado_en or a_las(HOY, 9))
    resultado = procesar_operacion(
        db, usuario, item, aplicar, Operacion("asistencias", "asistencia", "x", clave_registro=clave),
    )
    db.commit()
    return resultado


# ── Unidades de conteo ──────────────────────────────────────────────────────────────

def test_fila_de_lectura_solo_inicializada_no_cuenta_y_con_observacion_si(db, entorno):
    tabla = tabla_lectura(db, entorno.alumnos["ana"], LUNES)
    assert conteo(db) == (0, 0)

    fila = db.exec(select(LecturaSemanalAlumno).where(LecturaSemanalAlumno.id_lectura_semanal == tabla.id)).one()
    fila.observacion = "Lee en voz alta"
    db.add(fila)
    db.commit()

    assert conteo(db) == (1, 0)


def test_tabla_no_realizada_cuenta_uno_y_sus_filas_no(db, entorno):
    tabla_lectura(db, entorno.alumnos["ana"], LUNES, estado="no_realizada", motivo="Paro", observacion="x")

    assert conteo(db) == (1, 0)
    assert salud_sistema.lecturas_guardadas(db, salud_sistema.pa_actual_de_colegios(db, solo_activos=True)) == [
        claves_registro.tabla_lectura(*(getattr(entorno.alumnos["ana"], c) for c in (
            "id_colegio", "id_grado", "id_seccion", "id_programa_actual")), semana_de(db, LUNES).id_semana),
    ]


def test_rubrica_incompleta_y_vuelo_sin_nivel_cuentan_como_sincronizados(db, entorno):
    ana = entorno.alumnos["ana"]
    db.add(RubricaMensual(
        id_alumno=ana.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=6, id_colegio=ana.id_colegio,
        id_grado=ana.id_grado, id_seccion=ana.id_seccion, id_programa=ana.id_programa_actual,
        id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico, id_docente=entorno.id_docente,
        fecha_registro=a_las(HOY), creado_por=1, creado_en=a_las(HOY),
    ))
    db.commit()
    decision_de_vuelo(db, ana, entorno.periodos["vigente"], None)

    assert conteo(db) == (2, 0)
    assert salud_sistema.salud_sistema(db) == 100


def test_asistencia_de_20_alumnos_son_20_registros(db, entorno):
    ana = entorno.alumnos["ana"]
    colegio = entorno.colegios["Andino"]
    alumnos = [ana] + [
        crear_alumno(db, f"Alumno{n}", "Lista", colegio, ana.id_grado, ana.id_programa_actual, 1,
                     desde=entorno.anio.fecha_inicio)
        for n in range(19)
    ]
    for alumno in alumnos:
        registrar_asistencia(db, alumno, LUNES)
    registrar_asistencia(db, ana, LUNES_DEL_PA1)  # PA culminado: no cuenta

    assert conteo(db) == (20, 0)


# ── Pendientes por registro ─────────────────────────────────────────────────────────

def test_tres_avisos_del_mismo_registro_son_un_pendiente(client, db, entorno, doc):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES)
    assert conteo(db) == (1, 0)

    respuesta = informar(client, doc, *(aviso(registro=datos_asistencia(ana), realizado_en=a_las(HOY, h))
                                        for h in (6, 7, 8)))

    assert respuesta.status_code in (200, 201), respuesta.text
    # El registro guardado con modificaciones pendientes sale del numerador.
    assert conteo(db) == (0, 1)
    assert salud_sistema.salud_sistema(db) == 0


def test_la_operacion_aceptada_borra_los_avisos_anteriores_de_su_registro(client, db, entorno, doc, actividad, miercoles):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES)
    posterior = aviso(registro=datos_asistencia(ana), realizado_en=a_las(HOY, 9, 30))
    informar(client, doc, *(aviso(registro=datos_asistencia(ana), realizado_en=a_las(HOY, h)) for h in (6, 7)),
             posterior)

    operar(db, entorno.docente, clave_asistencia(ana), realizado_en=a_las(HOY, 8))

    # Quedan solo los avisos posteriores a la operación confirmada.
    db.expire_all()
    assert [str(p.id_operacion) for p in db.exec(select(OperacionPendiente)).all()] == [posterior["id_operacion"]]
    assert conteo(db) == (0, 1)
    miercoles.fijar(a_las(HOY, 10))
    operar(db, entorno.docente, clave_asistencia(ana), realizado_en=a_las(HOY, 9, 45))
    assert conteo(db) == (1, 0)


def test_rechazo_sin_resolver_cuenta_como_pendiente_hasta_descartar(client, db, entorno, doc, actividad):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES)

    rechazada = operar(db, entorno.docente, clave_asistencia(ana), rechazar=True)
    assert conteo(db) == (0, 1)

    descarte = client.post(f"/sincronizacion/operaciones/{rechazada.id_operacion}/descartar", headers=doc)
    assert descarte.status_code == 200, descarte.text
    assert conteo(db) == (1, 0)


def test_rechazo_resuelto_por_una_aceptada_posterior(db, entorno, actividad, miercoles):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES)
    operar(db, entorno.docente, clave_asistencia(ana), rechazar=True)
    miercoles.avanzar(minutes=5)

    operar(db, entorno.docente, clave_asistencia(ana), realizado_en=a_las(HOY, 9, 5))

    assert conteo(db) == (1, 0)


def test_suma_cero_es_null_y_solo_sincronizados_es_100(db, entorno):
    assert salud_sistema.salud_sistema(db) is None

    registrar_asistencia(db, entorno.alumnos["ana"], LUNES)

    assert salud_sistema.salud_sistema(db) == 100


def test_aviso_sin_registro_cuenta_por_id_operacion(client, db, entorno, doc):
    informar(client, doc, aviso(), aviso(), aviso(modulo="alumnos"))

    # Dos avisos sin clave de los módulos operativos (el de Alumnos no cuenta).
    assert conteo(db) == (0, 2)


@pytest.mark.parametrize(("modulo", "registro"), [
    ("asistencias", {"id_colegio": 1, "id_grado": 1, "id_seccion": 1, "id_alumno": 1}),  # sin fecha
    ("rubrica", {"id_alumno": 1, "id_anio_escolar": 1, "mes": 13}),
    ("registro_vuelo", {"id_alumno": 1}),
    ("lectura", {"id_colegio": "uno", "id_grado": 1, "id_seccion": 1, "id_programa": 1, "id_semana": 1}),
    ("alumnos", {"id_alumno": 1}),
])
def test_aviso_con_registro_incompleto_es_422(client, db, entorno, doc, modulo, registro):
    respuesta = informar(client, doc, aviso(modulo=modulo, registro=registro))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert db.exec(select(OperacionPendiente)).all() == []


def test_aviso_con_registro_guarda_la_clave_y_es_idempotente(client, db, entorno, doc):
    ana = entorno.alumnos["ana"]
    repetido = aviso(registro=datos_asistencia(ana))

    informar(client, doc, repetido)
    informar(client, doc, repetido)

    db.expire_all()
    [guardado] = db.exec(select(OperacionPendiente)).all()
    assert guardado.clave_registro == clave_asistencia(ana)


@pytest.mark.parametrize(("registro", "clave"), [
    ({"id_colegio": 1, "id_grado": 2, "id_seccion": 3, "id_programa": 4, "id_semana": 5, "id_alumno": 6},
     "lectura:1-2-3-4-5:6"),
    ({"id_colegio": 1, "id_grado": 2, "id_seccion": 3, "id_programa": 4, "id_semana": 5}, "lectura_tabla:1-2-3-4-5"),
])
def test_claves_de_lectura_fila_y_tabla(registro, clave):
    assert claves_registro.clave_de_aviso("lectura", registro) == clave


def test_solo_cuentan_los_registros_del_pa_vigente(db, entorno):
    registrar_asistencia(db, entorno.alumnos["ana"], LUNES_DEL_PA1)
    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["pasado"], 4)

    assert conteo(db) == (0, 0)


@pytest.mark.parametrize(("sincronizados", "pendientes", "esperado"), [(41, 1, 98), (39, 1, 98), (2, 1, 67)])
def test_redondeo(sincronizados, pendientes, esperado):
    # 41/42 = 97,6 % -> 98; 39/40 = 97,5 % -> 98 (0,5 hacia arriba).
    assert salud_sistema.porcentaje(sincronizados, pendientes) == esperado


def test_la_clave_de_cada_modulo_coincide_con_la_de_su_operacion(client, db, entorno, doc, actividad):
    """La clave que guarda un PUT real es la misma que se calcula desde la fila."""
    ana = entorno.alumnos["ana"]
    pa = salud_sistema.pa_actual_de_colegios(db, solo_activos=True)
    grupo = {"id_colegio": ana.id_colegio, "id_grado": ana.id_grado, "id_seccion": ana.id_seccion,
             "id_programa": ana.id_programa_actual, "id_anio_escolar": entorno.anio.id_anio_escolar}
    client.put("/rubricas", json=[{"id_operacion": str(uuid.uuid4()), "id_alumno": ana.id_alumno, "mes": 6,
                                   "fluidez": 2, **grupo}], headers=doc)
    grupo.pop("id_anio_escolar")
    client.put("/registro-vuelo", json=[{
        "id_operacion": str(uuid.uuid4()), "id_alumno": ana.id_alumno,
        "id_periodo_academico": entorno.periodos["vigente"].id_periodo_academico, "observacion": "Bien", **grupo,
    }], headers=doc)

    db.expire_all()
    claves = {o.modulo: o.clave_registro for o in db.exec(select(OperacionSincronizacion)).all()}
    assert claves["rubrica"] in salud_sistema.rubricas_guardadas(db, pa)
    assert claves["registro_vuelo"] in salud_sistema.vuelos_guardados(db, pa)
