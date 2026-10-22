"""Endpoints del calendario académico (CU027): consultar, crear el general, editar con
control de versión, fechas particulares, volver al general, validaciones, impacto sobre
asistencias, auditoría y permisos."""
import json
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.organizacion import ORIGEN_AUTOMATICO, AnioEscolar, Calendario, PeriodoAcademico
from app.models.trazabilidad import Auditoria
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    cabecera_de,
    crear_calendario,
    motivo,
    rangos_relativos,
    registrar_asistencia,
    releer,
)

HOY = date(2026, 6, 17)  # miércoles; el PA2 del general va del 18/05 al 16/07
LUNES = date(2026, 6, 15)
URL = "/calendarios"
ORDEN = ("PA1", "PR1", "PA2", "PR2", "PA3", "PR3", "PA4", "PR4")


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def periodos(rangos: dict[str, tuple[date, date]]) -> list[dict]:
    return [
        {"tipo": nombre[:2], "numero": int(nombre[2]), "fecha_inicio": inicio.isoformat(), "fecha_fin": fin.isoformat()}
        for nombre, (inicio, fin) in sorted(rangos.items(), key=lambda r: ORDEN.index(r[0]))
    ]


def dia(inicio: date, fin=None, motivo_dia="Feriado") -> dict:
    return {"motivo": motivo_dia, "fecha_inicio": inicio.isoformat(), "fecha_fin": (fin or inicio).isoformat()}


def con_cambio(nombre: str, inicio=None, fin=None) -> dict:
    """Rangos del entorno con un periodo movido."""
    rangos = rangos_relativos(HOY)
    viejo = rangos[nombre]
    rangos[nombre] = (inicio or viejo[0], fin or viejo[1])
    return rangos


def anio_nuevo(db, desplazamiento=400) -> AnioEscolar:
    """Año escolar sin calendario, lejos del del entorno."""
    anio = AnioEscolar(
        nombre="Nuevo", fecha_inicio=HOY + timedelta(days=desplazamiento - 200),
        fecha_fin=HOY + timedelta(days=desplazamiento + 150), creado_por=1, creado_en=ahora_utc(),
    )
    db.add(anio)
    db.commit()
    return anio


def editar(client, cabecera, id_calendario, rangos, version=1, dias=()):
    return client.put(
        f"{URL}/{id_calendario}",
        json={"version": version, "periodos": periodos(rangos), "dias_no_lectivos": list(dias)},
        headers=cabecera,
    )


def asistencia_en(db, entorno, fecha, colegio="Andino"):
    alumno = entorno.alumnos["ana"] if colegio == "Andino" else entorno.alumnos["mario"]
    registrar_asistencia(db, alumno, fecha)


def auditoria(db, id_calendario) -> list[Auditoria]:
    db.expire_all()
    return db.exec(
        select(Auditoria).where(Auditoria.tabla == "calendario", Auditoria.id_registro == str(id_calendario))
        .order_by(Auditoria.id_auditoria)
    ).all()


def particular(client, entorno, sup, rangos=None, dias=None, colegio="Lejano"):
    cuerpo = {
        "id_colegio": entorno.colegios[colegio].id_colegio, "id_anio_escolar": entorno.anio.id_anio_escolar,
        "periodos": periodos(rangos or rangos_relativos(HOY)),
    }
    if dias is not None:
        cuerpo["dias_no_lectivos"] = dias
    return client.post(f"{URL}/particulares", json=cuerpo, headers=sup)


# ── Consulta ────────────────────────────────────────────────────────────────────────

def test_consultar_el_general(client, entorno, sup):
    cuerpo = client.get(URL, params={"id_anio_escolar": entorno.anio.id_anio_escolar}, headers=sup).json()

    assert (cuerpo["origen"], cuerpo["colegio"], cuerpo["version"]) == ("general", None, 1)
    assert cuerpo["id_calendario"] == entorno.calendario.id_calendario
    assert [f'{p["tipo"]}{p["numero"]}' for p in cuerpo["periodos"]] == list(ORDEN)
    assert cuerpo["periodos"][2]["id_periodo_academico"] == entorno.periodos["vigente"].id_periodo_academico
    assert cuerpo["periodo_actual"]["tipo"] == "PA"
    assert cuerpo["periodo_actual"]["numero"] == 2
    assert (cuerpo["dias_no_lectivos"], cuerpo["fecha_referencia"]) == ([], HOY.isoformat())
    # Sin año: el de referencia.
    assert client.get(URL, headers=sup).json() == cuerpo


