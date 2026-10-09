"""Actividades de trabajo del Docente (CU009, CU010) y su consulta (CU017 a CU019),
incluido el registro de actividades de otra sesión (Tanda 7.1)."""
import uuid
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from tests.conftest import cabecera_de, crear_docente, id_sesion_de, iniciar_actividad, instante, iso, motivo, releer

MANUAL = "Manual por finalización de actividad"
FORZADO = "Forzado por cierre de sesión"
EXPIRACION = "Automático por expiración de sesión"


def post(client, cabecera, id_actividad=None, **cuerpo):
    return client.post(
        "/actividades", json={"id_actividad": str(id_actividad or uuid.uuid4()), **cuerpo}, headers=cabecera
    )


def finalizar(client, cabecera, id_actividad, fin=None):
    cuerpo = None if fin is None else {"fin": iso(fin)}
    return client.post(f"/actividades/{id_actividad}/finalizar", json=cuerpo, headers=cabecera)


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


# ── CU009: iniciar ──────────────────────────────────────────────────────────────────

def test_cu009_iniciar_actividad(client, entorno, doc):
    respuesta = post(client, doc)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["actividad"]["estado"] == "en_curso"
    assert cuerpo["actividad"]["tipo_cierre"] is None
    assert cuerpo["sesion_expira"] == cuerpo["actividad_sesion_expira"]


def test_cu009_iniciar_es_idempotente_por_id(client, entorno, doc):
    id_actividad = uuid.uuid4()
    primera = post(client, doc, id_actividad)

    segunda = post(client, doc, id_actividad)

    assert segunda.status_code == 200
    assert segunda.json() == primera.json()


def test_cu009_solo_una_actividad_activa(client, entorno, doc, actividad):
    respuesta = post(client, doc)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_activa_existente")


def test_cu009_id_de_otro_docente(client, db, entorno, doc, actividad):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    respuesta = post(client, cabecera_de(db, otro), actividad["id"])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_id_en_uso")


def test_cu009_sin_asignaciones_no_inicia(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    respuesta = post(client, cabecera_de(db, otro))

    assert (respuesta.status_code, motivo(respuesta)) == (409, "sin_asignaciones")
    assert respuesta.json()["detail"] == "No puede iniciar una actividad porque no tiene asignaciones activas."


@pytest.mark.parametrize("desfase", [timedelta(minutes=-1), timedelta(minutes=3)], ids=["antes_de_la_sesion", "futuro"])
def test_cu009_inicio_fuera_de_rango(client, db, entorno, doc, desfase):
    sesion = db.get(Sesion, uuid.UUID(id_sesion_de(doc)))
    hora = sesion.inicio + desfase if desfase.total_seconds() < 0 else ahora_utc() + desfase

    respuesta = post(client, doc, inicio=iso(hora))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "inicio_invalido")


@pytest.mark.parametrize("cuerpo", [{"id_actividad": "no-es-uuid"}, {"id_actividad": str(uuid.uuid4()), "inicio": "2026-01-01T10:00:00"}])
def test_cu009_validacion_del_cuerpo(client, entorno, doc, cuerpo):
    assert motivo(client.post("/actividades", json=cuerpo, headers=doc)) == "validacion"


@pytest.mark.parametrize("rol", ["Supervisor", "Directivo"])
def test_cu009_solo_el_docente(client, entorno, cabeceras, rol):
    assert motivo(post(client, cabeceras[rol])) == "sin_permiso"


# ── CU009: finalizar ────────────────────────────────────────────────────────────────

def test_cu009_finalizar_y_repetir(client, entorno, doc, actividad):
    primera = finalizar(client, doc, actividad["id"])
    segunda = finalizar(client, doc, actividad["id"])

    assert primera.json()["actividad"]["tipo_cierre"] == MANUAL
    assert primera.json()["actividad"]["estado"] == "finalizada"
    assert segunda.json() == primera.json()
    assert post(client, doc).status_code == 201


def test_cu009_finalizar_con_hora_real(client, entorno, doc, actividad):
    fin = instante(actividad["inicio"]) + timedelta(seconds=30)

    respuesta = finalizar(client, doc, actividad["id"], fin)

    assert instante(respuesta.json()["actividad"]["fin"]) == fin


@pytest.mark.parametrize("desfase", [timedelta(seconds=-1), timedelta(minutes=3)], ids=["antes_del_inicio", "futuro"])
def test_cu009_fin_fuera_de_rango(client, entorno, doc, actividad, desfase):
    base = instante(actividad["inicio"]) if desfase.total_seconds() < 0 else ahora_utc()

    respuesta = finalizar(client, doc, actividad["id"], base + desfase)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "fin_invalido")


def test_cu009_finalizar_ajena_o_inexistente_da_404(client, db, entorno, doc, actividad):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    ajena = finalizar(client, cabecera_de(db, otro), actividad["id"])
    inexistente = finalizar(client, doc, uuid.uuid4())

    assert (ajena.status_code, motivo(ajena)) == (404, "actividad_no_encontrada")
    assert ajena.json() == inexistente.json()


