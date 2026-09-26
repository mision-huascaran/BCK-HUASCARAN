"""Pruebas de los catálogos de apoyo: GET /grados y GET /programas.

Son de solo lectura y los ve cualquier usuario con sesión: el frontend los necesita
para pintar los desplegables de las pantallas de alumnos.
"""


def test_grados_los_ve_cualquier_rol(client, entorno, sesiones):
    for rol, cabecera in sesiones.items():
        respuesta = client.get("/grados", headers=cabecera)

        assert respuesta.status_code == 200, f"{rol}: {respuesta.text}"
        nombres = [grado["nombre"] for grado in respuesta.json()]
        assert nombres == ["1° primaria", "2° primaria"]


def test_programas_los_ve_cualquier_rol(client, entorno, sesiones):
    for rol, cabecera in sesiones.items():
        respuesta = client.get("/programas", headers=cabecera)

        assert respuesta.status_code == 200, f"{rol}: {respuesta.text}"
        assert [p["nombre"] for p in respuesta.json()] == ["Lectura", "Matemática"]


def test_catalogos_sin_sesion(client, entorno):
    assert client.get("/grados").status_code == 401
    assert client.get("/programas").status_code == 401
