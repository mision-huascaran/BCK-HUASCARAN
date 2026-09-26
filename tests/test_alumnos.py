"""Pruebas de /alumnos.

Es el endpoint con más reglas de visibilidad del sistema:

- Docente: solo los alumnos de los colegios y grados que tiene asignados y vigentes,
  tanto para leer como para crear y editar.
- Directivo: todos, pero sin nombres ni apellidos, y sin poder buscar por nombre.
- Supervisor: todos y completos, pero en solo lectura.
"""
from sqlmodel import select

from app.models.organizacion import Alumno


def alumno_nuevo(entorno, **cambios):
    """Cuerpo válido para POST /alumnos, dentro del alcance del docente."""
    cuerpo = {
        "nombres": "Nuevo",
        "apellidos": "Alumno",
        "id_colegio": entorno["colegios"][0].id_colegio,
        "id_grado": entorno["grados"][0].id_grado,
        "id_programa_actual": entorno["programas"][0].id_programa,
    }
    cuerpo.update(cambios)
    return cuerpo


# ── Alta ─────────────────────────────────────────────────────────────────────────────

def test_docente_crea_alumno_en_su_alcance(client, entorno, sesiones):
    respuesta = client.post(
        "/alumnos", json=alumno_nuevo(entorno), headers=sesiones["Docente"]
    )

    assert respuesta.status_code == 201, respuesta.text
    creado = respuesta.json()
    assert creado["nombres"] == "Nuevo"
    assert creado["activo"] is True
    assert creado["creado_por"] == entorno["usuarios"]["Docente"].id_usuario
    assert creado["fecha_registro"] is not None


def test_docente_no_crea_alumno_fuera_de_su_alcance(client, session, entorno, sesiones):
    cuerpo = alumno_nuevo(
        entorno,
        id_colegio=entorno["colegios"][1].id_colegio,
        id_grado=entorno["grados"][1].id_grado,
    )

    respuesta = client.post("/alumnos", json=cuerpo, headers=sesiones["Docente"])

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == (
        "Ese alumno no pertenece a un colegio y grado que tengas asignado"
    )
    # El rechazo es antes de escribir: el alumno no quedó a medias en la base.
    assert session.exec(select(Alumno).where(Alumno.nombres == "Nuevo")).first() is None


def test_alta_de_alumno_es_solo_del_docente(client, entorno, sesiones):
    for rol in ("Supervisor", "Directivo"):
        respuesta = client.post(
            "/alumnos", json=alumno_nuevo(entorno), headers=sesiones[rol]
        )
        assert respuesta.status_code == 403, f"{rol} pudo crear un alumno"


def test_alta_sin_campos_obligatorios(client, entorno, sesiones):
    respuesta = client.post(
        "/alumnos", json={"nombres": "Incompleto"}, headers=sesiones["Docente"]
    )
    assert respuesta.status_code == 422


# ── Listado ──────────────────────────────────────────────────────────────────────────

def test_docente_solo_ve_los_alumnos_de_su_alcance(client, entorno, sesiones):
    respuesta = client.get("/alumnos", headers=sesiones["Docente"])

    assert respuesta.status_code == 200, respuesta.text
    pagina = respuesta.json()
    # El alumno del otro colegio no aparece, y tampoco cuenta para el total.
    assert pagina["total"] == 1
    assert [a["apellidos"] for a in pagina["items"]] == ["Pérez Quispe"]


