"""Sincronización (T5): procesar operaciones, avisos de pendientes, estados de actividad y
docente (CU017-CU021), Salud del Sistema (CU012), cierre con hora no verificada (CU008)
y los endpoints de habilitaciones del contrato."""
import uuid
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.organizacion import DocenteColegioGrado, PermisoCorreccion, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import OperacionPendiente, OperacionSincronizacion
from app.schemas.sincronizacion import ItemOperacion
from app.services import salud_sistema
from app.services.habilitacion import habilitacion_escritura
from app.services.sesiones import fin_estimado
from app.services.sincronizacion import Operacion, RechazoOperacion, procesar_operacion
from tests.conftest import (
    agregar_no_lectivo,
    asignar,
    cabecera_de,
    crear_docente,
    crear_particular,
    crear_permiso,
    id_sesion_de,
    iniciar_actividad,
    iso,
    motivo,
    rangos_en_receso,
    releer,
)

OPERACION = Operacion(modulo="asistencias", entidad="prueba", id_entidad="1")


def item(**campos) -> ItemOperacion:
    return ItemOperacion(id_operacion=campos.pop("id_operacion", uuid.uuid4()), **campos)


def operaciones(db) -> list[OperacionSincronizacion]:
    db.expire_all()
    return list(db.exec(select(OperacionSincronizacion)).all())


def pendiente(id_operacion=None, modulo="asistencias", **campos) -> dict:
    return {
        "id_operacion": str(id_operacion or uuid.uuid4()), "id_dispositivo": "tablet-1", "modulo": modulo,
        "realizado_en": iso(tiempo.ahora_utc()), **campos,
    }


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


# ── procesar_operacion ──────────────────────────────────────────────────────────────

def test_idempotencia_aplica_una_sola_vez(db, entorno, actividad):
    llamadas = []

    def aplicar(contexto):
        llamadas.append(contexto)
        return {"valor": len(llamadas)}

    operacion = item()
    primera = procesar_operacion(db, entorno.docente, operacion, aplicar, OPERACION)
    db.commit()
    segunda = procesar_operacion(db, entorno.docente, operacion, aplicar, OPERACION)

    assert len(llamadas) == 1
    assert primera == segunda
    assert (primera.estado, primera.registro) == ("aceptada", {"valor": 1})
    assert [o.estado for o in operaciones(db)] == ["aceptada"]
    assert str(llamadas[0].id_actividad) == actividad["id"]


def test_rechazo_de_negocio_se_registra_y_deshace_lo_aplicado(db, entorno, actividad):
    def aplicar(_):
        db.add(PermisoCorreccion(
            id_docente=entorno.id_docente, id_anio_escolar=entorno.anio.id_anio_escolar,
            id_periodo_academico=entorno.periodos["pasado"].id_periodo_academico,
            id_colegio=entorno.colegios["Andino"].id_colegio, id_grado=entorno.grados["1.º"], modulo="lectura",
            inicio=tiempo.ahora_utc(), fin=tiempo.ahora_utc(), motivo="x",
            otorgado_por=entorno.supervisor.id_usuario, otorgado_en=tiempo.ahora_utc(),
        ))
        db.flush()
        raise RechazoOperacion("regla", "No se cumple la regla.")

    resultado = procesar_operacion(db, entorno.docente, item(), aplicar, OPERACION)
    db.commit()

    assert (resultado.estado, resultado.motivo, resultado.detail) == ("rechazada", "regla", "No se cumple la regla.")
    assert [(o.estado, o.motivo) for o in operaciones(db)] == [("rechazada", "regla")]
    # El SAVEPOINT deshizo lo que alcanzó a escribir.
    assert db.exec(select(PermisoCorreccion)).all() == []


def test_error_tecnico_no_registra_nada(db, entorno, actividad):
    def aplicar(_):
        raise RuntimeError("se cayó la BD")

    operacion = item()

    with pytest.raises(RuntimeError):
        procesar_operacion(db, entorno.docente, operacion, aplicar, OPERACION)
    db.rollback()

    assert operaciones(db) == []


