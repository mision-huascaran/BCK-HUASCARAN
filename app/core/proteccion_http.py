"""Protecciones HTTP comunes a toda la API (middleware ASGI).

- Un carácter nulo (NUL, \\x00) en la query o en un cuerpo JSON se rechaza con el 422
  `validacion` de siempre, antes de llegar a la base: PostgreSQL no admite NUL en textos
  y, sin esto, la petición terminaba en un 500 (lo detectó OWASP ZAP).
- Toda respuesta lleva `X-Content-Type-Options: nosniff` y
  `Cross-Origin-Resource-Policy: same-origin`. El frontend llama a la API por su propio
  dominio (`/api`, proxy de nginx), así que same-origin no lo afecta.

Es ASGI puro (no BaseHTTPMiddleware) para poder leer el cuerpo una vez y entregárselo
igual a la aplicación.
"""
import json
from typing import Any, Optional
from urllib.parse import parse_qsl

from app.core.validacion import MENSAJE_GENERAL, MOTIVO_VALIDACION

MENSAJE_NULO = "No puede contener caracteres nulos."
CABECERAS = (
    (b"x-content-type-options", b"nosniff"),
    (b"cross-origin-resource-policy", b"same-origin"),
)
METODOS_CON_CUERPO = frozenset({"POST", "PUT", "PATCH"})


def campo_con_nulo_en_query(query_string: bytes) -> Optional[str]:
    """Nombre del primer parámetro de la query con un NUL (en el nombre o el valor)."""
    for nombre, valor in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True):
        if "\x00" in nombre or "\x00" in valor:
            return nombre.replace("\x00", "")
    return None


def _subruta(ruta: str, clave: Any) -> str:
    return f"{ruta}.{clave}" if ruta else str(clave)


def _primera(rutas) -> Optional[str]:
    return next((r for r in rutas if r is not None), None)


def _rutas_en_objeto(dato: dict, ruta: str):
    for clave, valor in dato.items():
        subruta = _subruta(ruta, clave)
        yield subruta.replace("\x00", "") if "\x00" in str(clave) else _ruta_con_nulo(valor, subruta)


def _ruta_con_nulo(dato: Any, ruta: str) -> Optional[str]:
    """Ruta del primer texto (valor o clave) con un NUL, o None."""
    if isinstance(dato, str):
        return ruta if "\x00" in dato else None
    if isinstance(dato, dict):
        return _primera(_rutas_en_objeto(dato, ruta))
    if isinstance(dato, list):
        return _primera(_ruta_con_nulo(valor, _subruta(ruta, i)) for i, valor in enumerate(dato))
    return None


def campo_con_nulo_en_json(cuerpo: bytes) -> Optional[str]:
    """Ruta (p. ej. `registros.0.observacion`) del primer texto del JSON con un NUL.
    Solo se decodifica si el cuerpo puede tenerlo; un JSON inválido lo rechaza FastAPI."""
    if b"\\u0000" not in cuerpo and b"\x00" not in cuerpo:
        return None
    try:
        dato = json.loads(cuerpo)
    except ValueError:
        return None
    return _ruta_con_nulo(dato, "")


async def _leer_cuerpo(receive) -> bytes:
    partes = []
    while True:
        mensaje = await receive()
        if mensaje["type"] != "http.request":
            break
        partes.append(mensaje.get("body", b""))
        if not mensaje.get("more_body", False):
            break
    return b"".join(partes)


def _reproducir(cuerpo: bytes, receive):
    """`receive` que entrega primero el cuerpo ya leído y después delega en el original."""
    entregado = False

    async def receive_reproducido():
        nonlocal entregado
        if not entregado:
            entregado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}
        return await receive()

    return receive_reproducido


def _con_cabeceras(send):
    async def send_con_cabeceras(mensaje):
        if mensaje["type"] == "http.response.start":
            existentes = {nombre.lower() for nombre, _ in mensaje.get("headers", [])}
            extra = [(n, v) for n, v in CABECERAS if n not in existentes]
            mensaje = {**mensaje, "headers": [*mensaje.get("headers", []), *extra]}
        await send(mensaje)

    return send_con_cabeceras


async def _responder_nulo(campo: str, send) -> None:
    cuerpo = json.dumps(
        {"detail": MENSAJE_GENERAL, "motivo": MOTIVO_VALIDACION,
         "errores": [{"campo": campo, "mensaje": MENSAJE_NULO}]},
        ensure_ascii=False,
    ).encode()
    await send({
        "type": "http.response.start",
        "status": 422,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(cuerpo)).encode())],
    })
    await send({"type": "http.response.body", "body": cuerpo})


class ProteccionHttp:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = _con_cabeceras(send)
        campo = campo_con_nulo_en_query(scope.get("query_string", b""))
        if campo is None and scope["method"] in METODOS_CON_CUERPO:
            cuerpo = await _leer_cuerpo(receive)
            campo = campo_con_nulo_en_json(cuerpo)
            receive = _reproducir(cuerpo, receive)
        if campo is not None:
            await _responder_nulo(campo, send)
            return
        await self.app(scope, receive, send)
