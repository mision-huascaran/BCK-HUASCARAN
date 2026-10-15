"""Asistencia por clase de los alumnos (CU022) y su % en el detalle del alumno (CU015)."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.evaluacion import Asistencia
from app.models.organizacion import Alumno, DiaNoLaborable
from app.models.trazabilidad import Auditoria
from app.services import asistencia_service
from tests.conftest import a_las, cabecera_de, crear_alumno, crear_colegio, iso, motivo, releer

# Miércoles. El periodo vigente va del lunes 18 de mayo al viernes 17 de julio; el pasado
# termina el 18 de abril.
HOY = date(2026, 6, 17)
LUNES, MARTES = date(2026, 6, 15), date(2026, 6, 16)
SABADO, FERIADO, MANANA = date(2026, 6, 13), date(2026, 6, 12), date(2026, 6, 18)
DEL_PERIODO_PASADO = date(2026, 3, 10)  # martes
ENTRE_PERIODOS = date(2026, 5, 5)  # martes
NO_LECTIVA = "La fecha seleccionada no corresponde a un día lectivo válido del periodo vigente."


@pytest.fixture(autouse=True)
def miercoles(reloj, db):
    """Fija el reloj antes de armar el entorno (el calendario es relativo a hoy) y marca
    el viernes 12 como feriado."""
    reloj.fijar(a_las(HOY, 9))
    db.add(DiaNoLaborable(fecha=FERIADO, motivo="Feriado", creado_por=1, creado_en=ahora_utc()))
    db.commit()
    return reloj


def clase(entorno, fecha=HOY, colegio="Andino", grado="2.º", programa="Comprensión Lectora") -> dict:
    return {
        "id_colegio": entorno.colegios[colegio].id_colegio,
        "id_grado": entorno.grados[grado],
        "id_programa": entorno.programas[programa],
        "fecha": fecha.isoformat(),
    }


def consultar(client, cabecera, params):
    return client.get("/asistencias", params=params, headers=cabecera)


def guardar(client, cabecera, registros, fecha=HOY, **extra):
    cuerpo = {"fecha": fecha.isoformat(), "registros": registros, **extra}
    return client.put("/asistencias", json=cuerpo, headers=cabecera)


def marca(entorno, alumno, estado="presente", **campos) -> dict:
    return {"id_alumno": entorno.alumnos[alumno].id_alumno, "estado": estado, **campos}


def filas(db) -> list[Asistencia]:
    db.expire_all()
    return db.exec(select(Asistencia).order_by(Asistencia.id)).all()


def alumno_de_la_clase(cuerpo, entorno, alumno) -> dict:
    return next(a for a in cuerpo["alumnos"] if a["id_alumno"] == entorno.alumnos[alumno].id_alumno)


# ── Consulta ────────────────────────────────────────────────────────────────────────

def test_cu022_docente_lee_su_clase_sin_actividad(client, entorno, cabeceras):
    respuesta = consultar(client, cabeceras["Docente"], clase(entorno))

    assert respuesta.status_code == 200
    # Solo activos del colegio, grado y subprograma (Iván está inactivo y sin registro).
    assert respuesta.json() == {
        "fecha": HOY.isoformat(),
        "editable": True,
        "alumnos": [{
            "id_alumno": entorno.alumnos["ana"].id_alumno, "nombres": "Ana", "apellidos": "Torres",
            "activo": True, "en_aula": True, "estado": None, "observacion": None,
        }],
    }


def test_cu022_ordena_por_apellidos_y_muestra_lo_registrado(client, db, entorno, cabeceras, actividad):
    crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["2.º"],
                 entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario)
    db.commit()
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana", "tardanza", observacion="Llegó 8:20")])

    alumnos = consultar(client, cabeceras["Docente"], clase(entorno)).json()["alumnos"]

    assert [(a["apellidos"], a["estado"], a["observacion"]) for a in alumnos] == [
        ("Abad", None, None), ("Torres", "tardanza", "Llegó 8:20"),
    ]


def test_cu022_docente_fuera_de_su_alcance(client, entorno, cabeceras):
    respuesta = consultar(client, cabeceras["Docente"], clase(entorno, colegio="Lejano", grado="3.º"))

    assert (respuesta.status_code, motivo(respuesta)) == (403, "aula_fuera_de_alcance")


def test_cu022_supervisor_solo_lee_y_directivo_no_tiene_acceso(client, entorno, cabeceras):
    params = clase(entorno, colegio="Lejano", grado="3.º")

    supervisor = consultar(client, cabeceras["Supervisor"], params)

    assert supervisor.status_code == 200
    assert supervisor.json()["editable"] is False
    assert [a["nombres"] for a in supervisor.json()["alumnos"]] == ["Mario"]
    assert consultar(client, cabeceras["Directivo"], params).status_code == 403


@pytest.mark.parametrize("caso", ["colegio_inexistente", "grado_no_ofrecido", "programa_no_ofrecido"])
def test_cu022_consulta_invalida(client, db, entorno, cabeceras, caso):
    params = clase(entorno)
    if caso == "colegio_inexistente":
        params["id_colegio"] = 999
    else:
        chico = crear_colegio(db, "Colegio Chico", [entorno.grados["1.º"]], [entorno.programas["Alfabetización"]],
                              entorno.supervisor.id_usuario)
        db.commit()
        params["id_colegio"] = chico.id_colegio
        if caso == "programa_no_ofrecido":
            params["id_grado"] = entorno.grados["1.º"]

    respuesta = consultar(client, cabeceras["Supervisor"], params)

    assert (respuesta.status_code, motivo(respuesta)) == (422, caso)


@pytest.mark.parametrize("filtro", ["fecha", "id_programa"])
def test_cu022_los_cuatro_filtros_son_obligatorios(client, entorno, cabeceras, filtro):
    params = clase(entorno)
    del params[filtro]

    respuesta = consultar(client, cabeceras["Supervisor"], params)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert respuesta.json()["errores"][0]["campo"] == filtro


@pytest.mark.parametrize(
    "fecha",
    [SABADO, FERIADO, MANANA, DEL_PERIODO_PASADO, ENTRE_PERIODOS],
    ids=["fin_de_semana", "feriado", "futura", "periodo_pasado", "entre_periodos"],
)
def test_cu022_docente_solo_lee_dias_lectivos_del_periodo_vigente(client, entorno, cabeceras, fecha):
    respuesta = consultar(client, cabeceras["Docente"], clase(entorno, fecha))

    assert (respuesta.status_code, motivo(respuesta)) == (422, "fecha_no_lectiva")
    assert respuesta.json()["detail"] == NO_LECTIVA


@pytest.mark.parametrize("fecha", [LUNES, MARTES, HOY], ids=["lunes", "martes", "hoy"])
def test_cu022_dias_lectivos_del_periodo_vigente_son_editables(client, entorno, cabeceras, fecha):
    assert consultar(client, cabeceras["Docente"], clase(entorno, fecha)).json()["editable"] is True


@pytest.mark.parametrize(
    "fecha, codigo",
    [(DEL_PERIODO_PASADO, 200), (LUNES, 200), (SABADO, 422), (FERIADO, 422), (MANANA, 422), (ENTRE_PERIODOS, 422)],
)
def test_cu022_supervisor_lee_cualquier_dia_lectivo_pasado(client, entorno, cabeceras, fecha, codigo):
    respuesta = consultar(client, cabeceras["Supervisor"], clase(entorno, fecha))

    assert respuesta.status_code == codigo
    if codigo == 200:
        assert respuesta.json()["editable"] is False
    else:
        assert motivo(respuesta) == "fecha_no_lectiva"


def test_cu022_sin_periodo_vigente_el_docente_no_lee_ni_registra(client, db, entorno, cabeceras, actividad):
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = HOY - timedelta(days=1)
    db.add(vigente)
    db.commit()

    # Sin periodo vigente el docente no tiene alcance (como en Alumnos): 403 antes que la fecha.
    assert motivo(consultar(client, cabeceras["Docente"], clase(entorno, MARTES))) == "aula_fuera_de_alcance"
    assert motivo(guardar(client, cabeceras["Docente"], [marca(entorno, "ana")], fecha=MARTES)) == "fecha_no_lectiva"
    assert consultar(client, cabeceras["Supervisor"], clase(entorno, MARTES)).status_code == 200


def test_cu022_incluye_a_quien_tiene_registro_aunque_hoy_este_inactivo_o_en_otro_subprograma(
    client, db, entorno, cabeceras, actividad
):
    docente = cabeceras["Docente"]
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario)
    db.commit()
    guardar(client, docente, [marca(entorno, "ana", "ausente"), {"id_alumno": beto.id_alumno, "estado": "presente"}],
            fecha=LUNES)
    ana = releer(db, Alumno, entorno.alumnos["ana"].id_alumno)
    ana.activo = False
    beto = releer(db, Alumno, beto.id_alumno)
    beto.id_programa_actual = entorno.programas["Alfabetización"]
    db.add_all([ana, beto])
    db.commit()

    del_lunes = consultar(client, docente, clase(entorno, LUNES)).json()["alumnos"]
    de_hoy = consultar(client, docente, clase(entorno)).json()["alumnos"]

    assert [(a["nombres"], a["activo"], a["en_aula"], a["estado"]) for a in del_lunes] == [
        ("Beto", True, False, "presente"), ("Ana", False, True, "ausente"),
    ]
    assert de_hoy == []


# ── Registro ────────────────────────────────────────────────────────────────────────

def test_cu022_registrar_exige_actividad(client, entorno, cabeceras):
    respuesta = guardar(client, cabeceras["Docente"], [marca(entorno, "ana")])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_requerida")
    assert respuesta.json()["detail"] == "Debe iniciar una actividad para realizar cambios."


@pytest.mark.parametrize("rol", ["Supervisor", "Directivo"])
def test_cu022_solo_el_docente_registra(client, entorno, cabeceras, rol):
    respuesta = guardar(client, cabeceras[rol], [marca(entorno, "ana")])

    assert (respuesta.status_code, motivo(respuesta)) == (403, "sin_permiso")


def test_cu022_marcar_todos_y_cambiar_uno_cuenta_un_cambio_por_alumno(client, db, entorno, cabeceras, actividad):
    docente = cabeceras["Docente"]
    luis, ana = entorno.alumnos["luis"], entorno.alumnos["ana"]

    respuesta = guardar(client, docente, [marca(entorno, "luis"), marca(entorno, "ana")])

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "fecha": HOY.isoformat(),
        "registros": [
            {"id_alumno": luis.id_alumno, "aplicado": True, "estado": "presente", "observacion": None},
            {"id_alumno": ana.id_alumno, "aplicado": True, "estado": "presente", "observacion": None},
        ],
    }
    # Cada uno en la clase de su subprograma actual.
    assert [(f.id_alumno, f.fecha, f.id_programa, f.estado, f.id_docente) for f in filas(db)] == [
        (luis.id_alumno, HOY, entorno.programas["Alfabetización"], "presente", entorno.id_docente),
        (ana.id_alumno, HOY, entorno.programas["Comprensión Lectora"], "presente", entorno.id_docente),
    ]

    guardar(client, docente, [marca(entorno, "ana", "ausente")])
    guardar(client, docente, [marca(entorno, "ana", "presente")])
    guardar(client, docente, [marca(entorno, "ana", "presente")])  # sin cambio: no audita

    auditoria = db.exec(select(Auditoria.accion, Auditoria.campo).where(Auditoria.tabla == "asistencia")).all()
    assert sorted(auditoria, key=str) == sorted(
        [("crear", None), ("crear", None), ("editar", "estado"), ("editar", "estado")], key=str
    )
    fila_ana = next(f for f in filas(db) if f.id_alumno == ana.id_alumno)
    assert (fila_ana.estado, fila_ana.modificado_por) == ("presente", entorno.docente.id_usuario)

    detalle = client.get(f"/actividades/{actividad['id']}", headers=docente).json()
    assert detalle["cambios"] == 2
    assert [(m["modulo"], m["total"]) for m in detalle["cambios_por_modulo"]] == [("asistencia", 2)]


def test_cu021_productividad_en_plural_aunque_sea_uno(client, db, entorno, cabeceras, actividad):
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana")])
    sup = cabeceras["Supervisor"]

    historial = client.get(f"/seguimiento/docentes/{entorno.id_docente}/actividades", headers=sup).json()

    assert historial["items"][0]["productividad_texto"] == "1 Asistencias"


def test_cu022_observacion_del_modal_sin_tocar_el_estado(client, db, entorno, cabeceras, actividad):
    docente = cabeceras["Docente"]
    ana = entorno.alumnos["ana"].id_alumno

    solo_observacion = guardar(client, docente, [{"id_alumno": ana, "observacion": "  Vino con su mamá "}])
    estado = guardar(client, docente, [{"id_alumno": ana, "estado": "ausente"}])

    assert solo_observacion.json()["registros"][0] == {
        "id_alumno": ana, "aplicado": True, "estado": None, "observacion": "Vino con su mamá",
    }
    assert (filas(db)[0].estado, filas(db)[0].observacion) == ("ausente", "Vino con su mamá")
    assert estado.json()["registros"][0]["observacion"] == "Vino con su mamá"

    guardar(client, docente, [{"id_alumno": ana, "observacion": "  "}])
    assert filas(db)[0].observacion is None
    guardar(client, docente, [{"id_alumno": ana, "observacion": "Otra"}])
    guardar(client, docente, [{"id_alumno": ana, "observacion": None}])
    assert (filas(db)[0].estado, filas(db)[0].observacion) == ("ausente", None)
    campos = db.exec(select(Auditoria.campo).where(Auditoria.accion == "editar").order_by(Auditoria.id_auditoria)).all()
    assert campos == ["estado", "observacion", "observacion", "observacion"]


@pytest.mark.parametrize(
    "registro, campo",
    [
        ({}, "registros.0"),
        ({"estado": None}, "registros.0.estado"),
        ({"estado": "justificado"}, "registros.0.estado"),
    ],
    ids=["sin_estado_ni_observacion", "estado_null", "estado_no_permitido"],
)
def test_cu022_registro_invalido(client, db, entorno, cabeceras, actividad, registro, campo):
    respuesta = guardar(client, cabeceras["Docente"], [{"id_alumno": entorno.alumnos["ana"].id_alumno, **registro}])

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert [e["campo"] for e in respuesta.json()["errores"]] == [campo]
    assert filas(db) == []


@pytest.mark.parametrize("fecha", [SABADO, FERIADO, MANANA, DEL_PERIODO_PASADO])
def test_cu022_fecha_no_lectiva_no_se_registra(client, db, entorno, cabeceras, actividad, fecha):
    respuesta = guardar(client, cabeceras["Docente"], [marca(entorno, "ana")], fecha=fecha)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "fecha_no_lectiva")
    assert filas(db) == []


def test_cu022_registra_una_clase_pasada_del_periodo_vigente(client, db, entorno, cabeceras, actividad):
    respuesta = guardar(client, cabeceras["Docente"], [marca(entorno, "ana", "tardanza")], fecha=LUNES)

    assert respuesta.status_code == 200
    assert (filas(db)[0].fecha, filas(db)[0].estado) == (LUNES, "tardanza")


def test_cu022_todo_o_nada_con_un_error_por_alumno(client, db, entorno, cabeceras, actividad):
    registros = [
        marca(entorno, "ana"),
        marca(entorno, "mario"),  # fuera del alcance
        marca(entorno, "inactivo"),
        {"id_alumno": 999, "estado": "presente"},
        marca(entorno, "ana", "ausente"),  # repetido
    ]

    respuesta = guardar(client, cabeceras["Docente"], registros)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert [e["campo"] for e in respuesta.json()["errores"]] == [
        "registros.1.id_alumno", "registros.2.id_alumno", "registros.3.id_alumno", "registros.4.id_alumno",
    ]
    assert filas(db) == []


def test_cu022_registros_vacios(client, entorno, cabeceras, actividad):
    respuesta = guardar(client, cabeceras["Docente"], [])

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


def test_cu022_fecha_de_registro_en_el_futuro(client, db, entorno, cabeceras, actividad):
    respuesta = guardar(client, cabeceras["Docente"], [marca(entorno, "ana")],
                        fecha_registro=iso(ahora_utc() + timedelta(minutes=3)))

    assert (respuesta.status_code, respuesta.json()["errores"][0]["campo"]) == (422, "fecha_registro")
    assert filas(db) == []


def test_cu022_gana_la_captura_mas_reciente(client, db, entorno, cabeceras, actividad):
    """Dos dispositivos: llega primero la captura de las 8:30 y después una de las 8:10
    (sincronizada tarde). La de las 8:10 no se aplica; una de las 8:45 sí."""
    docente = cabeceras["Docente"]
    reciente, vieja = a_las(HOY, 8, 30), a_las(HOY, 8, 10)
    guardar(client, docente, [marca(entorno, "ana", "tardanza")], fecha_registro=iso(reciente))

    tarde = guardar(client, docente, [marca(entorno, "ana", "ausente", observacion="No vino")], fecha_registro=iso(vieja))

    assert tarde.status_code == 200
    assert tarde.json()["registros"] == [{
        "id_alumno": entorno.alumnos["ana"].id_alumno, "aplicado": False, "estado": "tardanza", "observacion": None,
    }]
    fila = filas(db)[0]
    assert (fila.estado, fila.observacion, fila.fecha_registro) == ("tardanza", None, reciente)
    assert db.exec(select(Auditoria).where(Auditoria.accion == "editar")).all() == []

    posterior = guardar(client, docente, [marca(entorno, "ana", "presente")], fecha_registro=iso(a_las(HOY, 8, 45)))

    assert posterior.json()["registros"][0]["aplicado"] is True
    assert (filas(db)[0].estado, filas(db)[0].fecha_registro) == ("presente", a_las(HOY, 8, 45))


def test_cu022_creada_a_la_vez_por_otra_peticion_se_trata_como_edicion(client, db, entorno, cabeceras,
                                                                       actividad, monkeypatch):
    """Si la fila aparece entre la lectura y el INSERT, el INSERT no choca: se edita."""
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana", "presente")])
    reales = asistencia_service._existentes
    llamadas = []

    def sin_ver_la_primera_vez(*args):
        llamadas.append(args)
        return {} if len(llamadas) == 1 else reales(*args)

    monkeypatch.setattr(asistencia_service, "_existentes", sin_ver_la_primera_vez)
    respuesta = guardar(client, cabeceras["Docente"], [marca(entorno, "ana", "ausente")])

    assert respuesta.status_code == 200
    assert [f.estado for f in filas(db)] == ["ausente"]


# ── CU015: % de asistencia ──────────────────────────────────────────────────────────

def test_cu015_porcentaje_sobre_las_clases_dictadas_y_la_tardanza_cuenta(client, db, entorno, cabeceras, actividad):
    """Clases dictadas a 2.º de Comprensión Lectora del Andino: lunes, martes y hoy (el
    10, con solo una observación, no es una clase dictada). Ana: tardanza el lunes,
    ausente el martes y sin registro hoy -> 1 de 3."""
    docente = cabeceras["Docente"]
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario).id_alumno
    db.commit()
    guardar(client, docente, [marca(entorno, "ana", "tardanza"), {"id_alumno": beto, "estado": "presente"}], fecha=LUNES)
    guardar(client, docente, [marca(entorno, "ana", "ausente"), {"id_alumno": beto, "estado": "presente"}], fecha=MARTES)
    guardar(client, docente, [{"id_alumno": beto, "estado": "presente"}])
    guardar(client, docente, [{"id_alumno": beto, "observacion": "Solo observación"}], fecha=date(2026, 6, 10))

    resumen = client.get(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/resumen", headers=docente).json()
    de_beto = client.get(f"/alumnos/{beto}/resumen", headers=docente).json()

    assert (resumen["asistencia_porcentaje"], de_beto["asistencia_porcentaje"]) == (33.3, 100.0)


def test_cu015_sin_clases_dictadas_es_cero(client, entorno, cabeceras):
    resumen = client.get(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/resumen", headers=cabeceras["Docente"]).json()

    assert resumen["asistencia_porcentaje"] == 0.0


def test_cu022_la_supervisora_ve_lo_que_registro_el_docente_en_otra_sesion(client, db, entorno, cabeceras, actividad):
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana", "ausente")], fecha=MARTES)

    cuerpo = consultar(client, cabecera_de(db, entorno.supervisor), clase(entorno, MARTES)).json()

    assert alumno_de_la_clase(cuerpo, entorno, "ana")["estado"] == "ausente"


# ── El registro guarda su aula: rotar a un alumno no muda sus asistencias ──────────

def rotar(db, entorno, alumno, colegio="Andino", grado="1.º") -> None:
    """El Supervisor cambia de colegio o de grado al alumno (CU014)."""
    fila = releer(db, Alumno, entorno.alumnos[alumno].id_alumno)
    fila.id_colegio, fila.id_grado = entorno.colegios[colegio].id_colegio, entorno.grados[grado]
    db.add(fila)
    db.commit()


def test_cu022_el_registro_guarda_el_aula_del_alumno(client, db, entorno, cabeceras, actividad):
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana")], fecha=LUNES)

    fila = filas(db)[0]
    assert (fila.id_colegio, fila.id_grado, fila.id_programa) == (
        entorno.colegios["Andino"].id_colegio, entorno.grados["2.º"], entorno.programas["Comprensión Lectora"],
    )


@pytest.mark.parametrize("colegio, grado", [("Andino", "1.º"), ("Lejano", "2.º")], ids=["de_grado", "de_colegio"])
def test_cu022_al_rotar_el_aula_original_lo_muestra_fuera_del_aula(client, db, entorno, cabeceras, actividad,
                                                                     colegio, grado):
    """Ana se registró el lunes en 2.º y rota: el lunes de 2.º la sigue mostrando
    (`en_aula: false`) y hoy ya no la lista; su aula nueva sí, desde hoy."""
    docente, sup = cabeceras["Docente"], cabeceras["Supervisor"]
    guardar(client, docente, [marca(entorno, "ana", "tardanza")], fecha=LUNES)

    rotar(db, entorno, "ana", colegio, grado)

    del_lunes = consultar(client, sup, clase(entorno, LUNES)).json()["alumnos"]
    de_hoy = consultar(client, sup, clase(entorno)).json()["alumnos"]
    nueva_hoy = consultar(client, sup, clase(entorno, colegio=colegio, grado=grado)).json()["alumnos"]
    assert [(a["nombres"], a["activo"], a["en_aula"], a["estado"]) for a in del_lunes] == [("Ana", True, False, "tardanza")]
    assert de_hoy == []
    assert ("Ana", True, None) in [(a["nombres"], a["en_aula"], a["estado"]) for a in nueva_hoy]


def test_cu022_quien_ya_tiene_registro_ese_dia_en_otra_aula_no_aparece_en_la_nueva(client, db, entorno, cabeceras,
                                                                                     actividad):
    """Ana se registró el lunes en 2.º y ese mismo día pasó a 1.º: en el lunes de 1.º no
    aparece (su asistencia de ese día ya se tomó en 2.º)."""
    guardar(client, cabeceras["Docente"], [marca(entorno, "ana")], fecha=LUNES)
    rotar(db, entorno, "ana", grado="1.º")

    lunes_de_primero = consultar(client, cabeceras["Docente"], clase(entorno, LUNES, grado="1.º")).json()["alumnos"]

    assert [a["nombres"] for a in lunes_de_primero] == []


def test_cu022_editar_un_registro_existente_no_lo_muda(client, db, entorno, cabeceras, actividad):
    docente = cabeceras["Docente"]
    guardar(client, docente, [marca(entorno, "ana", "presente")], fecha=LUNES)
    rotar(db, entorno, "ana", grado="1.º")

    respuesta = guardar(client, docente, [marca(entorno, "ana", "ausente")], fecha=LUNES)

    assert respuesta.json()["registros"][0]["aplicado"] is True
    fila = filas(db)[0]
    assert (len(filas(db)), fila.estado, fila.id_grado) == (1, "ausente", entorno.grados["2.º"])


def test_cu022_el_alcance_se_valida_contra_el_aula_del_registro(client, db, entorno, cabeceras, actividad):
    """Ana rota al Lejano (fuera del alcance): su registro del lunes (Andino 2.º) se puede
    corregir, pero no puede registrarse una clase nueva suya. Al revés, un registro del
    Lejano no lo puede tocar aunque Ana vuelva al Andino."""
    docente = cabeceras["Docente"]
    guardar(client, docente, [marca(entorno, "ana", "presente")], fecha=LUNES)
    rotar(db, entorno, "ana", colegio="Lejano", grado="2.º")

    corregir = guardar(client, docente, [marca(entorno, "ana", "tardanza")], fecha=LUNES)
    nueva = guardar(client, docente, [marca(entorno, "ana")], fecha=MARTES)

    assert corregir.status_code == 200
    assert (nueva.status_code, nueva.json()["errores"][0]["mensaje"]) == (
        422, "El alumno no pertenece a un colegio y grado que tenga asignados.",
    )
    guardar_en_otra_aula = Asistencia(
        id_alumno=entorno.alumnos["ana"].id_alumno, fecha=MARTES, id_programa=entorno.programas["Comprensión Lectora"],
        id_colegio=entorno.colegios["Lejano"].id_colegio, id_grado=entorno.grados["2.º"], estado="presente",
        id_docente=entorno.id_docente, fecha_registro=ahora_utc(), creado_por=1, creado_en=ahora_utc(),
    )
    db.add(guardar_en_otra_aula)
    db.commit()
    rotar(db, entorno, "ana", colegio="Andino", grado="2.º")

    ajena = guardar(client, docente, [marca(entorno, "ana", "ausente")], fecha=MARTES)

    assert (ajena.status_code, ajena.json()["errores"][0]["mensaje"]) == (
        422, "Su asistencia de esa fecha es de un colegio y grado que no tiene asignados.",
    )


def test_cu015_el_porcentaje_usa_el_aula_guardada(client, db, entorno, cabeceras, actividad):
    """Ana y Beto (2.º): lunes Ana tardanza y Beto presente; martes solo Beto. Ana: 1 de 2.
    Ana rota a 1.º: allí no hubo clases (0 %), y en 2.º siguen siendo 2 clases para Beto."""
    docente = cabeceras["Docente"]
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["2.º"],
                        entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario).id_alumno
    db.commit()
    guardar(client, docente, [marca(entorno, "ana", "tardanza"), {"id_alumno": beto, "estado": "presente"}], fecha=LUNES)
    guardar(client, docente, [{"id_alumno": beto, "estado": "presente"}], fecha=MARTES)
    porcentaje = lambda a: client.get(f"/alumnos/{a}/resumen", headers=docente).json()["asistencia_porcentaje"]  # noqa: E731
    ana = entorno.alumnos["ana"].id_alumno
    assert (porcentaje(ana), porcentaje(beto)) == (50.0, 100.0)

    rotar(db, entorno, "ana", grado="1.º")

    assert (porcentaje(ana), porcentaje(beto)) == (0.0, 100.0)