def test_procesar_borra_el_pendiente(client, db, entorno, doc, actividad):
    id_operacion = uuid.uuid4()
    client.post("/sincronizacion/pendientes", json=[pendiente(id_operacion, id_actividad=actividad["id"])], headers=doc)

    procesar_operacion(db, entorno.docente, item(id_operacion=id_operacion), lambda _: None, OPERACION)
    db.commit()

    assert db.exec(select(OperacionPendiente)).all() == []


def test_fuera_de_actividad(db, entorno, actividad):
    antes = tiempo.ahora_utc() - timedelta(hours=1)

    resultado = procesar_operacion(
        db, entorno.docente, item(realizado_en=antes, id_actividad=uuid.UUID(actividad["id"])), lambda _: {}, OPERACION
    )

    assert (resultado.estado, resultado.motivo) == ("rechazada", "fuera_de_actividad")


def test_actividad_ajena_es_fuera_de_actividad(client, db, entorno, actividad):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()

    resultado = procesar_operacion(db, otro, item(id_actividad=uuid.UUID(actividad["id"])), lambda _: {}, OPERACION)

    assert resultado.motivo == "fuera_de_actividad"


def test_sin_actividad_activa_ni_indicada(db, entorno):
    resultado = procesar_operacion(db, entorno.docente, item(), lambda _: {}, OPERACION)

    assert (resultado.estado, resultado.motivo) == ("rechazada", "actividad_requerida")


def test_realizado_en_futuro(db, entorno, actividad):
    futuro = tiempo.ahora_utc() + timedelta(hours=1)

    resultado = procesar_operacion(db, entorno.docente, item(realizado_en=futuro), lambda _: {}, OPERACION)

    assert resultado.motivo == "realizado_en_futuro"


def test_id_de_operacion_de_otro_usuario(db, entorno, actividad):
    operacion = item()
    procesar_operacion(db, entorno.docente, operacion, lambda _: {}, OPERACION)
    db.commit()

    resultado = procesar_operacion(db, entorno.supervisor, operacion, lambda _: {}, OPERACION)

    assert (resultado.estado, resultado.motivo) == ("rechazada", "operacion_ajena")


def test_habilitacion_se_evalua_en_la_hora_real(client, db, entorno, reloj):
    """Un permiso que ya venció pero estaba vigente al hacer la operación: aceptada."""
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()
    inicio = tiempo.ahora_utc()
    reloj.fijar(inicio)
    crear_permiso(db, entorno, otro.id_docente, "Lejano", "3.º", modulo="asistencias",
                  inicio=inicio - timedelta(minutes=5), fin=inicio + timedelta(hours=1))
    actividad = iniciar_actividad(client, cabecera_de(db, otro))
    reloj.avanzar(hours=2)

    def aplicar(contexto):
        habilitacion = habilitacion_escritura(
            db, otro, entorno.colegios["Lejano"].id_colegio, entorno.grados["3.º"],
            entorno.periodos["pasado"].id_periodo_academico, "asistencias", en=contexto.en,
        )
        if not habilitacion.permitido:
            raise RechazoOperacion("sin_habilitacion", habilitacion.motivo)
        return {"id_permiso": habilitacion.id_permiso}

    en_su_hora = procesar_operacion(
        db, otro, item(realizado_en=inicio + timedelta(minutes=30), id_actividad=uuid.UUID(actividad["id"])),
        aplicar, OPERACION,
    )
    ahora = procesar_operacion(db, otro, item(id_actividad=uuid.UUID(actividad["id"])), aplicar, OPERACION)

    assert en_su_hora.estado == "aceptada"
    assert (ahora.estado, ahora.motivo) == ("rechazada", "sin_habilitacion")


# ── Avisos de pendientes y consulta de operaciones ──────────────────────────────────