def test_consultar_por_colegio_sin_particular_es_el_general(client, entorno, sup):
    andino = entorno.colegios["Andino"]

    cuerpo = client.get(URL, params={"id_colegio": andino.id_colegio}, headers=sup).json()

    assert (cuerpo["origen"], cuerpo["colegio"]) == ("general", {"id": andino.id_colegio, "nombre": "Colegio Andino"})


def test_sin_periodo_a_la_fecha_actual(client, db, entorno, miercoles):
    """Después del PR4: "Sin periodo aplicable a la fecha actual" (periodo_actual null)."""
    miercoles.fijar(a_las(entorno.fin("PR4") + timedelta(days=3), 9))
    # La sesión se abre después de mover el reloj: una de antes ya habría vencido.
    cabecera = cabecera_de(db, entorno.supervisor)

    cuerpo = client.get(URL, params={"id_anio_escolar": entorno.anio.id_anio_escolar}, headers=cabecera).json()

    assert (cuerpo["periodo_actual"], len(cuerpo["periodos"])) == (None, 8)


def test_consulta_errores(client, db, entorno, sup):
    nuevo = anio_nuevo(db)

    sin_calendario = client.get(URL, params={"id_anio_escolar": nuevo.id_anio_escolar}, headers=sup)
    anio_inexistente = client.get(URL, params={"id_anio_escolar": 999}, headers=sup)
    colegio_inexistente = client.get(URL, params={"id_colegio": 999}, headers=sup)

    assert (sin_calendario.status_code, motivo(sin_calendario)) == (404, "calendario_no_configurado")
    assert sin_calendario.json()["detail"] == "No hay un calendario académico configurado para este año escolar."
    assert (anio_inexistente.status_code, motivo(anio_inexistente)) == (422, "anio_inexistente")
    assert (colegio_inexistente.status_code, motivo(colegio_inexistente)) == (422, "colegio_inexistente")


def test_el_docente_solo_lee_el_calendario_de_sus_colegios(client, entorno, cabeceras):
    doc = cabeceras["Docente"]

    suyo = client.get(URL, params={"id_colegio": entorno.colegios["Andino"].id_colegio}, headers=doc)
    ajeno = client.get(URL, params={"id_colegio": entorno.colegios["Lejano"].id_colegio}, headers=doc)
    general = client.get(URL, headers=doc)

    assert (suyo.status_code, suyo.json()["origen"]) == (200, "general")
    assert (ajeno.status_code, motivo(ajeno)) == (403, "colegio_fuera_de_alcance")
    assert (general.status_code, motivo(general)) == (403, "colegio_fuera_de_alcance")


def test_permisos(client, entorno, cabeceras):
    directivo, doc = cabeceras["Directivo"], cabeceras["Docente"]
    id_calendario = entorno.calendario.id_calendario
    escrituras = [
        lambda c: client.post(URL, json={}, headers=c),
        lambda c: client.post(f"{URL}/particulares", json={}, headers=c),
        lambda c: client.put(f"{URL}/{id_calendario}", json={}, headers=c),
        lambda c: client.post(f"{URL}/{id_calendario}/usar-general", headers=c),
    ]

    assert client.get(URL).status_code == 401
    assert motivo(client.get(URL, headers=directivo)) == "sin_permiso"
    for escribir in escrituras:
        assert escribir({}).status_code == 401
        assert (escribir(doc).status_code, escribir(directivo).status_code) == (403, 403)


# ── Crear el general ────────────────────────────────────────────────────────────────

