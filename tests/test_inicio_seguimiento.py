"""Inicio por rol (CU010, CU011, CU012) y seguimiento de docentes (CU020, CU021)."""
import uuid
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.organizacion import DiaNoLaborable, DocenteColegioGrado, PeriodoAcademico
from app.services import inicio_service
from app.services.calendario import periodo_de_referencia_del_anio
from tests.conftest import a_las, asignar, cabecera_de, crear_docente, iniciar_actividad, motivo

# Miércoles. Las alertas cuentan días hábiles hasta ayer (martes 16).
MIERCOLES = date(2026, 6, 17)
INACTIVIDAD = "Inactividad Operativa - {}: Sin actividad registrada en los últimos 4 días hábiles."


@pytest.fixture
def miercoles(reloj):
    """Fija el reloj antes de armar el entorno: el calendario se arma relativo a este día."""
    reloj.fijar(a_las(MIERCOLES, 9))
    return reloj


def asignado_desde(db, entorno, dia: date) -> None:
    vigente = entorno.periodos["vigente"].id_periodo_academico
    for fila in db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_periodo_academico == vigente)).all():
        fila.creado_en = a_las(dia, 8)
        db.add(fila)
    db.commit()


def alertas(client, cabeceras) -> list[dict]:
    return client.get("/inicio/supervisor", headers=cabeceras["Supervisor"]).json()["alertas"]


def terminar_periodo_vigente(db, entorno) -> None:
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = entorno.hoy - timedelta(days=1)
    db.add(vigente)
    db.commit()


# ── CU010: Inicio del Docente ───────────────────────────────────────────────────────

def test_cu010_inicio_del_docente(client, entorno, cabeceras):
    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert (cuerpo["puede_iniciar_actividad"], cuerpo["motivo_no_puede_iniciar"]) == (True, None)
    assert cuerpo["actividad_activa"] is None
    assert cuerpo["totales"] == {"ciclos": ["III"], "subprogramas": ["Alfabetización", "Comprensión Lectora"], "cantidad_alumnos": 2}
    assert [a["colegio"] for a in cuerpo["asignaciones"]] == ["Colegio Andino"]
    assert cuerpo["registros_offline_pendientes"] is None


def test_cu010_con_actividad_en_curso(client, entorno, cabeceras, actividad):
    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert cuerpo["actividad_activa"]["id"] == actividad["id"]
    assert (cuerpo["puede_iniciar_actividad"], cuerpo["motivo_no_puede_iniciar"]) == (False, "actividad_activa")