def test_cu009_finalizar_corrige_un_cierre_automatico(client, db, entorno, reloj):
    reloj.fijar(ahora_utc())
    cabecera = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera)
    reloj.avanzar(hours=9)
    nueva = cabecera_de(db, entorno.docente)
    fin_real = instante(actividad["inicio"]) + timedelta(hours=1)

    respuesta = finalizar(client, nueva, actividad["id"], fin_real)

    cuerpo = respuesta.json()["actividad"]
    assert (cuerpo["tipo_cierre"], instante(cuerpo["fin"])) == (MANUAL, fin_real)
    assert respuesta.json()["actividad_sesion_expira"] != respuesta.json()["sesion_expira"]


def test_cu010_actividad_de_una_sesion_vencida_no_habilita_escrituras(client, db, entorno, reloj):
    reloj.fijar(ahora_utc())
    cabecera = cabecera_de(db, entorno.docente)
    iniciar_actividad(client, cabecera)
    otra = cabecera_de(db, entorno.docente)
    reloj.avanzar(hours=8, minutes=30)
    nueva = cabecera_de(db, entorno.docente)

    respuesta = client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}", json={"nombres": "X"}, headers=nueva)

    assert motivo(respuesta) == "actividad_requerida"
    assert otra != nueva


# ── Tanda 7.1: actividades de otra sesión (sincronización tras un nuevo login) ──────

@pytest.fixture
def sesion_anterior(client, db, entorno, reloj):
    """Sesión A de hace 9 horas (vencida) y una sesión B actual."""
    reloj.fijar(ahora_utc())
    a = cabecera_de(db, entorno.docente)
    reloj.avanzar(hours=9)
    b = cabecera_de(db, entorno.docente)
    sesion = db.get(Sesion, uuid.UUID(id_sesion_de(a)))
    return {"b": b, "id": sesion.id_sesion, "inicio": sesion.inicio, "expira": sesion.expira}


def test_t71_registrar_en_una_sesion_vencida_nace_cerrada(client, entorno, sesion_anterior):
    inicio = sesion_anterior["inicio"] + timedelta(minutes=30)

    respuesta = post(client, sesion_anterior["b"], inicio=iso(inicio), id_sesion=str(sesion_anterior["id"]))

    assert respuesta.status_code == 201
    actividad = respuesta.json()["actividad"]
    assert actividad["id_sesion"] == str(sesion_anterior["id"])
    assert (actividad["tipo_cierre"], instante(actividad["fin"])) == (EXPIRACION, sesion_anterior["expira"])
    assert instante(respuesta.json()["actividad_sesion_expira"]) == sesion_anterior["expira"]


def test_t71_hereda_el_cierre_de_un_logout(client, db, entorno, sesion_anterior):
    sesion = db.get(Sesion, sesion_anterior["id"])
    sesion.fin, sesion.tipo_cierre = sesion.inicio + timedelta(hours=2), "Manual"
    db.add(sesion)
    db.commit()

    respuesta = post(client, sesion_anterior["b"], inicio=iso(sesion.inicio + timedelta(hours=1)),
                     id_sesion=str(sesion_anterior["id"]))

    assert respuesta.json()["actividad"]["tipo_cierre"] == FORZADO


@pytest.mark.parametrize(
    "caso, esperado",
    [("sin_inicio", "validacion"), ("fuera_de_la_sesion", "inicio_invalido"), ("ajena", "id_sesion_invalido")],
)
def test_t71_validaciones(client, db, entorno, sesion_anterior, caso, esperado):
    cuerpo = {"inicio": iso(sesion_anterior["inicio"] + timedelta(minutes=5)), "id_sesion": str(sesion_anterior["id"])}
    if caso == "sin_inicio":
        del cuerpo["inicio"]
    elif caso == "fuera_de_la_sesion":
        cuerpo["inicio"] = iso(sesion_anterior["expira"] + timedelta(minutes=1))
    else:
        cuerpo["id_sesion"] = str(uuid.uuid4())

    assert motivo(post(client, sesion_anterior["b"], **cuerpo)) == esperado


def test_t71_superposicion_en_la_misma_sesion(client, entorno, sesion_anterior):
    cuerpo = {"inicio": iso(sesion_anterior["inicio"] + timedelta(minutes=5)), "id_sesion": str(sesion_anterior["id"])}
    post(client, sesion_anterior["b"], **cuerpo)

    respuesta = post(client, sesion_anterior["b"], **cuerpo)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_superpuesta")


def test_t71_en_una_sesion_vigente_queda_en_curso(client, db, entorno):
    a = cabecera_de(db, entorno.docente)
    b = cabecera_de(db, entorno.docente)
    sesion_a = db.get(Sesion, uuid.UUID(id_sesion_de(a)))

    respuesta = post(client, b, inicio=iso(sesion_a.inicio), id_sesion=str(sesion_a.id_sesion))

    assert respuesta.json()["actividad"]["estado"] == "en_curso"
    assert motivo(post(client, b, inicio=iso(sesion_a.inicio), id_sesion=str(sesion_a.id_sesion))) == (
        "actividad_activa_existente"
    )