def test_crear_el_general(client, db, entorno, sup):
    nuevo = anio_nuevo(db)
    rangos = rangos_relativos(HOY + timedelta(days=400))
    cuerpo = {"id_anio_escolar": nuevo.id_anio_escolar, "periodos": periodos(rangos),
              "dias_no_lectivos": [dia(rangos["PA2"][0] + timedelta(days=2), motivo_dia="Feriado regional")]}

    respuesta = client.post(URL, json=cuerpo, headers=sup)
    repetido = client.post(URL, json=cuerpo, headers=sup)

    assert respuesta.status_code == 201
    creado = respuesta.json()
    assert (creado["origen"], creado["version"], creado["periodo_actual"]) == ("general", 1, None)
    assert creado["dias_no_lectivos"][0]["origen"] == "manual"
    assert len(db.exec(select(PeriodoAcademico).where(PeriodoAcademico.id_anio_escolar == nuevo.id_anio_escolar)).all()) == 4
    assert [a.accion for a in auditoria(db, creado["id_calendario"])] == ["crear"]
    assert (repetido.status_code, motivo(repetido)) == (409, "calendario_existente")
    assert client.get(URL, params={"id_anio_escolar": nuevo.id_anio_escolar}, headers=sup).json() == creado


def cuerpo_nuevo(db, rangos=None, dias=()):
    nuevo = anio_nuevo(db)
    rangos = rangos or rangos_relativos(HOY + timedelta(days=400))
    return {"id_anio_escolar": nuevo.id_anio_escolar, "periodos": periodos(rangos), "dias_no_lectivos": list(dias)}


BASE_NUEVO = HOY + timedelta(days=400)


