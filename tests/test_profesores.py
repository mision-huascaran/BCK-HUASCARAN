"""Pruebas de /profesores.

Un profesor son dos filas que tienen que viajar juntas: la cuenta en `usuario` y la
ficha en `docente`. Estas pruebas vigilan sobre todo que no se desincronicen — nombres,
estado activo — y que la contraseña temporal solo aparezca cuando el correo no salió.
"""
from sqlmodel import select

from app.models.organizacion import Docente, Rol, Usuario
from tests.conftest import texto_del_correo


def profesor_nuevo(**cambios):
    cuerpo = {
        "nombres": "Pedro",
        "apellidos": "Mamani",
        "correo": "pedro.mamani@sicedu.test",
    }
    cuerpo.update(cambios)
    return cuerpo


# ── Alta ─────────────────────────────────────────────────────────────────────────────

def test_supervisor_crea_profesor_y_le_llega_la_clave_por_correo(
    client, session, entorno, sesiones, correos
):
    respuesta = client.post("/profesores", json=profesor_nuevo(), headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    creado = respuesta.json()
    assert creado["correo"] == "pedro.mamani@sicedu.test"
    assert creado["id_docente"] is not None
    assert creado["id_rol"] == entorno["roles"]["Docente"].id_rol
    # El correo salió, así que la clave temporal no viaja en la respuesta: queda
    # únicamente en el buzón del profesor.
    assert creado["contraseña_temporal"] is None

    assert len(correos) == 1
    assert correos[0]["To"] == "pedro.mamani@sicedu.test"
    assert "Contraseña temporal" in texto_del_correo(correos[0])

    # Las dos filas quedaron creadas y enlazadas.
    ficha = session.get(Docente, creado["id_docente"])
    assert (ficha.nombres, ficha.apellidos) == ("Pedro", "Mamani")


def test_si_el_correo_falla_la_clave_temporal_vuelve_en_la_respuesta(
    client, entorno, sesiones, correos
):
    """Sin este reintento manual el profesor se quedaría sin manera de entrar."""
    correos.fallar = True

    respuesta = client.post("/profesores", json=profesor_nuevo(), headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["contraseña_temporal"]
    assert correos == []


def test_el_profesor_creado_puede_iniciar_sesion_con_su_clave_temporal(
    client, entorno, sesiones, correos
):
    correos.fallar = True
    creacion = client.post("/profesores", json=profesor_nuevo(), headers=sesiones["Supervisor"])
    temporal = creacion.json()["contraseña_temporal"]

    respuesta = client.post(
        "/login", json={"correo": "pedro.mamani@sicedu.test", "password": temporal}
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["access_token"]


def test_no_se_puede_repetir_el_correo(client, entorno, sesiones):
    ocupado = profesor_nuevo(correo=entorno["usuarios"]["Docente"].correo)

    respuesta = client.post("/profesores", json=ocupado, headers=sesiones["Supervisor"])

    assert respuesta.status_code == 409
    assert "ya está registrado" in respuesta.json()["detail"]


def test_sin_el_rol_docente_en_el_catalogo_el_alta_avisa(client, session, entorno, sesiones):
    """Si el seed no corrió, el alta no puede inventarse el rol: falla y lo dice."""
    session.delete(entorno["roles"]["Docente"])
    session.commit()

    respuesta = client.post("/profesores", json=profesor_nuevo(), headers=sesiones["Supervisor"])

    assert respuesta.status_code == 500
    assert "el rol Docente no existe" in respuesta.json()["detail"]


def test_profesor_creado_inactivo(client, session, entorno, sesiones):
    respuesta = client.post(
        "/profesores", json=profesor_nuevo(activo=False), headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["activo"] is False
    assert session.get(Docente, respuesta.json()["id_docente"]).activo is False


# ── Listado ──────────────────────────────────────────────────────────────────────────

def test_listado_de_profesores(client, entorno, sesiones):
    respuesta = client.get("/profesores", headers=sesiones["Supervisor"])

    assert respuesta.status_code == 200, respuesta.text
    devueltos = respuesta.json()
    # Solo las cuentas con ficha de docente: el Supervisor y el Directivo no salen.
    assert len(devueltos) == 1
    assert devueltos[0]["correo"] == entorno["usuarios"]["Docente"].correo
    assert devueltos[0]["id_docente"] is not None


# ── Activar y desactivar ─────────────────────────────────────────────────────────────

def test_desactivar_y_activar_mantienen_la_ficha_en_el_mismo_estado(
    client, session, entorno, sesiones
):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario
    id_docente = entorno["ficha_docente"].id_docente

    baja = client.patch(f"/profesores/{id_usuario}/desactivar", headers=sesiones["Supervisor"])

    assert baja.status_code == 200, baja.text
    assert baja.json()["activo"] is False
    session.expire_all()
    assert session.get(Docente, id_docente).activo is False

    alta = client.patch(f"/profesores/{id_usuario}/activar", headers=sesiones["Supervisor"])

    assert alta.status_code == 200
    assert alta.json()["activo"] is True
    session.expire_all()
    assert session.get(Docente, id_docente).activo is True


def test_el_profesor_desactivado_ya_no_entra(client, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario
    client.patch(f"/profesores/{id_usuario}/desactivar", headers=sesiones["Supervisor"])

    from tests.conftest import CORREOS, PASSWORD

    respuesta = client.post(
        "/login", json={"correo": CORREOS["Docente"], "password": PASSWORD}
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "Tu cuenta está deshabilitada. Contacta a tu supervisor."


def test_cambiar_estado_de_un_id_que_no_existe(client, entorno, sesiones):
    for accion in ("desactivar", "activar"):
        respuesta = client.patch(f"/profesores/9999/{accion}", headers=sesiones["Supervisor"])
        assert respuesta.status_code == 404
        assert "9999" in respuesta.json()["detail"]


def test_una_cuenta_sin_ficha_de_docente_no_es_un_profesor(client, entorno, sesiones):
    """El Directivo existe como usuario, pero /profesores no lo administra."""
    id_directivo = entorno["usuarios"]["Directivo"].id_usuario

    respuesta = client.patch(
        f"/profesores/{id_directivo}/desactivar", headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 404


# ── Edición ──────────────────────────────────────────────────────────────────────────

def test_editar_profesor_actualiza_tambien_su_ficha(client, session, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario
    id_docente = entorno["ficha_docente"].id_docente

    respuesta = client.patch(
        f"/profesores/{id_usuario}",
        json={"nombres": "Rosa María", "apellidos": "Quispe Ramos"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["nombres"] == "Rosa María"
    session.expire_all()
    ficha = session.get(Docente, id_docente)
    # Los nombres viven duplicados en las dos tablas: si solo se tocara `usuario`, el
    # listado de docentes seguiría mostrando el nombre viejo.
    assert (ficha.nombres, ficha.apellidos) == ("Rosa María", "Quispe Ramos")


def test_editar_solo_el_correo(client, session, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    respuesta = client.patch(
        f"/profesores/{id_usuario}",
        json={"correo": "rosa.nueva@sicedu.test"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["correo"] == "rosa.nueva@sicedu.test"
    assert respuesta.json()["nombres"] == "Rosa"


def test_editar_al_correo_de_otra_cuenta(client, entorno, sesiones):
    id_usuario = entorno["usuarios"]["Docente"].id_usuario

    respuesta = client.patch(
        f"/profesores/{id_usuario}",
        json={"correo": entorno["usuarios"]["Supervisor"].correo},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 409
    assert "ya está registrado" in respuesta.json()["detail"]


def test_dejar_el_mismo_correo_no_es_un_conflicto(client, entorno, sesiones):
    """Reenviar el correo que ya tenía la cuenta no debe chocar consigo misma."""
    usuario = entorno["usuarios"]["Docente"]

    respuesta = client.patch(
        f"/profesores/{usuario.id_usuario}",
        json={"correo": usuario.correo, "nombres": "Rosa Elena"},
        headers=sesiones["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["nombres"] == "Rosa Elena"


def test_editar_profesor_inexistente(client, entorno, sesiones):
    respuesta = client.patch(
        "/profesores/9999", json={"nombres": "Fantasma"}, headers=sesiones["Supervisor"]
    )

    assert respuesta.status_code == 404


# ── Permisos ─────────────────────────────────────────────────────────────────────────

def test_profesores_es_parcela_exclusiva_del_supervisor(client, entorno, sesiones):
    """Ni el Docente ni el Directivo tocan la gestión de profesores."""
    id_usuario = entorno["usuarios"]["Docente"].id_usuario
    for rol in ("Docente", "Directivo"):
        cabecera = sesiones[rol]
        assert client.get("/profesores", headers=cabecera).status_code == 403
        assert client.post("/profesores", json=profesor_nuevo(), headers=cabecera).status_code == 403
        assert client.patch(
            f"/profesores/{id_usuario}", json={"nombres": "X"}, headers=cabecera
        ).status_code == 403
        assert client.patch(
            f"/profesores/{id_usuario}/desactivar", headers=cabecera
        ).status_code == 403
        assert client.patch(
            f"/profesores/{id_usuario}/activar", headers=cabecera
        ).status_code == 403


def test_profesores_sin_sesion(client, entorno):
    assert client.get("/profesores").status_code == 401