def test_cu010_entre_periodos_no_puede_iniciar(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert (cuerpo["asignaciones"], cuerpo["puede_iniciar_actividad"]) == ([], False)
    assert cuerpo["motivo_no_puede_iniciar"] == "fuera_de_periodo"
    assert client.get("/me/asignaciones", headers=cabeceras["Docente"]).json() == []


def test_cu010_sin_asignaciones_en_el_periodo_vigente(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    cuerpo = client.get("/inicio/docente", headers=cabecera_de(db, otro)).json()

    assert (cuerpo["puede_iniciar_actividad"], cuerpo["motivo_no_puede_iniciar"]) == (False, "sin_asignaciones")


def test_cu010_solo_con_colegio_inactivo_es_sin_asignaciones(client, db, entorno, cabeceras):
    colegio = entorno.colegios["Andino"]
    colegio.activo = False
    db.add(colegio)
    db.commit()

    cuerpo = client.get("/inicio/docente", headers=cabeceras["Docente"]).json()

    assert cuerpo["motivo_no_puede_iniciar"] == "sin_asignaciones"
    assert motivo(client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabeceras["Docente"])) == (
        "sin_asignaciones"
    )


@pytest.mark.parametrize("ruta, rol", [("/inicio/docente", "Supervisor"), ("/inicio/supervisor", "Directivo"), ("/inicio/directivo", "Docente")])
def test_cu010_cada_inicio_es_solo_de_su_rol(client, entorno, cabeceras, ruta, rol):
    assert client.get(ruta, headers=cabeceras[rol]).status_code == 403


# ── CU011: Inicio del Supervisor ────────────────────────────────────────────────────

def test_cu011_indicadores_del_supervisor(client, entorno, cabeceras, actividad):
    cuerpo = client.get("/inicio/supervisor", headers=cabeceras["Supervisor"]).json()

    assert (cuerpo["colegios_registrados"], cuerpo["docentes_activos"], cuerpo["docentes_con_actividad_activa"]) == (2, 1, 1)
    assert (cuerpo["registros_vencidos"], cuerpo["registros_incompletos"]) == (0, 0)


def supervisor(client, cabeceras) -> dict:
    return client.get("/inicio/supervisor", headers=cabeceras["Supervisor"]).json()


def test_cu011_docentes_activos_solo_con_asignacion_vigente_en_colegio_activo(client, db, entorno, cabeceras):
    """No cuentan: sin asignación, asignado solo en un periodo pasado, asignado solo en un
    colegio inactivo ni una cuenta desactivada. Un docente con dos grados cuenta una vez."""
    lejano = entorno.colegios["Lejano"]
    crear_docente(db, entorno, "Sin", "sin@sicedu.test", "70000001")
    pasado = crear_docente(db, entorno, "Pasado", "pasado@sicedu.test", "70000002")
    asignar(db, pasado.id_docente, lejano, [entorno.grados["3.º"]], entorno.periodos["pasado"], 1)
    inactivo = crear_docente(db, entorno, "Inactivo", "inactivo@sicedu.test", "70000003")
    asignar(db, inactivo.id_docente, lejano, [entorno.grados["4.º"]], entorno.periodos["vigente"], 1)
    inactivo.activo = False
    db.commit()
    assert supervisor(client, cabeceras)["docentes_activos"] == 1

    lejano.activo = False
    db.add(lejano)
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000004")
    asignar(db, otro.id_docente, lejano, [entorno.grados["5.º"]], entorno.periodos["vigente"], 1)
    db.commit()

    cuerpo = supervisor(client, cabeceras)
    assert (cuerpo["docentes_activos"], cuerpo["colegios_registrados"]) == (1, 1)


def test_cu011_en_un_receso_no_hay_docentes_activos(client, db, entorno, cabeceras, reloj):
    """Entre el periodo pasado y el vigente no hay periodo vigente: 0, aunque el docente
    tenga asignaciones en ambos (el Periodo de Receso del CU no se implementa)."""
    reloj.fijar(a_las(entorno.periodos["pasado"].fecha_fin + timedelta(days=10)))
    cabecera = cabecera_de(db, entorno.supervisor)

    assert client.get("/inicio/supervisor", headers=cabecera).json()["docentes_activos"] == 0


def test_cu011_solo_cuenta_asignaciones_del_periodo_vigente(client, db, entorno, cabeceras):
    otro = crear_docente(db, entorno, "Futuro", "futuro@sicedu.test", "70000005")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["futuro"], 1)
    db.commit()

    assert supervisor(client, cabeceras)["docentes_activos"] == 1


def test_cu011_sin_calendario_no_hay_docentes_activos(client, entorno, cabeceras, monkeypatch):
    monkeypatch.setattr(inicio_service, "periodo_vigente", lambda _db: None)

    cuerpo = supervisor(client, cabeceras)
    assert (cuerpo["docentes_activos"], cuerpo["alertas"]) == (0, [])


def test_cu011_docente_recien_asignado_no_alerta(miercoles, client, entorno, cabeceras):
    assert alertas(client, cabeceras) == []


@pytest.mark.parametrize(
    "asignado, dias",
    [(date(2026, 6, 10), 4), (date(2026, 6, 11), 3), (date(2026, 5, 1), 22)],
    ids=["cuatro_dias_alerta", "tres_dias_no_alerta", "desde_el_inicio_del_periodo"],
)
def test_cu011_alerta_con_4_dias_habiles_sin_actividad(miercoles, client, db, entorno, cabeceras, asignado, dias):
    asignado_desde(db, entorno, asignado)

    resultado = alertas(client, cabeceras)

    if dias < 4:
        assert resultado == []
    else:
        assert resultado == [{"tipo": "inactividad_operativa", "mensaje": INACTIVIDAD.format("Rosa Quispe"),
                              "critica": False}]


