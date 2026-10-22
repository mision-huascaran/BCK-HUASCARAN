"""Prepara el escaneo de OWASP ZAP sobre la API (stage "OWASP ZAP - Backend").

Corre dentro de la imagen de ZAP (trae python3), en la red proxy_net:

    python3 preparar.py preparar   # escribe /zap/wrk/openapi-zap.json e imprime el token
    python3 preparar.py cerrar     # cierra la sesión del token (ZAP_AUTH_HEADER_VALUE)

Variables: ZAP_API_BASE (p. ej. http://qa-bck-huascaran:8000), E2E_SUPERVISOR_USER y
E2E_SUPERVISOR_PASS (las mismas cuentas del seed `sembrar-e2e`).

El escaneo es activo y autenticado como Supervisor, así que la copia del OpenAPI que
recibe ZAP se recorta para que no pueda dañar datos reales de qa ni su propia sesión:

- Se quitan los endpoints que cerrarían la sesión del escaneo (/logout y el cierre de
  sesiones), los que cambian el estado de un registro (activar/desactivar: podrían
  inactivar cuentas reales o las del seed E2E) y los de contraseña (envían PINs por
  correo y cuentan intentos).
- Los ids de la ruta y los `id_*` de los cuerpos apuntan a registros que no existen
  (999999), así los PATCH responden 404 y los POST no llegan a crear nada; los demás
  campos siguen recibiendo los ataques de ZAP.

Lo único que va por stdout es el token; los mensajes van a stderr.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ID_INEXISTENTE = 999999
UUID_INEXISTENTE = "00000000-0000-4000-8000-000000000000"
SALIDA = "/zap/wrk/openapi-zap.json"


def _base() -> str:
    return os.environ["ZAP_API_BASE"].rstrip("/")


def _pedir(metodo: str, ruta: str, cuerpo=None, token=None):
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = token
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(_base() + ruta, data=datos, headers=cabeceras, method=metodo)
    with urllib.request.urlopen(req, timeout=30) as respuesta:
        texto = respuesta.read()
        return json.loads(texto) if texto else None


def _excluido(metodo: str, ruta: str) -> bool:
    return (
        ruta == "/logout"
        or ruta.startswith("/me/sesiones/")
        or ruta.endswith(("/activar", "/desactivar"))
        or ruta.startswith(("/password/", "/me/password"))
    )


def _ids_inexistentes(esquemas: dict) -> None:
    """Ids de los cuerpos (componentes del OpenAPI) apuntando a registros inexistentes:
    los `id_*` enteros y las listas de enteros (p. ej. `programas` y `grados`)."""
    for esquema in esquemas.values():
        for nombre, prop in esquema.get("properties", {}).items():
            for variante in prop.get("anyOf", [prop]):
                if nombre.startswith("id_") and variante.get("type") == "integer":
                    variante["example"] = ID_INEXISTENTE
                elif variante.get("type") == "array" and variante.get("items", {}).get("type") == "integer":
                    variante["example"] = [ID_INEXISTENTE]


def _parametros_inexistentes(operacion: dict) -> None:
    for parametro in operacion.get("parameters", []):
        if parametro.get("in") != "path":
            continue
        esquema = parametro.setdefault("schema", {})
        valor = UUID_INEXISTENTE if esquema.get("format") == "uuid" else ID_INEXISTENTE
        parametro["example"] = valor
        esquema["example"] = valor


def preparar() -> None:
    usuario = os.environ.get("E2E_SUPERVISOR_USER", "")
    clave = os.environ.get("E2E_SUPERVISOR_PASS", "")
    if not usuario or not clave:
        sys.exit("ERROR: faltan E2E_SUPERVISOR_USER / E2E_SUPERVISOR_PASS")

    especificacion = _pedir("GET", "/openapi.json")
    quitados = []
    for ruta in list(especificacion["paths"]):
        operaciones = especificacion["paths"][ruta]
        for metodo in list(operaciones):
            if _excluido(metodo.upper(), ruta):
                quitados.append(f"{metodo.upper()} {ruta}")
                del operaciones[metodo]
            else:
                _parametros_inexistentes(operaciones[metodo])
        if not operaciones:
            del especificacion["paths"][ruta]
    _ids_inexistentes(especificacion.get("components", {}).get("schemas", {}))

    with open(SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(especificacion, archivo, ensure_ascii=False, indent=2)
    total = sum(len(o) for o in especificacion["paths"].values())
    print(f"OpenAPI para ZAP: {total} operaciones; fuera del escaneo: {', '.join(quitados)}", file=sys.stderr)

    sesion = _pedir("POST", "/login", {"correo": usuario, "password": clave})
    print("Bearer " + sesion["access_token"])


def cerrar() -> None:
    token = os.environ.get("ZAP_AUTH_HEADER_VALUE", "")
    if not token:
        return
    try:
        _pedir("POST", "/logout", {}, token=token)
        print("Sesión del escaneo cerrada.", file=sys.stderr)
    except urllib.error.URLError as error:
        print(f"AVISO: no se pudo cerrar la sesión del escaneo: {error}", file=sys.stderr)


if __name__ == "__main__":
    {"preparar": preparar, "cerrar": cerrar}[sys.argv[1]]()