def test_pendientes_idempotente_e_ignora_los_procesados(client, db, entorno, doc, actividad):
    procesada = item()
    procesar_operacion(db, entorno.docente, procesada, lambda _: {}, OPERACION)
    db.commit()
    nueva = pendiente()

    primera = client.post("/sincronizacion/pendientes", json=[nueva, pendiente(procesada.id_operacion)], headers=doc)
    segunda = client.post("/sincronizacion/pendientes", json=[nueva], headers=doc)

    assert (primera.status_code, primera.json()) == (200, {"recibidos": 2, "registrados": 1})
    assert segunda.json() == {"recibidos": 1, "registrados": 0}
    assert [str(p.id_operacion) for p in db.exec(select(OperacionPendiente)).all()] == [nueva["id_operacion"]]


def test_pendientes_validacion_y_roles(client, cabeceras):
    sin_token = client.post("/sincronizacion/pendientes", json=[pendiente()])
    supervisor = client.post("/sincronizacion/pendientes", json=[pendiente()], headers=cabeceras["Supervisor"])
    invalido = client.post("/sincronizacion/pendientes", json=[pendiente(modulo="otro")], headers=cabeceras["Docente"])

    assert sin_token.status_code == 401
    assert (supervisor.status_code, motivo(supervisor)) == (403, "sin_permiso")
    assert (invalido.status_code, motivo(invalido)) == (422, "validacion")


def test_consultar_operacion(client, db, entorno, doc, actividad):
    propia = item()
    procesar_operacion(db, entorno.docente, propia, lambda _: {"ok": True}, OPERACION)
    db.commit()
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    url = f"/sincronizacion/operaciones/{propia.id_operacion}"
    mia = client.get(url, headers=doc).json()
    ajena = client.get(url, headers=cabecera_de(db, otro)).json()
    nunca = client.get(f"/sincronizacion/operaciones/{uuid.uuid4()}", headers=doc).json()

    assert (mia["estado"], mia["respuesta"]["registro"]) == ("aceptada", {"ok": True})
    assert ajena == nunca == {"estado": "desconocida", "motivo": None, "respuesta": None}
    assert client.get(url).status_code == 401
    assert client.get(url, headers=cabecera_de(db, entorno.supervisor)).status_code == 403


# ── Estados de actividad y de docente (CU017-CU021) ─────────────────────────────────

def estado_actividad(client, doc, id_actividad) -> tuple[str, int]:
    cuerpo = client.get(f"/actividades/{id_actividad}", headers=doc).json()
    return cuerpo["sincronizacion"], cuerpo["registros_pendientes"]


def estado_docente(client, cabeceras) -> tuple[str, int]:
    [fila] = client.get("/seguimiento/docentes", headers=cabeceras["Supervisor"]).json()["items"]
    return fila["sincronizacion"], fila["registros_pendientes"]


def test_estados_sin_informacion_solo_sin_actividades(client, cabeceras):
    assert estado_docente(client, cabeceras) == ("sin_informacion", None)


def test_estados_al_dia_pendiente_y_error(client, db, entorno, cabeceras, actividad):
    doc = cabeceras["Docente"]
    assert estado_actividad(client, doc, actividad["id"]) == ("sincronizada", 0)
    assert estado_docente(client, cabeceras) == ("al_dia", 0)

    aviso = pendiente(id_actividad=actividad["id"])
    client.post("/sincronizacion/pendientes", json=[aviso, pendiente(id_actividad=actividad["id"])], headers=doc)
    assert estado_actividad(client, doc, actividad["id"]) == ("pendiente", 2)
    assert estado_docente(client, cabeceras) == ("pendiente", 2)

    def rechazar(_):
        raise RechazoOperacion("regla", "No.")

    procesar_operacion(db, entorno.docente, item(id_operacion=uuid.UUID(aviso["id_operacion"])), rechazar, OPERACION)
    db.commit()
    assert estado_actividad(client, doc, actividad["id"]) == ("error", 1)
    assert estado_docente(client, cabeceras) == ("error", 1)
    assert client.get("/actividades", params={"sincronizacion": "error"}, headers=doc).json()["total"] == 1