def test_cu011_los_feriados_no_cuentan(miercoles, client, db, entorno, cabeceras):
    asignado_desde(db, entorno, date(2026, 6, 10))
    db.add(DiaNoLaborable(fecha=date(2026, 6, 15), motivo="Feriado", creado_por=1, creado_en=ahora_utc()))
    db.commit()

    assert alertas(client, cabeceras) == []


def test_cu011_la_ultima_actividad_finalizada_reinicia_la_cuenta(miercoles, client, db, entorno, cabeceras):
    """Cuenta desde el día siguiente a su fin: finalizada el viernes 12, el lunes 15 y
    el martes 16 son 2 días hábiles."""
    asignado_desde(db, entorno, date(2026, 6, 1))
    miercoles.fijar(a_las(date(2026, 6, 12), 9))
    cabecera = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera)
    miercoles.fijar(a_las(date(2026, 6, 12), 11))
    assert client.post(f"/actividades/{actividad['id']}/finalizar", json={}, headers=cabecera).status_code == 200
    miercoles.fijar(a_las(MIERCOLES, 9))

    assert alertas(client, cabeceras) == []


def test_cu011_iniciar_sin_finalizar_no_reinicia_la_cuenta(miercoles, client, db, entorno, cabeceras):
    """Una actividad en curso no es una actividad finalizada: sigue contando desde la
    asignación."""
    asignado_desde(db, entorno, date(2026, 6, 1))
    db.commit()
    iniciar_actividad(client, cabeceras["Docente"])

    assert [a["tipo"] for a in alertas(client, cabeceras)] == ["inactividad_operativa"]


def test_cu011_varias_alertas_ordenadas_por_nombre(miercoles, client, db, entorno, cabeceras):
    asignado_desde(db, entorno, date(2026, 6, 1))
    otro = crear_docente(db, entorno, "Beto", "beto@sicedu.test", "70000002")
    db.add(DocenteColegioGrado(
        id_docente=otro.id_docente, id_colegio=entorno.colegios["Lejano"].id_colegio, id_grado=entorno.grados["3.º"],
        id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico, creado_por=1,
        creado_en=a_las(date(2026, 6, 9), 8),
    ))
    db.commit()

    resultado = alertas(client, cabeceras)

    assert [a["mensaje"] for a in resultado] == [INACTIVIDAD.format("Beto Prueba"), INACTIVIDAD.format("Rosa Quispe")]


