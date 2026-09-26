"""Pruebas de /asignaciones: qué colegio y grado lleva cada docente en cada periodo.

La asignación es lo que define el alcance del docente en el resto del sistema, así que
aquí importa sobre todo quién puede verla y quién puede tocarla. El Directivo queda
fuera: la gestión de docentes no está en su parcela, igual que GET /profesores.
"""
from app.models.organizacion import DocenteColegioGrado


def asignacion_nueva(entorno, **cambios):
    cuerpo = {
        "id_docente": entorno["ficha_docente"].id_docente,
        "id_colegio": entorno["colegios"][1].id_colegio,
        "id_grado": entorno["grados"][1].id_grado,
        "id_periodo_academico": entorno["periodos"][0].id_periodo_academico,
    }
    cuerpo.update(cambios)
    return cuerpo


# ── Listado ──────────────────────────────────────────────────────────────────────────

def test_el_supervisor_ve_las_asignaciones_con_los_nombres_resueltos(
    client, entorno, sesiones
):
    respuesta = client.get("/asignaciones", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    devueltas = respuesta.json()
    assert len(devueltas) == 1
    fila = devueltas[0]
    # El listado trae los nombres ya cruzados, para pintarlo sin pedir los catálogos.
    assert fila["docente"] == "Rosa Quispe"
    assert fila["colegio"] == "Colegio Andino"
    assert fila["grado"] == "1° primaria"
    assert fila["periodo"] == "Periodo 1"
    assert fila["vigente"] is True


def test_el_docente_solo_ve_las_suyas(client, session, entorno, sesiones):
    """El recorte va por el id_docente del token, no por un parámetro de la URL."""
    otra_ficha = entorno["ficha_docente"]
    ajena = DocenteColegioGrado(
        id_docente=otra_ficha.id_docente + 50,
        id_colegio=entorno["colegios"][1].id_colegio,
        id_grado=entorno["grados"][1].id_grado,
        id_periodo_academico=entorno["periodos"][0].id_periodo_academico,
        creado_por=entorno["usuarios"]["Supervisor"].id_usuario,
        creado_en=entorno["alumnos"][0].fecha_registro,
    )
    session.add(ajena)
    session.commit()

    respuesta = client.get("/asignaciones", headers=sesiones["Docente"])

    assert respuesta.status_code == 200, respuesta.text
    devueltas = respuesta.json()
    assert len(devueltas) == 1
    assert devueltas[0]["id_docente"] == otra_ficha.id_docente


def test_una_asignacion_de_un_periodo_cerrado_sale_como_no_vigente(
    client, session, entorno, sesiones
):
    entorno["asignacion"].id_periodo_academico = entorno["periodos"][1].id_periodo_academico
    session.add(entorno["asignacion"])
    session.commit()

    respuesta = client.get("/asignaciones", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200
    assert respuesta.json()[0]["vigente"] is False


def test_el_directivo_no_entra_a_asignaciones(client, entorno, sesiones):
    respuesta = client.get("/asignaciones", headers=sesiones["Directivo"])

    assert respuesta.status_code == 403


# ── Alta ─────────────────────────────────────────────────────────────────────────────

def test_el_supervisor_crea_una_asignacion(client, entorno, sesiones):
    respuesta = client.post(
        "/asignaciones", json=asignacion_nueva(entorno), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 201, respuesta.text
    creada = respuesta.json()
    assert creada["id_colegio"] == entorno["colegios"][1].id_colegio
    assert creada["creado_por"] == entorno["usuarios"]["Supervisor"].id_usuario


def test_la_asignacion_nueva_amplia_el_alcance_del_docente(client, entorno, sesiones):
    """Asignar un colegio se nota de inmediato en lo que el docente puede ver."""
    antes = client.get("/colegios", headers=sesiones["Docente"])
    client.post(
        "/asignaciones", json=asignacion_nueva(entorno), headers=sesiones["Supervisor"]
    )
    despues = client.get("/colegios", headers=sesiones["Docente"])

    assert len(antes.json()) == 1
    assert len(despues.json()) == 2


def test_no_se_repite_la_misma_asignacion(client, entorno, sesiones):
    repetida = asignacion_nueva(
        entorno,
        id_colegio=entorno["colegios"][0].id_colegio,
        id_grado=entorno["grados"][0].id_grado,
    )

    respuesta = client.post("/asignaciones", json=repetida, headers=sesiones["Supervisor"])

    assert respuesta.status_code == 409
    assert "ya tiene asignado" in respuesta.json()["detail"]


def test_alta_con_referencias_que_no_existen(client, entorno, sesiones):
    casos = {
        "id_docente": "docente",
        "id_colegio": "colegio",
        "id_grado": "grado",
        "id_periodo_academico": "periodo académico",
    }
    for campo, texto in casos.items():
        respuesta = client.post(
            "/asignaciones",
            json=asignacion_nueva(entorno, **{campo: 9999}),
            headers=sesiones["Supervisor"],
        )

        assert respuesta.status_code == 404, f"{campo}: {respuesta.text}"
        assert texto in respuesta.json()["detail"]


def test_alta_sin_campos_obligatorios(client, entorno, sesiones):
    respuesta = client.post("/asignaciones", json={}, headers=sesiones["Supervisor"])
    assert respuesta.status_code == 422


# ── Baja ─────────────────────────────────────────────────────────────────────────────

def test_el_supervisor_elimina_una_asignacion(client, session, entorno, sesiones):
    """Aquí sí se borra de verdad: es una relación del periodo, no un histórico."""
    id_asignacion = entorno["asignacion"].id

    respuesta = client.delete(f"/asignaciones/{id_asignacion}", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 204
    assert respuesta.content == b""
    assert session.get(DocenteColegioGrado, id_asignacion) is None


def test_eliminar_una_asignacion_que_no_existe(client, entorno, sesiones):
    respuesta = client.delete("/asignaciones/9999", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 404
    assert "9999" in respuesta.json()["detail"]


# ── Permisos ─────────────────────────────────────────────────────────────────────────

def test_el_docente_ve_pero_no_administra(client, entorno, sesiones):
    cabecera = sesiones["Docente"]

    assert client.get("/asignaciones", headers=cabecera).status_code == 200
    assert client.post(
        "/asignaciones", json=asignacion_nueva(entorno), headers=cabecera
    ).status_code == 403
    assert client.delete(
        f"/asignaciones/{entorno['asignacion'].id}", headers=cabecera
    ).status_code == 403


def test_asignaciones_sin_sesion(client, entorno):
    assert client.get("/asignaciones").status_code == 401
    assert client.post("/asignaciones", json={}).status_code == 401