def test_t71_sin_asignacion_ese_dia_no_registra(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()
    a, b = cabecera_de(db, otro), cabecera_de(db, otro)
    sesion_a = db.get(Sesion, uuid.UUID(id_sesion_de(a)))

    respuesta = post(client, b, inicio=iso(sesion_a.inicio), id_sesion=str(sesion_a.id_sesion))

    assert (respuesta.status_code, motivo(respuesta)) == (409, "sin_asignaciones")


# ── CU017 a CU019: consulta ─────────────────────────────────────────────────────────

def test_cu017_listado_con_productividad(client, entorno, doc, actividad):
    client.post(
        "/alumnos",
        json={"nombres": "N", "apellidos": "N", "id_colegio": entorno.colegios["Andino"].id_colegio,
              "id_grado": entorno.grados["2.º"], "id_programa": entorno.programas["Comprensión Lectora"]},
        headers=doc,
    )
    client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}", json={"nombres": "Anita"}, headers=doc)

    cuerpo = client.get("/actividades", headers=doc).json()

    fila = cuerpo["items"][0]
    assert (fila["estado"], fila["sincronizacion"], fila["cambios"]) == ("en_curso", "sincronizada", 2)
    assert fila["productividad"] == [{"modulo": "alumnos", "cantidad": 2}]
    assert fila["productividad_texto"] == "2 Alumnos"
    assert fila["fecha"] == entorno.hoy.isoformat()


def test_cu018_filtros_del_listado(client, entorno, doc, actividad):
    finalizar(client, doc, actividad["id"])
    en_curso = iniciar_actividad(client, doc)
    hoy = entorno.hoy.isoformat()

    def ids(**params):
        return [a["id"] for a in client.get("/actividades", params=params, headers=doc).json()["items"]]

    assert ids(estado="en_curso") == [en_curso["id"]]
    assert ids(estado="finalizada", tipo_cierre=MANUAL) == [actividad["id"]]
    assert ids(desde=hoy, hasta=hoy, sincronizacion="sincronizada") == [en_curso["id"], actividad["id"]]
    assert ids(hasta=(entorno.hoy - timedelta(days=1)).isoformat()) == []


def test_cu018_rango_invertido(client, entorno, doc):
    respuesta = client.get("/actividades", params={"desde": "2026-05-02", "hasta": "2026-05-01"}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "rango_fechas_invalido")


def test_cu017_solo_el_docente_consulta(client, entorno, cabeceras):
    assert client.get("/actividades", headers=cabeceras["Supervisor"]).status_code == 403


def test_cu019_detalle_de_la_actividad(client, entorno, doc, actividad):
    ana = entorno.alumnos["ana"].id_alumno
    client.patch(f"/alumnos/{ana}", json={"nombres": "Anita"}, headers=doc)
    client.patch(f"/alumnos/{ana}/desactivar", headers=doc)
    finalizar(client, doc, actividad["id"])

    cuerpo = client.get(f"/actividades/{actividad['id']}", headers=doc).json()

    assert (cuerpo["estado"], cuerpo["tipo_cierre"], cuerpo["registros_pendientes"], cuerpo["cambios"]) == (
        "finalizada", MANUAL, 0, 1
    )
    assert cuerpo["cambios_por_modulo"] == [
        {"modulo": "alumnos", "total": 1, "creados": 0, "editados": 1, "activados": 0, "inactivados": 1}
    ]
    assert cuerpo["asignaciones"] == [
        {"colegio": {"id": entorno.colegios["Andino"].id_colegio, "nombre": "Colegio Andino"},
         "grado": {"id": entorno.grados["2.º"], "nombre": "2.º"}, "ciclo": "III", "seccion": "Única"}
    ]


def test_cu019_detalle_ajeno_o_inexistente(client, entorno, doc):
    respuesta = client.get(f"/actividades/{uuid.uuid4()}", headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (404, "actividad_no_encontrada")
    assert respuesta.json()["detail"] == "El detalle solicitado no se encuentra disponible."


def test_cu019_detalle_sin_cambios(client, entorno, doc, actividad):
    cuerpo = client.get(f"/actividades/{actividad['id']}", headers=doc).json()

    assert (cuerpo["cambios"], cuerpo["cambios_por_modulo"], cuerpo["asignaciones"]) == (0, [], [])


def test_cu017_la_lista_cierra_antes_las_actividades_vencidas(client, db, entorno, reloj):
    reloj.fijar(ahora_utc())
    cabecera = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera)
    reloj.avanzar(hours=9)

    client.get("/actividades", headers=cabecera_de(db, entorno.docente))

    assert releer(db, Actividad, uuid.UUID(actividad["id"])).tipo_cierre == EXPIRACION
    assert db.exec(select(Actividad)).one().fin is not None