@pytest.mark.parametrize(
    "cambiar, campo, texto",
    [
        (lambda c: c["periodos"].pop(3), "periodos", "Faltan los periodos: PR2."),
        (lambda c: c["periodos"].append(dict(c["periodos"][0])), "periodos.8", "El periodo PA1 está repetido."),
        (lambda c: c["periodos"][2].update(fecha_fin=c["periodos"][2]["fecha_inicio"], fecha_inicio="2030-01-01"),
         "periodos.2.fecha_fin", "La fecha de fin no puede ser anterior a la fecha de inicio."),
        (lambda c: c["periodos"][1].update(fecha_inicio=c["periodos"][0]["fecha_fin"]),
         "periodos.1.fecha_inicio", "PR1 debe empezar después del fin de PA1"),
        (lambda c: c["dias_no_lectivos"].append(dia(date.fromisoformat(c["periodos"][1]["fecha_inicio"]))),
         "dias_no_lectivos.0", "Debe estar comprendido dentro de un PA del calendario."),
        (lambda c: c["dias_no_lectivos"].extend([dia(BASE_NUEVO, BASE_NUEVO + timedelta(days=2), "Uno"),
                                                 dia(BASE_NUEVO + timedelta(days=1), motivo_dia="Dos")]),
         "dias_no_lectivos.1", "Se superpone con el día no lectivo «Uno»."),
        (lambda c: c["dias_no_lectivos"].append(dia(BASE_NUEVO, BASE_NUEVO - timedelta(days=1))),
         "dias_no_lectivos.0.fecha_fin", "La fecha de fin no puede ser anterior a la fecha de inicio."),
        (lambda c: c["dias_no_lectivos"].append(dia(BASE_NUEVO, motivo_dia="   ")),
         "dias_no_lectivos.0.motivo", "No puede estar vacío."),
    ],
    ids=["falta_un_periodo", "repetido", "inicio_mayor_que_fin", "superpuestos", "no_lectivo_en_pr",
         "no_lectivos_superpuestos", "no_lectivo_invertido", "motivo_vacio"],
)
def test_crear_el_general_validaciones(client, db, entorno, sup, cambiar, campo, texto):
    cuerpo = cuerpo_nuevo(db)
    cambiar(cuerpo)

    respuesta = client.post(URL, json=cuerpo, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    errores = respuesta.json()["errores"]
    assert any(e["campo"] == campo and texto in e["mensaje"] for e in errores), errores
    assert db.exec(select(Calendario).where(Calendario.id_anio_escolar == cuerpo["id_anio_escolar"])).first() is None


def test_crear_el_general_de_un_anio_inexistente(client, entorno, sup):
    cuerpo = {"id_anio_escolar": 999, "periodos": periodos(rangos_relativos(HOY)), "dias_no_lectivos": []}

    assert motivo(client.post(URL, json=cuerpo, headers=sup)) == "anio_inexistente"


def test_el_pa1_no_choca_con_el_pr4_del_anio_anterior(client, db, entorno, sup):
    """El PR4 del entorno termina hoy+150: el PA1 del año siguiente debe empezar después."""
    rangos = rangos_relativos(HOY + timedelta(days=300))
    rangos["PA1"] = (entorno.fin("PR4"), rangos["PA1"][1])

    respuesta = client.post(URL, json=cuerpo_nuevo(db, rangos), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert respuesta.json()["errores"][0]["campo"] == "periodos.0.fecha_inicio"
    assert "PR4 del año escolar" in respuesta.json()["errores"][0]["mensaje"]


def test_el_pr4_no_choca_con_el_pa1_del_anio_siguiente(client, db, entorno, sup):
    siguiente = rangos_relativos(HOY + timedelta(days=400))
    crear_calendario(db, anio_nuevo(db).id_anio_escolar, siguiente, 1)
    db.commit()

    respuesta = editar(client, sup, entorno.calendario.id_calendario, con_cambio("PR4", fin=siguiente["PA1"][0]))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert respuesta.json()["errores"][0]["campo"] == "periodos.7.fecha_fin"


# ── Editar ──────────────────────────────────────────────────────────────────────────

def test_editar_con_la_version_vigente_y_auditoria(client, db, entorno, sup):
    id_calendario = entorno.calendario.id_calendario
    rangos = con_cambio("PA3", fin=entorno.fin("PA3") - timedelta(days=5))
    rangos["PR3"] = (entorno.fin("PA3") - timedelta(days=4), entorno.fin("PR3"))

    respuesta = editar(client, sup, id_calendario, rangos, dias=[dia(LUNES + timedelta(days=7))])

    assert respuesta.status_code == 200
    assert respuesta.json()["version"] == 2
    filas = auditoria(db, id_calendario)
    assert {(a.accion, a.campo) for a in filas} == {("editar", "periodos"), ("editar", "dias_no_lectivos")}
    cambio = next(a for a in filas if a.campo == "periodos")
    assert cambio.id_usuario == entorno.supervisor.id_usuario
    anterior, nuevo = json.loads(cambio.valor_anterior), json.loads(cambio.valor_nuevo)
    assert anterior[4]["periodo"] == "PA3"
    assert anterior[4] != nuevo[4]


def test_editar_con_version_vieja_es_409(client, entorno, sup):
    id_calendario = entorno.calendario.id_calendario
    assert editar(client, sup, id_calendario, rangos_relativos(HOY), dias=[dia(LUNES)]).status_code == 200

    respuesta = editar(client, sup, id_calendario, rangos_relativos(HOY), version=1)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "modificacion_concurrente")
    assert respuesta.json()["detail"] == (
        "El calendario fue modificado por otro Supervisor. Consulta la versión actual antes de volver a guardar."
    )


def test_editar_sin_cambios_no_sube_la_version_ni_audita(client, db, entorno, sup):
    respuesta = editar(client, sup, entorno.calendario.id_calendario, rangos_relativos(HOY))

    assert respuesta.json()["version"] == 1
    assert auditoria(db, entorno.calendario.id_calendario) == []


def test_editar_conserva_el_origen_de_los_dias_que_no_cambian(client, entorno, sup, db):
    agregar_no_lectivo(db, entorno.calendario, LUNES, motivo="Feriado nacional: X", origen=ORIGEN_AUTOMATICO)

    respuesta = editar(client, sup, entorno.calendario.id_calendario, rangos_relativos(HOY),
                       dias=[dia(LUNES, motivo_dia="Feriado nacional: X"), dia(HOY, motivo_dia="Paro")])

    assert {(d["motivo"], d["origen"]) for d in respuesta.json()["dias_no_lectivos"]} == {
        ("Feriado nacional: X", "automatico"), ("Paro", "manual")
    }


def test_editar_inexistente(client, entorno, sup):
    respuesta = editar(client, sup, 999, rangos_relativos(HOY))

    assert (respuesta.status_code, motivo(respuesta)) == (404, "calendario_no_encontrado")


# ── Impacto sobre las asistencias ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rangos, dias, esperado",
    [
        (lambda: rangos_relativos(HOY), [dia(LUNES)], "no_lectiva"),
        (lambda: {**con_cambio("PA2", inicio=LUNES + timedelta(days=1)),
                  "PR1": (rangos_relativos(HOY)["PR1"][0], LUNES)}, [], "fuera_de_pa"),
    ],
    ids=["dia_no_lectivo", "fuera_de_su_pa"],
)
def test_no_se_guarda_un_cambio_incompatible_con_asistencias(client, db, entorno, sup, rangos, dias, esperado):
    asistencia_en(db, entorno, LUNES)

    respuesta = editar(client, sup, entorno.calendario.id_calendario, rangos(), dias=dias)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_incompatible")
    assert respuesta.json()["incompatibilidades"] == [{
        "colegio": {"id": entorno.colegios["Andino"].id_colegio, "nombre": "Colegio Andino"},
        "fecha": LUNES.isoformat(), "tipo_registro": "asistencia", "motivo": esperado,
        "mensaje": respuesta.json()["incompatibilidades"][0]["mensaje"],
    }]
    assert releer(db, Calendario, entorno.calendario.id_calendario).version == 1