def test_cu011_entre_periodos_no_hay_alertas(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    assert alertas(client, cabeceras) == []


# ── CU012: Inicio del Directivo ─────────────────────────────────────────────────────

def test_cu012_indicadores_del_directivo(client, entorno, cabeceras):
    cuerpo = client.get("/inicio/directivo", headers=cabeceras["Directivo"]).json()

    assert cuerpo == {"beneficiarios_activos": 3, "colegios_operando": 1, "salud_sistema": None}


def directivo(client, cabeceras) -> dict:
    return client.get("/inicio/directivo", headers=cabeceras["Directivo"]).json()


def test_cu012_beneficiarios_solo_de_colegios_activos(client, db, entorno, cabeceras):
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()

    assert directivo(client, cabeceras)["beneficiarios_activos"] == 2


def test_cu012_en_un_receso_entre_periodos_usa_el_proximo(client, entorno, cabeceras, reloj):
    """Entre el periodo pasado y el vigente: cuenta los colegios del que viene."""
    reloj.fijar(a_las(entorno.periodos["pasado"].fecha_fin + timedelta(days=10)))

    assert directivo(client, cabeceras)["colegios_operando"] == 1


def test_cu012_con_el_anio_terminado_usa_el_ultimo_periodo(client, db, entorno, reloj):
    lejano = entorno.colegios["Lejano"]
    asignar(db, entorno.id_docente, lejano, [entorno.grados["3.º"]], entorno.periodos["futuro"], 1)
    db.commit()
    reloj.fijar(a_las(entorno.anio.fecha_fin + timedelta(days=10)))
    # La sesión se abre después de mover el reloj: una de antes ya habría vencido.
    cabecera = cabecera_de(db, entorno.usuarios["Directivo"])

    assert client.get("/inicio/directivo", headers=cabecera).json()["colegios_operando"] == 1
    assert periodo_de_referencia_del_anio(db).id_periodo_academico == entorno.periodos["futuro"].id_periodo_academico


def test_cu012_entre_periodos_sin_asignaciones_en_el_proximo_no_opera_ninguno(client, db, entorno, cabeceras):
    terminar_periodo_vigente(db, entorno)

    assert directivo(client, cabeceras)["colegios_operando"] == 0


def test_cu012_sin_calendario_no_opera_ningun_colegio(client, entorno, cabeceras, monkeypatch):
    monkeypatch.setattr(inicio_service, "periodo_de_referencia_del_anio", lambda _db: None)

    assert directivo(client, cabeceras)["colegios_operando"] == 0


def test_cu011_sin_docentes_asignados_no_hay_alertas(client, db, entorno, cabeceras):
    for colegio in entorno.colegios.values():
        colegio.activo = False
        db.add(colegio)
    db.commit()

    cuerpo = supervisor(client, cabeceras)
    assert (cuerpo["docentes_activos"], cuerpo["alertas"]) == (0, [])


# ── CU020 y CU021: seguimiento ──────────────────────────────────────────────────────

def test_cu020_tabla_de_seguimiento(client, entorno, cabeceras):
    cuerpo = client.get("/seguimiento/docentes", headers=cabeceras["Supervisor"]).json()

    fila = cuerpo["items"][0]
    # Sin información de la cola offline (llega en T5): ni "al día" ni 0 pendientes.
    assert (fila["id_docente"], fila["sincronizacion"], fila["registros_pendientes"]) == (
        entorno.id_docente, "sin_informacion", None
    )
    assert fila["colegios"] == [{"id": entorno.colegios["Andino"].id_colegio, "nombre": "Colegio Andino"}]
    assert fila["ultima_conexion"] is not None


def test_cu020_filtros(client, db, entorno, cabeceras):
    crear_docente(db, entorno, "Nunca", "nunca@sicedu.test", "70000003")
    db.commit()
    sup = cabeceras["Supervisor"]
    hoy = entorno.hoy.isoformat()

    def nombres(**params):
        return [d["nombres"] for d in client.get("/seguimiento/docentes", params=params, headers=sup).json()["items"]]

    assert nombres() == ["Nunca", "Rosa"]
    assert nombres(desde=hoy, hasta=hoy) == ["Rosa"]
    assert nombres(id_colegio=entorno.colegios["Andino"].id_colegio) == ["Rosa"]
    assert nombres(sincronizacion="pendiente") == []
    assert nombres(sincronizacion="sin_informacion") == ["Nunca", "Rosa"]
    assert nombres(activo="false") == []


def test_cu020_ultima_conexion_null_si_nunca_inicio_sesion(client, db, entorno, cabeceras):
    crear_docente(db, entorno, "Nunca", "nunca@sicedu.test", "70000003")
    db.commit()

    items = client.get("/seguimiento/docentes", headers=cabeceras["Supervisor"]).json()["items"]

    assert {d["nombres"]: d["ultima_conexion"] is None for d in items} == {"Nunca": True, "Rosa": False}


def test_cu020_por_defecto_solo_docentes_activos(client, db, entorno, cabeceras):
    inactivo = crear_docente(db, entorno, "Inactiva", "inactiva@sicedu.test", "70000004")
    inactivo.activo = False
    db.commit()
    sup = cabeceras["Supervisor"]

    def nombres(**params):
        return [d["nombres"] for d in client.get("/seguimiento/docentes", params=params, headers=sup).json()["items"]]

    assert nombres() == ["Rosa"]
    assert nombres(activo="false") == ["Inactiva"]


def test_cu020_ordena_por_estado_de_sincronizacion_y_nombre(client, db, entorno, cabeceras, monkeypatch):
    """error → pendiente → sin información → al día, y por nombre dentro de cada estado,
    en la consulta: la segunda página sigue el mismo orden."""
    from sqlalchemy import case, cast, Integer, null

    from app.core import paginacion
    from app.models.organizacion import Usuario
    from app.services import seguimiento_service

    estados = {"Ana": "al_dia", "Beto": "error", "Carla": "pendiente", "Dora": "sin_informacion",
               "Eva": "error", "Rosa": "pendiente"}
    for i, nombre in enumerate(n for n in estados if n != "Rosa"):
        crear_docente(db, entorno, nombre, f"{nombre.lower()}@sicedu.test", f"7100000{i}")
    db.commit()
    pendientes = {n: 3 for n, e in estados.items() if e == "pendiente"} | {
        n: 0 for n, e in estados.items() if e in ("al_dia", "error")
    }
    errores = {n: 1 for n, e in estados.items() if e == "error"}
    monkeypatch.setattr(seguimiento_service, "pendientes_docente_sql", lambda: case(
        pendientes, value=Usuario.nombres, else_=cast(null(), Integer)))
    monkeypatch.setattr(seguimiento_service, "errores_docente_sql", lambda: case(errores, value=Usuario.nombres, else_=0))
    monkeypatch.setattr(paginacion, "TAMANO_PAGINA", 4)

    sup = cabeceras["Supervisor"]
    paginas = [client.get("/seguimiento/docentes", params={"page": p}, headers=sup).json() for p in (1, 2)]
    filas = [d for pagina in paginas for d in pagina["items"]]

    assert [(d["nombres"], d["sincronizacion"]) for d in filas] == [
        ("Beto", "error"), ("Eva", "error"), ("Carla", "pendiente"), ("Rosa", "pendiente"),
        ("Dora", "sin_informacion"), ("Ana", "al_dia"),
    ]
    assert {d["nombres"]: d["registros_pendientes"] for d in filas}["Dora"] is None
    assert {d["nombres"]: d["registros_pendientes"] for d in filas}["Ana"] == 0


def test_cu020_rango_invertido_y_entre_periodos(client, db, entorno, cabeceras):
    sup = cabeceras["Supervisor"]
    invertido = client.get("/seguimiento/docentes", params={"desde": "2026-05-02", "hasta": "2026-05-01"}, headers=sup)
    terminar_periodo_vigente(db, entorno)
    por_colegio = client.get(
        "/seguimiento/docentes", params={"id_colegio": entorno.colegios["Andino"].id_colegio}, headers=sup
    ).json()

    assert motivo(invertido) == "rango_fechas_invalido"
    assert por_colegio["total"] == 0


def test_cu021_cabecera_e_historial_del_docente(client, entorno, cabeceras, actividad):
    sup = cabeceras["Supervisor"]
    url = f"/seguimiento/docentes/{entorno.id_docente}"

    cabecera = client.get(url, headers=sup).json()
    historial = client.get(f"{url}/actividades", headers=sup).json()

    assert cabecera["nombres"] == "Rosa"
    assert [a["id"] for a in historial["items"]] == [actividad["id"]]
    assert historial["items"][0]["productividad_texto"] == "Sin registros"


def test_cu021_docente_inexistente(client, entorno, cabeceras):
    sup = cabeceras["Supervisor"]

    for url in ("/seguimiento/docentes/999", "/seguimiento/docentes/999/actividades"):
        respuesta = client.get(url, headers=sup)
        assert (respuesta.status_code, motivo(respuesta)) == (404, "docente_no_encontrado")


@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_cu020_solo_el_supervisor(client, entorno, cabeceras, rol):
    assert client.get("/seguimiento/docentes", headers=cabeceras[rol]).status_code == 403


def test_periodo_vigente_unico(db, entorno):
    vigentes = db.exec(
        select(PeriodoAcademico).where(PeriodoAcademico.fecha_inicio <= entorno.hoy, PeriodoAcademico.fecha_fin >= entorno.hoy)
    ).all()

    assert len(vigentes) == 1
