"""generar-recovery-keys: Recovery Keys del Supervisor original (CU001). SOLO uso local.

No toca la BD. Escribe las llaves en texto plano en un .txt (que se entrega a la alta
dirección y nunca entra al repositorio) y devuelve la línea para el .env con sus hashes
bcrypt; el comando inicializar-supervisor-original los guarda en `recovery_key`.
"""
from pathlib import Path

from app.cli.comun import RAIZ_PROYECTO
from app.core.recovery_key import CANTIDAD_LLAVES, generar_llave
from app.core.security import hash_password
from app.core.tiempo import ZONA_LIMA, ahora_utc

VARIABLE_ENV = "SUPERVISOR_ORIGINAL_RECOVERY_HASHES"


class SalidaNoPermitida(Exception):
    """La ruta de salida no se puede usar (ya existe o cae dentro del repositorio)."""


def generar_llaves(cantidad: int = CANTIDAD_LLAVES) -> list[str]:
    return [generar_llave() for _ in range(cantidad)]


def validar_salida(ruta: Path, raiz_repositorio: Path = RAIZ_PROYECTO) -> Path:
    ruta = Path(ruta).expanduser().resolve()
    if ruta.is_relative_to(raiz_repositorio.resolve()):
        raise SalidaNoPermitida(
            f"{ruta} está dentro del repositorio ({raiz_repositorio}). Las llaves en texto "
            "plano no pueden quedar ahí: usa una ruta fuera del repo."
        )
    if ruta.exists():
        raise SalidaNoPermitida(f"{ruta} ya existe y no se sobrescribe.")
    if not ruta.parent.is_dir():
        raise SalidaNoPermitida(f"La carpeta {ruta.parent} no existe.")
    return ruta


def contenido_archivo(llaves: list[str]) -> str:
    generado = ahora_utc().astimezone(ZONA_LIMA).strftime("%Y-%m-%d %H:%M (hora de Lima)")
    lineas = [
        "SICEDU - Recovery Keys del Supervisor original",
        f"Generadas: {generado}",
        "",
        "Instrucciones:",
        "- Cada llave sirve UNA sola vez para recuperar el acceso del Supervisor original",
        "  sin depender del correo (CU001). Una vez usada, queda invalidada.",
        "- Guarda este archivo fuera de cualquier computadora compartida (impreso o en un",
        "  gestor de contraseñas) y no lo envíes por correo ni chat.",
        "- El sistema solo guarda el hash de cada llave: si se pierde este archivo, las",
        "  llaves no se pueden recuperar; hay que generar unas nuevas.",
        "",
    ]
    lineas += [f"{i:2d}. {llave}" for i, llave in enumerate(llaves, start=1)]
    return "\n".join(lineas) + "\n"


def generar_recovery_keys(salida: Path) -> str:
    """Escribe el .txt con las llaves y devuelve la línea lista para pegar en el .env.

    Lanza SalidaNoPermitida si la ruta no se puede usar; en ese caso no escribe nada.
    """
    ruta = validar_salida(salida)
    llaves = generar_llaves()
    hashes = [hash_password(llave) for llave in llaves]
    # "x": falla si el archivo apareció entre la validación y la escritura.
    with open(ruta, "x", encoding="utf-8") as archivo:
        archivo.write(contenido_archivo(llaves))
    # Comillas simples: ni docker compose ni python-dotenv interpretan los "$" del hash.
    return f"{VARIABLE_ENV}='{','.join(hashes)}'"