def test_cambiar_el_pa_de_una_fecha_con_asistencias_es_incompatible(client, db, entorno, sup):
    """Mover el fin del PA1 hasta una fecha con asistencias del PA2 la cambiaría de PA."""
    fecha = entorno.inicio("vigente") + timedelta(days=1)
    asistencia_en(db, entorno, fecha)
    rangos = con_cambio("PA1", fin=fecha + timedelta(days=1))
    rangos["PR1"] = (fecha + timedelta(days=2), fecha + timedelta(days=3))
    rangos["PA2"] = (fecha + timedelta(days=4), entorno.fin("vigente"))

    respuesta = editar(client, sup, entorno.calendario.id_calendario, rangos)

    assert [i["motivo"] for i in respuesta.json()["incompatibilidades"]] == ["cambia_de_pa"]


def test_el_general_no_evalua_a_colegios_con_particular(client, db, entorno, sup):
    """Lejano tiene fechas particulares: sus asistencias no frenan un cambio del general."""
    assert particular(client, entorno, sup).status_code == 201
    asistencia_en(db, entorno, LUNES, colegio="Lejano")

    respuesta = editar(client, sup, entorno.calendario.id_calendario, rangos_relativos(HOY), dias=[dia(LUNES)])

    assert respuesta.status_code == 200


# ── Fechas particulares y volver al general ─────────────────────────────────────────

def test_crear_particular_copia_los_dias_del_general(client, db, entorno, sup):
    """Copia los días del general con su origen, salvo los que quedan fuera de sus PA (el
    del día +15 cae en su PR2)."""
    agregar_no_lectivo(db, entorno.calendario, LUNES, motivo="Feriado nacional: X", origen=ORIGEN_AUTOMATICO)
    agregar_no_lectivo(db, entorno.calendario, HOY + timedelta(days=15), motivo="Aniversario")
    rangos = con_cambio("PA2", fin=HOY + timedelta(days=10))
    rangos["PR2"] = (HOY + timedelta(days=11), entorno.fin("PR2"))

    respuesta = particular(client, entorno, sup, rangos)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert (cuerpo["origen"], cuerpo["colegio"]["nombre"], cuerpo["version"]) == ("particular", "Colegio Lejano", 1)
    assert cuerpo["dias_no_lectivos"] == [dia(LUNES, motivo_dia="Feriado nacional: X") | {"origen": "automatico"}]
    assert cuerpo["periodos"][2]["fecha_fin"] == (HOY + timedelta(days=10)).isoformat()
    assert [a.accion for a in auditoria(db, cuerpo["id_calendario"])] == ["crear"]
    lejano = {"id_colegio": entorno.colegios["Lejano"].id_colegio}
    assert client.get(URL, params=lejano, headers=sup).json()["origen"] == "particular"
    andino = {"id_colegio": entorno.colegios["Andino"].id_colegio}
    assert client.get(URL, params=andino, headers=sup).json()["origen"] == "general"


def test_crear_particular_con_sus_propios_dias(client, entorno, sup, db):
    agregar_no_lectivo(db, entorno.calendario, LUNES)

    respuesta = particular(client, entorno, sup, dias=[])

    assert respuesta.json()["dias_no_lectivos"] == []