def test_inicio_del_docente_cuenta_sus_pendientes(client, doc):
    client.post("/sincronizacion/pendientes", json=[pendiente(), pendiente()], headers=doc)

    assert client.get("/inicio/docente", headers=doc).json()["registros_offline_pendientes"] == 2


# ── Salud del Sistema (CU012) ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("sincronizados", "pendientes", "esperado"), [
    (0, 0, None), (2, 1, 67), (1, 7, 13), (1, 1, 50), (3, 0, 100), (0, 4, 0),
])
def test_salud_formula_y_redondeo(sincronizados, pendientes, esperado):
    assert salud_sistema.porcentaje(sincronizados, pendientes) == esperado


def test_salud_en_el_inicio_del_directivo(client, cabeceras, monkeypatch):
    directivo = cabeceras["Directivo"]
    assert client.get("/inicio/directivo", headers=directivo).json()["salud_sistema"] is None

    client.post("/sincronizacion/pendientes", json=[pendiente(), pendiente(modulo="alumnos")], headers=cabeceras["Docente"])
    monkeypatch.setitem(salud_sistema.CONTADORES, "lectura", lambda _db, _pa: 2)

    # 2 sincronizados y 1 pendiente de los cuatro módulos (el de Alumnos no cuenta).
    assert client.get("/inicio/directivo", headers=directivo).json()["salud_sistema"] == 67


# ── Cierre con hora no verificada (CU008) ───────────────────────────────────────────

@pytest.fixture
def anterior(client, db, entorno, reloj):
    """Una sesión anterior del Docente abierta hace 2 horas, con su última petición hace
    1 hora, y la sesión nueva (desde la que se cierra)."""
    reloj.fijar(tiempo.ahora_utc() - timedelta(hours=2))
    vieja = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, vieja)
    reloj.avanzar(hours=1)
    client.get("/me", headers=vieja)
    reloj.avanzar(hours=1)
    return {"vieja": vieja, "nueva": cabecera_de(db, entorno.docente), "actividad": actividad}


def cerrar(client, anterior, **cuerpo):
    return client.post(f"/me/sesiones/{id_sesion_de(anterior['vieja'])}/cerrar", json=cuerpo, headers=anterior["nueva"])


def test_fin_minimo_usa_la_ultima_peticion(client, db, anterior, reloj):
    ultima = releer(db, Sesion, uuid.UUID(id_sesion_de(anterior["vieja"]))).ultima_peticion_en
    fin_minimo = ultima - timedelta(minutes=20)

    respuesta = cerrar(client, anterior, fin_minimo=iso(fin_minimo))

    assert respuesta.status_code == 200, respuesta.text
    sesion = respuesta.json()["sesion"]
    assert (sesion["fin"], sesion["fin_verificado"]) == (iso(ultima), False)
    assert respuesta.json()["actividad"]["fin"] == iso(ultima)


def test_fin_minimo_posterior_a_la_ultima_peticion(client, anterior, reloj):
    fin_minimo = reloj.instante - timedelta(minutes=10)

    sesion = cerrar(client, anterior, fin_minimo=iso(fin_minimo)).json()["sesion"]

    assert (sesion["fin"], sesion["fin_verificado"]) == (iso(fin_minimo), False)


def test_fin_minimo_acotado_al_vencimiento(db, anterior, reloj):
    sesion = releer(db, Sesion, uuid.UUID(id_sesion_de(anterior["vieja"])))

    assert fin_estimado(sesion, sesion.expira + timedelta(hours=1)) == sesion.expira
    assert fin_estimado(sesion, sesion.inicio) == sesion.ultima_peticion_en


def test_fin_minimo_de_una_sesion_ya_vencida_queda_en_su_vencimiento(client, db, anterior, reloj):
    reloj.avanzar(hours=7)
    expira = releer(db, Sesion, uuid.UUID(id_sesion_de(anterior["vieja"]))).expira

    respuesta = cerrar(client, anterior, fin_minimo=iso(expira + timedelta(hours=1)))

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["sesion"]["fin"] == iso(expira)