def test_supervisor_ve_todos_los_alumnos_completos(client, entorno, sesiones):
    respuesta = client.get("/alumnos", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert pagina["total"] == 2
    assert all(a["nombres"] is not None for a in pagina["items"])


def test_directivo_ve_los_alumnos_sin_nombres(client, entorno, sesiones):
    respuesta = client.get("/alumnos", headers=sesiones["Directivo"])

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    assert pagina["total"] == 2
    for alumno in pagina["items"]:
        assert alumno["nombres"] is None
        assert alumno["apellidos"] is None
        # Lo que sí ve: el dato agregado por colegio, grado y programa.
        assert alumno["id_colegio"] is not None


def test_directivo_no_puede_buscar_por_nombre(client, entorno, sesiones):
    respuesta = client.get("/alumnos", params={"q": "Perez"}, headers=sesiones["Directivo"])

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "La vista de Directivo no permite buscar por nombre"


def test_docente_sin_asignaciones_no_ve_alumnos(client, session, entorno, sesiones):
    session.delete(entorno["asignacion"])
    session.commit()

    respuesta = client.get("/alumnos", headers=sesiones["Docente"])

    assert respuesta.status_code == 200
    assert respuesta.json() == {"total": 0, "limit": 50, "offset": 0, "items": []}


def test_filtros_del_listado(client, entorno, sesiones):
    cabecera = sesiones["Supervisor"]
    colegio = entorno["colegios"][1].id_colegio
    grado = entorno["grados"][1].id_grado
    programa = entorno["programas"][1].id_programa

    por_colegio = client.get("/alumnos", params={"colegio": colegio}, headers=cabecera)
    por_grado = client.get("/alumnos", params={"grado": grado}, headers=cabecera)
    por_programa = client.get("/alumnos", params={"programa": programa}, headers=cabecera)
    inactivos = client.get("/alumnos", params={"activo": False}, headers=cabecera)
    activos = client.get("/alumnos", params={"activo": True}, headers=cabecera)

    for respuesta in (por_colegio, por_grado, por_programa, inactivos):
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["total"] == 1
        assert respuesta.json()["items"][0]["apellidos"] == "Lejano"
    assert activos.json()["items"][0]["apellidos"] == "Pérez Quispe"


def test_busqueda_ignora_tildes_y_mayusculas(client, entorno, sesiones):
    respuesta = client.get("/alumnos", params={"q": "PEREZ"}, headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["total"] == 1
    assert respuesta.json()["items"][0]["apellidos"] == "Pérez Quispe"


def test_busqueda_por_varias_palabras_en_cualquier_orden(client, entorno, sesiones):
    respuesta = client.get(
        "/alumnos", params={"q": "quispe luis"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["total"] == 1


def test_busqueda_sin_coincidencias(client, entorno, sesiones):
    respuesta = client.get("/alumnos", params={"q": "nadie"}, headers=sesiones["Supervisor"])

    assert respuesta.json()["total"] == 0
    assert respuesta.json()["items"] == []


def test_paginacion(client, entorno, sesiones):
    respuesta = client.get(
        "/alumnos", params={"limit": 1, "offset": 1}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200
    pagina = respuesta.json()
    # `total` es el total del filtro, no el de la página: 2 aunque solo vuelva 1.
    assert pagina["total"] == 2
    assert pagina["limit"] == 1
    assert pagina["offset"] == 1
    assert len(pagina["items"]) == 1


def test_limites_de_paginacion_invalidos(client, entorno, sesiones):
    cabecera = sesiones["Supervisor"]
    assert client.get("/alumnos", params={"limit": 0}, headers=cabecera).status_code == 422
    assert client.get("/alumnos", params={"limit": 501}, headers=cabecera).status_code == 422
    assert client.get("/alumnos", params={"offset": -1}, headers=cabecera).status_code == 422


def test_listado_sin_sesion(client, entorno):
    assert client.get("/alumnos").status_code == 401


# ── Edición ──────────────────────────────────────────────────────────────────────────

def test_docente_edita_un_alumno_suyo(client, session, entorno, sesiones):
    id_alumno = entorno["alumnos"][0].id_alumno

    respuesta = client.patch(
        f"/alumnos/{id_alumno}",
        json={"nombres": "Luis Alberto"},
        headers=sesiones["Docente"],
    )

    assert respuesta.status_code == 200, respuesta.text
    editado = respuesta.json()
    assert editado["nombres"] == "Luis Alberto"
    assert editado["apellidos"] == "Pérez Quispe"
    assert editado["modificado_por"] == entorno["usuarios"]["Docente"].id_usuario
    assert session.get(Alumno, id_alumno).nombres == "Luis Alberto"


def test_baja_logica_del_alumno(client, session, entorno, sesiones):
    """No hay DELETE: la baja es `activo: false` y la fila se conserva."""
    id_alumno = entorno["alumnos"][0].id_alumno

    respuesta = client.patch(
        f"/alumnos/{id_alumno}", json={"activo": False}, headers=sesiones["Docente"]
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["activo"] is False
    assert session.get(Alumno, id_alumno) is not None


def test_docente_no_edita_un_alumno_ajeno(client, entorno, sesiones):
    ajeno = entorno["alumnos"][1].id_alumno

    respuesta = client.patch(
        f"/alumnos/{ajeno}", json={"nombres": "Intento"}, headers=sesiones["Docente"]
    )

    assert respuesta.status_code == 403


def test_docente_no_saca_un_alumno_de_su_alcance(client, entorno, sesiones):
    """Editar uno propio es válido; moverlo a un colegio que no lleva, no."""
    propio = entorno["alumnos"][0].id_alumno

    respuesta = client.patch(
        f"/alumnos/{propio}",
        json={"id_colegio": entorno["colegios"][1].id_colegio},
        headers=sesiones["Docente"],
    )

    assert respuesta.status_code == 403


def test_editar_alumno_inexistente(client, entorno, sesiones):
    respuesta = client.patch(
        "/alumnos/9999", json={"nombres": "Fantasma"}, headers=sesiones["Docente"]
    )

    assert respuesta.status_code == 404
    assert "9999" in respuesta.json()["detail"]


def test_edicion_de_alumno_es_solo_del_docente(client, entorno, sesiones):
    id_alumno = entorno["alumnos"][0].id_alumno
    for rol in ("Supervisor", "Directivo"):
        respuesta = client.patch(
            f"/alumnos/{id_alumno}", json={"nombres": "X"}, headers=sesiones[rol]
        )
        assert respuesta.status_code == 403, f"{rol} pudo editar un alumno"
