"""Protecciones HTTP de toda la API: NUL en la entrada → 422 y cabeceras de seguridad
(hallazgos de OWASP ZAP en qa)."""
import asyncio

from app.core.config import settings

from app.core.proteccion_http import (
    MENSAJE_NULO,
    ProteccionHttp,
    _leer_cuerpo,
    _reproducir,
    campo_con_nulo_en_json,
    campo_con_nulo_en_query,
)
from tests.conftest import motivo


def errores(respuesta):
    return [(e["campo"], e["mensaje"]) for e in respuesta.json()["errores"]]


def test_nulo_en_un_filtro_de_texto_responde_422_y_no_500(client):
    for ruta, campo in (("/colegios?departamento=%00", "departamento"), ("/alumnos?q=a%00b", "q"),
                        ("/usuarios?activo=true&q=%00", "q"), ("/colegios/ubicaciones?x%00=1", "x")):
        respuesta = client.get(ruta)

        assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion"), ruta
        assert errores(respuesta) == [(campo, MENSAJE_NULO)]


def test_nulo_en_el_cuerpo_responde_422_con_la_ruta_del_campo(client):
    login = client.post("/login", json={"correo": "a\u0000@sicedu.test", "password": "x"})
    anidado = client.post("/colegios", json={"nombre": "X", "grados": [{"id_grado": 1, "secciones": ["A", "B\u0000"]}]})
    en_una_clave = client.put("/asistencias", json={"registros": [{"id\u0000": 1}]})

    assert (login.status_code, errores(login)) == (422, [("correo", MENSAJE_NULO)])
    assert errores(anidado) == [("grados.0.secciones.1", MENSAJE_NULO)]
    assert errores(en_una_clave) == [("registros.0.id", MENSAJE_NULO)]


def test_un_cuerpo_sin_nulos_llega_intacto_a_la_aplicacion(client):
    # La API leyó el JSON completo: valida el correo y la contraseña y responde como siempre.
    respuesta = client.post("/login", json={"correo": "nadie@sicedu.test", "password": "Clave-Valida-1"})

    assert (respuesta.status_code, motivo(respuesta)) == (401, "credenciales_invalidas")


def test_toda_respuesta_lleva_las_cabeceras_de_seguridad(client):
    for respuesta in (client.get("/"), client.get("/me"), client.get("/colegios?departamento=%00")):
        assert respuesta.headers["x-content-type-options"] == "nosniff"
        assert respuesta.headers["cross-origin-resource-policy"] == "same-origin"


def test_el_422_por_un_nulo_lleva_las_cabeceras_cors(client):
    # CORS va por fuera de ProteccionHttp: el navegador ve el 422 y no un error de CORS.
    origen = settings.cors_origins_list[0]
    respuesta = client.get("/colegios?departamento=%00", headers={"Origin": origen})

    assert respuesta.status_code == 422
    assert respuesta.headers["access-control-allow-origin"] == origen


def test_deteccion_de_nulos():
    assert campo_con_nulo_en_query(b"a=1&b=2") is None
    assert campo_con_nulo_en_json(b'{"a": "sin nulos"}') is None
    # JSON inválido: lo rechaza FastAPI después, con su propio 422.
    assert campo_con_nulo_en_json(b'{"a": "\\u0000"') is None
    assert campo_con_nulo_en_json(b'["x", {"y": "\\u0000"}]') == "1.y"
    assert campo_con_nulo_en_json(b'"\\u0000"') == ""


def test_lee_el_cuerpo_en_partes_y_corta_si_el_cliente_se_desconecta():
    mensajes = iter([
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": False},
    ])
    desconexion = iter([{"type": "http.request", "body": b"ab", "more_body": True}, {"type": "http.disconnect"}])

    async def recibir(fuente):
        return next(fuente)

    assert asyncio.run(_leer_cuerpo(lambda: recibir(mensajes))) == b"abcd"
    assert asyncio.run(_leer_cuerpo(lambda: recibir(desconexion))) == b"ab"


def test_el_cuerpo_se_entrega_una_vez_y_despues_se_delega():
    async def original():
        return {"type": "http.disconnect"}

    async def dos_lecturas():
        receive = _reproducir(b"{}", original)
        return [await receive(), await receive()]

    assert asyncio.run(dos_lecturas()) == [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]


def test_lo_que_no_es_http_pasa_sin_tocar():
    recibidos = []

    async def aplicacion(scope, receive, send):
        recibidos.append(scope["type"])

    asyncio.run(ProteccionHttp(aplicacion)({"type": "lifespan"}, None, None))

    assert recibidos == ["lifespan"]