def test_fin_y_fin_minimo_juntos_o_ninguno_es_422(client, anterior, reloj):
    ahora = iso(reloj.instante)

    ambos = cerrar(client, anterior, fin=ahora, fin_minimo=ahora)
    ninguno = cerrar(client, anterior)

    assert (ambos.status_code, motivo(ambos)) == (422, "validacion")
    assert (ninguno.status_code, motivo(ninguno)) == (422, "validacion")


def test_fin_verificado_por_defecto(client, anterior, reloj):
    sesion = cerrar(client, anterior, fin=iso(reloj.instante - timedelta(minutes=5))).json()["sesion"]

    assert sesion["fin_verificado"] is True


def test_ultima_peticion_a_lo_sumo_una_vez_por_minuto(client, db, entorno, reloj):
    reloj.fijar(tiempo.ahora_utc())
    cabecera = cabecera_de(db, entorno.docente)
    id_sesion = uuid.UUID(id_sesion_de(cabecera))

    def ultima():
        return releer(db, Sesion, id_sesion).ultima_peticion_en

    client.get("/me", headers=cabecera)
    primera = ultima()
    reloj.avanzar(seconds=30)
    client.get("/me", headers=cabecera)
    assert ultima() == primera == reloj.instante - timedelta(seconds=30)
    reloj.avanzar(seconds=31)
    client.get("/me", headers=cabecera)
    assert ultima() == reloj.instante


# ── GET /calendarios/periodo ────────────────────────────────────────────────────────

def periodo(client, cabecera, colegio_id, fecha):
    return client.get("/calendarios/periodo", params={"id_colegio": colegio_id, "fecha": fecha.isoformat()}, headers=cabecera)


def proximo_dia(desde, laborable=True):
    dia = desde
    while (dia.weekday() <= 4) != laborable:
        dia += timedelta(days=1)
    return dia


def test_periodo_en_un_dia_lectivo(client, entorno, cabeceras):
    andino = entorno.colegios["Andino"].id_colegio
    dia = proximo_dia(entorno.inicio("vigente") + timedelta(days=1))

    cuerpo = periodo(client, cabeceras["Docente"], andino, dia).json()

    assert cuerpo == {
        "fecha": dia.isoformat(), "tipo": "PA", "numero": 2,
        "id_periodo_academico": entorno.periodos["vigente"].id_periodo_academico,
        "fecha_inicio": entorno.inicio("vigente").isoformat(), "fecha_fin": entorno.fin("vigente").isoformat(),
        "es_lectivo": True, "motivo_no_lectivo": None,
    }


def test_periodo_motivos_no_lectivos(client, db, entorno, cabeceras):
    sup = cabeceras["Supervisor"]
    andino = entorno.colegios["Andino"].id_colegio
    feriado = proximo_dia(entorno.inicio("vigente") + timedelta(days=2))
    agregar_no_lectivo(db, entorno.calendario, feriado, motivo="Día del Maestro")

    def motivo_de(fecha):
        return periodo(client, sup, andino, fecha).json()["motivo_no_lectivo"]

    assert motivo_de(feriado) == "Día del Maestro"
    assert motivo_de(proximo_dia(entorno.inicio("vigente"), laborable=False)) == "fin_de_semana"
    assert motivo_de(entorno.inicio("PR1")) == "receso"
    fuera = periodo(client, sup, andino, entorno.inicio("pasado") - timedelta(days=10)).json()
    assert (fuera["tipo"], fuera["motivo_no_lectivo"], fuera["es_lectivo"]) == (None, "fuera_de_periodo", False)


def test_periodo_usa_el_calendario_del_colegio(client, db, entorno, cabeceras):
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))

    cuerpo = periodo(client, cabeceras["Supervisor"], entorno.colegios["Lejano"].id_colegio, entorno.hoy).json()

    assert (cuerpo["tipo"], cuerpo["numero"], cuerpo["motivo_no_lectivo"]) == ("PR", 2, "receso")


