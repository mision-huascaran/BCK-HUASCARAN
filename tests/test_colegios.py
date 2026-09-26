"""Pruebas de /colegios.

Los administra el Supervisor. El listado es el único endpoint que cambia según quién
pregunta: al Docente solo le devuelve los colegios donde tiene asignación vigente.
"""
from app.models.organizacion import Colegio


def test_supervisor_crea_colegio(client, entorno, sesiones):
    respuesta = client.post(
        "/colegios",
        json={"nombre": "Colegio Nuevo", "zona": "Sierra"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    creado = respuesta.json()
    assert creado["nombre"] == "Colegio Nuevo"
    assert creado["zona"] == "Sierra"
    assert creado["creado_por"] == entorno["usuarios"]["Supervisor"].id_usuario
    assert creado["modificado_por"] is None


def test_colegio_sin_zona(client, entorno, sesiones):
    respuesta = client.post(
        "/colegios", json={"nombre": "Colegio Sin Zona"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["zona"] is None


def test_supervisor_actualiza_colegio(client, session, entorno, sesiones):
    id_colegio = entorno["colegios"][0].id_colegio

    respuesta = client.patch(
        f"/colegios/{id_colegio}",
        json={"nombre": "Colegio Andino Renombrado"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    actualizado = respuesta.json()
    assert actualizado["nombre"] == "Colegio Andino Renombrado"
    # La zona no iba en el body: se conserva en vez de quedar en null.
    assert actualizado["zona"] == "Rural"
    assert actualizado["modificado_por"] == entorno["usuarios"]["Supervisor"].id_usuario
    assert session.get(Colegio, id_colegio).nombre == "Colegio Andino Renombrado"


def test_actualizar_colegio_inexistente(client, entorno, sesiones):
    respuesta = client.patch(
        "/colegios/9999", json={"nombre": "Fantasma"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 404
    assert "9999" in respuesta.json()["detail"]


def test_supervisor_y_directivo_ven_todos_los_colegios(client, entorno, sesiones):
    for rol in ("Supervisor", "Directivo"):
        respuesta = client.get("/colegios", headers=sesiones[rol])

        assert respuesta.status_code == 200, f"{rol}: {respuesta.text}"
        assert len(respuesta.json()) == 2


def test_docente_solo_ve_los_colegios_que_tiene_asignados(client, entorno, sesiones):
    respuesta = client.get("/colegios", headers=sesiones["Docente"])

    assert respuesta.status_code == 200, respuesta.text
    devueltos = respuesta.json()
    assert len(devueltos) == 1
    assert devueltos[0]["id_colegio"] == entorno["colegios"][0].id_colegio


def test_docente_sin_asignaciones_no_ve_ningun_colegio(client, session, entorno, sesiones):
    session.delete(entorno["asignacion"])
    session.commit()

    respuesta = client.get("/colegios", headers=sesiones["Docente"])

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_asignacion_en_periodo_cerrado_no_da_alcance(client, session, entorno, sesiones):
    """Una asignación de un periodo vencido no cuenta: el alcance es solo el vigente."""
    entorno["asignacion"].id_periodo_academico = entorno["periodos"][1].id_periodo_academico
    session.add(entorno["asignacion"])
    session.commit()

    respuesta = client.get("/colegios", headers=sesiones["Docente"])

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_solo_el_supervisor_administra_colegios(client, entorno, sesiones):
    for rol in ("Docente", "Directivo"):
        creacion = client.post(
            "/colegios", json={"nombre": "Prohibido"}, headers=sesiones[rol]
        )
        edicion = client.patch(
            f"/colegios/{entorno['colegios'][0].id_colegio}",
            json={"nombre": "Prohibido"},
            headers=sesiones[rol],
        )

        assert creacion.status_code == 403, f"{rol} pudo crear"
        assert edicion.status_code == 403, f"{rol} pudo editar"
        assert creacion.json()["detail"] == "No tienes permisos para realizar esta acción"


def test_colegios_sin_sesion(client, entorno):
    assert client.get("/colegios").status_code == 401
    assert client.post("/colegios", json={"nombre": "X"}).status_code == 401