def test_crear_particular_errores(client, db, entorno, sup):
    assert particular(client, entorno, sup).status_code == 201
    repetido = particular(client, entorno, sup)
    nuevo = anio_nuevo(db)
    sin_general = client.post(f"{URL}/particulares", json={
        "id_colegio": entorno.colegios["Andino"].id_colegio, "id_anio_escolar": nuevo.id_anio_escolar,
        "periodos": periodos(rangos_relativos(HOY + timedelta(days=400))),
    }, headers=sup)
    sin_colegio = client.post(f"{URL}/particulares", json={
        "id_colegio": 999, "id_anio_escolar": entorno.anio.id_anio_escolar, "periodos": periodos(rangos_relativos(HOY)),
    }, headers=sup)

    assert (repetido.status_code, motivo(repetido)) == (409, "calendario_particular_existente")
    assert (sin_general.status_code, motivo(sin_general)) == (409, "calendario_general_requerido")
    assert (sin_colegio.status_code, motivo(sin_colegio)) == (422, "colegio_inexistente")


def test_crear_particular_incompatible_con_las_asistencias_del_colegio(client, db, entorno, sup):
    asistencia_en(db, entorno, LUNES, colegio="Lejano")

    respuesta = particular(client, entorno, sup, dias=[dia(LUNES)])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_incompatible")
    assert respuesta.json()["incompatibilidades"][0]["colegio"]["nombre"] == "Colegio Lejano"


def test_editar_un_particular(client, db, entorno, sup):
    creado = particular(client, entorno, sup).json()

    respuesta = editar(client, sup, creado["id_calendario"], rangos_relativos(HOY), dias=[dia(LUNES)])

    assert (respuesta.status_code, respuesta.json()["version"], respuesta.json()["origen"]) == (200, 2, "particular")
    assert releer(db, Calendario, entorno.calendario.id_calendario).version == 1


def test_usar_general(client, db, entorno, sup):
    creado = particular(client, entorno, sup).json()
    url = f"{URL}/{creado['id_calendario']}/usar-general"

    respuesta = client.post(url, json={"version": 1}, headers=sup)
    otra_vez = client.post(url, headers=sup)

    assert respuesta.status_code == 200
    assert (respuesta.json()["origen"], respuesta.json()["id_calendario"]) == ("general", entorno.calendario.id_calendario)
    fila = releer(db, Calendario, creado["id_calendario"])
    assert (fila.activo, fila.version) == (False, 2)  # se conserva como historial
    assert [(a.accion, a.campo) for a in auditoria(db, creado["id_calendario"])] == [("crear", None), ("inactivar", "activo")]
    assert (otra_vez.status_code, motivo(otra_vez)) == (409, "calendario_inactivo")
    # Puede volver a tener fechas particulares.
    assert particular(client, entorno, sup).status_code == 201


def test_usar_general_errores(client, db, entorno, sup):
    creado = particular(client, entorno, sup).json()

    del_general = client.post(f"{URL}/{entorno.calendario.id_calendario}/usar-general", headers=sup)
    inexistente = client.post(f"{URL}/999/usar-general", headers=sup)
    version_vieja = client.post(f"{URL}/{creado['id_calendario']}/usar-general", json={"version": 7}, headers=sup)

    assert (del_general.status_code, motivo(del_general)) == (409, "calendario_es_general")
    assert (inexistente.status_code, motivo(inexistente)) == (404, "calendario_no_encontrado")
    assert (version_vieja.status_code, motivo(version_vieja)) == (409, "modificacion_concurrente")


def test_usar_general_incompatible_con_las_asistencias(client, db, entorno, sup):
    """El lunes es no lectivo en el general pero no en el particular de Lejano, que ya
    tiene asistencias ese día: no puede volver al general."""
    creado = particular(client, entorno, sup, dias=[]).json()
    agregar_no_lectivo(db, entorno.calendario, LUNES)
    asistencia_en(db, entorno, LUNES, colegio="Lejano")

    respuesta = client.post(f"{URL}/{creado['id_calendario']}/usar-general", headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_incompatible")
    assert releer(db, Calendario, creado["id_calendario"]).activo is True


def test_editar_un_particular_inactivo(client, entorno, sup):
    creado = particular(client, entorno, sup).json()
    client.post(f"{URL}/{creado['id_calendario']}/usar-general", headers=sup)

    respuesta = editar(client, sup, creado["id_calendario"], rangos_relativos(HOY), version=2)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_inactivo")