def test_periodo_errores(client, entorno, cabeceras):
    lejano = entorno.colegios["Lejano"].id_colegio

    sin_token = periodo(client, {}, lejano, entorno.hoy)
    directivo = periodo(client, cabeceras["Directivo"], lejano, entorno.hoy)
    fuera_de_alcance = periodo(client, cabeceras["Docente"], lejano, entorno.hoy)
    inexistente = periodo(client, cabeceras["Supervisor"], 999, entorno.hoy)
    sin_fecha = client.get("/calendarios/periodo", params={"id_colegio": lejano}, headers=cabeceras["Supervisor"])

    assert sin_token.status_code == 401
    assert directivo.status_code == 403
    assert (fuera_de_alcance.status_code, motivo(fuera_de_alcance)) == (403, "colegio_fuera_de_alcance")
    assert (inexistente.status_code, motivo(inexistente)) == (422, "colegio_inexistente")
    assert (sin_fecha.status_code, motivo(sin_fecha)) == (422, "validacion")


# ── GET /me/permisos y GET /me/habilitaciones ───────────────────────────────────────

def test_mis_permisos_vigentes(client, db, entorno, cabeceras):
    vigente = crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º")
    ahora = tiempo.ahora_utc()
    crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º", modulo="rubrica",
                  inicio=ahora + timedelta(hours=1), fin=ahora + timedelta(hours=2))

    respuesta = client.get("/me/permisos", headers=cabeceras["Docente"])

    assert respuesta.status_code == 200
    [permiso] = respuesta.json()
    assert (permiso["id"], permiso["estado"], permiso["modulo"]) == (vigente.id_permiso, "vigente", "lectura")
    assert permiso["colegio"] == {"id": entorno.colegios["Lejano"].id_colegio, "nombre": "Colegio Lejano"}
    assert client.get("/me/permisos").status_code == 401
    assert client.get("/me/permisos", headers=cabeceras["Supervisor"]).status_code == 403


def test_mis_habilitaciones(client, db, entorno, cabeceras):
    ahora = tiempo.ahora_utc()
    programado = crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º",
                               inicio=ahora + timedelta(hours=1), fin=ahora + timedelta(hours=2))
    crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º", modulo="rubrica",
                  inicio=ahora - timedelta(hours=2), fin=ahora - timedelta(hours=1))

    cuerpo = client.get("/me/habilitaciones", headers=cabeceras["Docente"]).json()

    vigente = entorno.periodos["vigente"].id_periodo_academico
    # La del PA1 ya terminó; la del PA2 sigue.
    assert cuerpo["asignaciones"] == [
        {"id_colegio": entorno.colegios["Andino"].id_colegio, "id_grado": entorno.grados[g],
         "id_periodo_academico": vigente, "retroactiva": False}
        for g in ("1.º", "2.º")
    ]
    assert [p["id_permiso"] for p in cuerpo["permisos"]] == [programado.id_permiso]
    assert sorted((c["id_colegio"], c["id_anio_escolar"]) for c in cuerpo["calendarios"]) == sorted(
        (entorno.colegios[c].id_colegio, entorno.anio.id_anio_escolar) for c in ("Andino", "Lejano")
    )
    assert all(len(c["periodos"]) == 8 for c in cuerpo["calendarios"])


