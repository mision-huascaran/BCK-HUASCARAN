"""Pruebas de /usuarios: gestión de cuentas administrativas.

Entran el Supervisor y el Directivo, pero cada uno tiene su parcela y no se pisan:
el Supervisor lleva Docentes y Supervisores; el Directivo, solo otros Directivos.
Sin ese techo, un Supervisor podría crearse un Directivo con una petición directa
aunque la interfaz no le ofrezca el botón, así que casi todas estas pruebas son
peticiones que la interfaz nunca haría.
"""
from app.models.organizacion import Usuario
from tests.conftest import CORREOS, PASSWORD


def cuenta_nueva(entorno, rol="Supervisor", **cambios):
    cuerpo = {
        "nombres": "Nueva",
        "apellidos": "Cuenta",
        "correo": "nueva.cuenta@sicedu.test",
        "id_rol": entorno["roles"][rol].id_rol,
    }
    cuerpo.update(cambios)
    return cuerpo


# ── Alta ─────────────────────────────────────────────────────────────────────────────

def test_supervisor_crea_otro_supervisor(client, entorno, sesiones, correos):
    respuesta = client.post(
        "/usuarios", json=cuenta_nueva(entorno), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 201, respuesta.text
    creada = respuesta.json()
    assert creada["correo"] == "nueva.cuenta@sicedu.test"
    assert creada["id_docente"] is None
    assert creada["correo_enviado"] is True
    assert creada["contraseña_temporal"] is None
    assert len(correos) == 1


def test_directivo_crea_otro_directivo(client, entorno, sesiones):
    respuesta = client.post(
        "/usuarios",
        json=cuenta_nueva(entorno, rol="Directivo"),
        headers=sesiones["Directivo"],
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["id_rol"] == entorno["roles"]["Directivo"].id_rol


def test_si_el_correo_falla_la_clave_vuelve_en_la_respuesta(client, entorno, sesiones, correos):
    correos.fallar = True

    respuesta = client.post(
        "/usuarios", json=cuenta_nueva(entorno), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["correo_enviado"] is False
    assert respuesta.json()["contraseña_temporal"]


def test_la_cuenta_creada_puede_iniciar_sesion(client, entorno, sesiones, correos):
    correos.fallar = True
    creacion = client.post(
        "/usuarios", json=cuenta_nueva(entorno), headers=sesiones["Supervisor"]
    )

    respuesta = client.post(
        "/login",
        json={
            "correo": "nueva.cuenta@sicedu.test",
            "password": creacion.json()["contraseña_temporal"],
        },
    )

    assert respuesta.status_code == 200, respuesta.text


def test_supervisor_no_puede_crear_un_directivo(client, entorno, sesiones):
    """El caso que motiva la parcela: la interfaz no lo ofrece, la API tampoco."""
    respuesta = client.post(
        "/usuarios",
        json=cuenta_nueva(entorno, rol="Directivo"),
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "Un Supervisor no gestiona cuentas de Directivo"


def test_directivo_no_puede_crear_un_supervisor(client, entorno, sesiones):
    respuesta = client.post(
        "/usuarios", json=cuenta_nueva(entorno), headers=sesiones["Directivo"]
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "Un Directivo no gestiona cuentas de Supervisor"


def test_un_docente_se_crea_por_profesores_no_por_usuarios(client, entorno, sesiones):
    """Está dentro de la parcela del Supervisor, pero por aquí se quedaría sin ficha."""
    respuesta = client.post(
        "/usuarios",
        json=cuenta_nueva(entorno, rol="Docente"),
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 400
    assert "POST /profesores" in respuesta.json()["detail"]


def test_alta_con_correo_ya_registrado(client, entorno, sesiones):
    respuesta = client.post(
        "/usuarios",
        json=cuenta_nueva(entorno, correo=CORREOS["Supervisor"]),
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 409
    assert "ya está registrado" in respuesta.json()["detail"]


def test_alta_con_rol_inexistente(client, entorno, sesiones):
    respuesta = client.post(
        "/usuarios", json=cuenta_nueva(entorno, id_rol=9999), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 404
    assert "9999" in respuesta.json()["detail"]


# ── Listado ──────────────────────────────────────────────────────────────────────────

def test_el_supervisor_lista_docentes_y_supervisores(client, entorno, sesiones):
    respuesta = client.get("/usuarios", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    roles = sorted(cuenta["rol"] for cuenta in respuesta.json())
    assert roles == ["Docente", "Supervisor"]


def test_el_directivo_solo_lista_directivos(client, entorno, sesiones):
    respuesta = client.get("/usuarios", headers=sesiones["Directivo"])

    assert respuesta.status_code == 200
    assert [cuenta["rol"] for cuenta in respuesta.json()] == ["Directivo"]


def test_filtro_por_rol(client, entorno, sesiones):
    respuesta = client.get(
        "/usuarios", params={"rol": "Docente"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200
    devueltas = respuesta.json()
    assert len(devueltas) == 1
    assert devueltas[0]["correo"] == CORREOS["Docente"]
    assert devueltas[0]["id_docente"] is not None


def test_el_filtro_no_saca_a_nadie_de_su_parcela(client, entorno, sesiones):
    """Pedir ?rol=Docente siendo Directivo no devuelve docentes: el recorte va antes."""
    respuesta = client.get(
        "/usuarios", params={"rol": "Docente"}, headers=sesiones["Directivo"]
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == []


# ── Activar y desactivar ─────────────────────────────────────────────────────────────

def test_desactivar_y_activar_una_cuenta_de_docente(client, session, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    baja = client.patch(f"/usuarios/{id_usuario}/desactivar", headers=sesiones["Supervisor"])

    assert baja.status_code == 200, baja.text
    assert baja.json()["activo"] is False
    session.expire_all()
    assert session.get(Usuario, id_usuario).activo is False

    alta = client.patch(f"/usuarios/{id_usuario}/activar", headers=sesiones["Supervisor"])

    assert alta.status_code == 200
    assert alta.json()["activo"] is True


def test_nadie_puede_desactivar_su_propia_cuenta(client, entorno, sesiones):
    """Desactivarse cierra la sesión en la siguiente petición y deja al usuario fuera."""
    id_usuario = entorno["usuarios"]["Supervisor"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_usuario}/desactivar", headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 409
    assert "tu propia cuenta" in respuesta.json()["detail"]


def test_desactivar_fuera_de_la_parcela(client, entorno, sesiones):
    id_directivo = entorno["usuarios"]["Directivo"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_directivo}/desactivar", headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "Un Supervisor no gestiona cuentas de Directivo"


def test_activar_fuera_de_la_parcela(client, entorno, sesiones):
    id_supervisor = entorno["usuarios"]["Supervisor"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_supervisor}/activar", headers=sesiones["Directivo"]
    )

    assert respuesta.status_code == 403


def test_cambiar_estado_de_una_cuenta_que_no_existe(client, entorno, sesiones):
    for accion in ("desactivar", "activar"):
        respuesta = client.patch(f"/usuarios/9999/{accion}", headers=sesiones["Supervisor"])
        assert respuesta.status_code == 404
        assert "9999" in respuesta.json()["detail"]


def test_desactivar_una_cuenta_de_docente_apaga_tambien_su_ficha(
    client, session, entorno, sesiones
):
    from app.models.organizacion import Docente

    id_usuario = entorno["usuarios"]["Docente"].id_usuario
    id_docente = entorno["ficha_docente"].id_docente

    client.patch(f"/usuarios/{id_usuario}/desactivar", headers=sesiones["Supervisor"])

    session.expire_all()
    assert session.get(Docente, id_docente).activo is False


# ── Edición ──────────────────────────────────────────────────────────────────────────

def test_editar_una_cuenta_de_la_parcela(client, session, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_usuario}",
        json={"nombres": "Rosa Elena", "correo": "rosa.elena@sicedu.test"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    editada = respuesta.json()
    assert editada["nombres"] == "Rosa Elena"
    assert editada["correo"] == "rosa.elena@sicedu.test"

    from app.models.organizacion import Docente

    session.expire_all()
    # La ficha de docente lleva los nombres duplicados: se escriben en las dos tablas.
    assert session.get(Docente, entorno["ficha_docente"].id_docente).nombres == "Rosa Elena"


def test_editar_una_cuenta_sin_ficha_de_docente(client, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Supervisor"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_usuario}", json={"apellidos": "Supervisora Mayor"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["apellidos"] == "Supervisora Mayor"


def test_editar_fuera_de_la_parcela(client, entorno, sesiones):
    id_directivo = entorno["usuarios"]["Directivo"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_directivo}", json={"nombres": "X"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 403


def test_editar_al_correo_de_otra_cuenta(client, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    respuesta = client.patch(
        f"/usuarios/{id_usuario}",
        json={"correo": CORREOS["Supervisor"]},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 409


def test_editar_una_cuenta_que_no_existe(client, entorno, sesiones):
    respuesta = client.patch(
        "/usuarios/9999", json={"nombres": "Fantasma"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 404


# ── Permisos ─────────────────────────────────────────────────────────────────────────

def test_el_docente_no_entra_a_la_gestion_de_cuentas(client, entorno, sesiones):
    cabecera = sesiones["Docente"]
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    assert client.get("/usuarios", headers=cabecera).status_code == 403
    assert client.post(
        "/usuarios", json=cuenta_nueva(entorno), headers=cabecera
    ).status_code == 403
    assert client.patch(
        f"/usuarios/{id_usuario}", json={"nombres": "X"}, headers=cabecera
    ).status_code == 403
    assert client.patch(
        f"/usuarios/{id_usuario}/desactivar", headers=cabecera
    ).status_code == 403
    assert client.patch(f"/usuarios/{id_usuario}/activar", headers=cabecera).status_code == 403


def test_usuarios_sin_sesion(client, entorno):
    assert client.get("/usuarios").status_code == 401