def test_mis_habilitaciones_docente_rotado_con_retroactiva(client, db, entorno, cabeceras):
    """Rotó de Lejano (retroactiva del PA2, hoy en su PR2) a Andino: vienen los dos
    calendarios, sin repetir."""
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))
    for fila in db.exec(select(DocenteColegioGrado)).all():
        db.delete(fila)
    asignar(db, entorno.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    asignar(db, entorno.id_docente, entorno.colegios["Andino"], [entorno.grados["1.º"]], entorno.periodos["futuro"], 1)
    asignar(db, entorno.id_docente, entorno.colegios["Andino"], [entorno.grados["2.º"]], entorno.periodos["futuro"], 1)
    retro = db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_colegio == entorno.colegios["Lejano"].id_colegio)).one()
    retro.retroactiva = True
    db.commit()

    cuerpo = client.get("/me/habilitaciones", headers=cabeceras["Docente"]).json()

    assert [(a["id_colegio"], a["retroactiva"]) for a in cuerpo["asignaciones"]] == [
        (entorno.colegios["Andino"].id_colegio, False), (entorno.colegios["Andino"].id_colegio, False),
        (entorno.colegios["Lejano"].id_colegio, True),
    ]
    calendarios = {c["id_colegio"]: c["origen"] for c in cuerpo["calendarios"]}
    assert calendarios == {entorno.colegios["Andino"].id_colegio: "general", entorno.colegios["Lejano"].id_colegio: "particular"}
    assert len(cuerpo["calendarios"]) == 2


def test_mis_habilitaciones_roles(client, cabeceras):
    assert client.get("/me/habilitaciones").status_code == 401
    assert client.get("/me/habilitaciones", headers=cabeceras["Supervisor"]).status_code == 403


# ── Ajustes de P2 (5.1) ─────────────────────────────────────────────────────────────

def test_desde_periodo_e_inicio_asignacion_juntos_es_422(client, entorno, cabeceras):
    url = f"/usuarios/{entorno.docente.id_usuario}"
    cambio = {"grados": [entorno.grados["3.º"]], "desde_periodo": entorno.periodos["futuro"].id_periodo_academico,
              "inicio_asignacion": "proximo"}

    respuesta = client.patch(url, json={"asignacion": cambio}, headers=cabeceras["Supervisor"])

    assert (respuesta.status_code, motivo(respuesta)) == (422, "inicio_asignacion_ambiguo")


@pytest.fixture
def con_retroactiva(client, db, entorno, cabeceras):
    """Docente nuevo con asignación retroactiva en Lejano 3.º (hoy en su PR2)."""
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))
    datos = {
        "nombres": "Nuevo", "apellidos": "Docente", "dni": "60000001", "correo": "nuevo@sicedu.test",
        "id_rol": entorno.roles["Docente"],
        "asignacion": {"id_colegio": entorno.colegios["Lejano"].id_colegio, "id_anio_escolar": entorno.anio.id_anio_escolar,
                       "grados": [entorno.grados["3.º"]], "inicio_asignacion": "retroactivo"},
    }
    creado = client.post("/usuarios", json=datos, headers=cabeceras["Supervisor"])
    assert creado.status_code == 201, creado.text
    return cabecera_de(db, db.get(Usuario, creado.json()["usuario"]["id"]))


def test_retroactiva_suma_al_inicio_pero_no_escribe_alumnos(client, entorno, con_retroactiva):
    inicio = client.get("/inicio/docente", headers=con_retroactiva).json()
    iniciar_actividad(client, con_retroactiva)
    editar = client.patch(f"/alumnos/{entorno.alumnos['mario'].id_alumno}", json={"nombres": "M"}, headers=con_retroactiva)

    assert [a["colegio"] for a in inicio["asignaciones"]] == ["Colegio Lejano"]
    assert inicio["totales"]["cantidad_alumnos"] == 1
    assert (editar.status_code, motivo(editar)) == (403, "alumno_fuera_de_alcance")


def test_grado_a_cargo_con_subprogramas_y_secciones(client, entorno, cabeceras):
    [andino] = client.get("/me/asignaciones", headers=cabeceras["Docente"]).json()
    primero = andino["grados"][0]

    assert primero["subprogramas"] == [{"id": entorno.programas["Alfabetización"], "nombre": "Alfabetización"}]
    assert [s["nombre"] for s in primero["secciones"]] == ["Única"]
    assert andino["subprogramas"] == ["Alfabetización", "Comprensión Lectora"]
