"""
Levanta todo el backend de SICEDU con un solo comando:

    python run.py

Pasos (cada uno se salta si ya esta hecho):
  1. Arranca Docker Desktop si esta apagado.
  2. Arranca (o crea) el contenedor PostgreSQL `sicedu-db` y espera a que acepte conexiones.
  3. Crea el entorno virtual `venv` e instala dependencias si cambio requirements.txt.
  4. Crea el `.env` con valores de desarrollo si no existe.
  5. Aplica las migraciones de Alembic.
  6. Carga los datos de prueba (idempotente).
  7. Inicia uvicorn con recarga automatica en http://127.0.0.1:8000

Opciones:
  --sin-seed   no carga los datos de prueba
  --puerto N   puerto de la API (por defecto 8000)

Solo usa la libreria estandar: funciona con el Python del sistema antes de que exista el venv.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / "venv"
ES_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV / ("Scripts/python.exe" if ES_WINDOWS else "bin/python")
REQUIREMENTS = RAIZ / "requirements.txt"
SELLO_REQUIREMENTS = VENV / ".requirements.sha256"

CONTENEDOR = "sicedu-db"
DB_USUARIO = "admin"
DB_PASSWORD = "postgres123"
DB_NOMBRE = "sicedu"
DB_PUERTO = 5433

ENV_POR_DEFECTO = f"""DATABASE_URL=postgresql+psycopg2://{DB_USUARIO}:{DB_PASSWORD}@localhost:{DB_PUERTO}/{DB_NOMBRE}
JWT_SECRET_KEY=dev-secret-key-cambiar-en-produccion
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000
"""

DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")


def paso(n: int, texto: str) -> None:
    print(f"\n[{n}/7] {texto}", flush=True)


def ok(texto: str) -> None:
    print(f"      OK  {texto}", flush=True)


def fallar(texto: str) -> None:
    print(f"\n  ERROR  {texto}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def ejecutar(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=RAIZ, **kwargs)


def silencioso(cmd: list) -> subprocess.CompletedProcess:
    return ejecutar(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


# --- 1. Docker ---------------------------------------------------------------

def docker_responde() -> bool:
    return silencioso(["docker", "info"]).returncode == 0


def asegurar_docker() -> None:
    paso(1, "Docker")
    if shutil.which("docker") is None:
        fallar("No se encontro el comando `docker`. Instala Docker Desktop: https://www.docker.com/products/docker-desktop/")

    if docker_responde():
        ok("Docker ya esta corriendo")
        return

    if not (ES_WINDOWS and DOCKER_DESKTOP.exists()):
        fallar("Docker no esta corriendo. Inicia Docker Desktop y vuelve a ejecutar este comando.")

    print("      Docker apagado, iniciando Docker Desktop (puede tardar 1-2 minutos)...", flush=True)
    subprocess.Popen([str(DOCKER_DESKTOP)])
    limite = time.time() + 180
    while time.time() < limite:
        if docker_responde():
            ok("Docker Desktop iniciado")
            return
        time.sleep(3)
    fallar("Docker Desktop no respondio en 3 minutos. Abrelo a mano y vuelve a intentar.")


# --- 2. PostgreSQL -------------------------------------------------------------

def estado_contenedor() -> str | None:
    r = silencioso(["docker", "inspect", "-f", "{{.State.Status}}", CONTENEDOR])
    return r.stdout.strip() if r.returncode == 0 else None


def asegurar_postgres() -> None:
    paso(2, f"Base de datos PostgreSQL (contenedor {CONTENEDOR}, puerto {DB_PUERTO})")
    estado = estado_contenedor()

    if estado is None:
        print("      El contenedor no existe, creandolo (la primera vez descarga la imagen)...", flush=True)
        r = ejecutar([
            "docker", "run", "--name", CONTENEDOR,
            "-e", f"POSTGRES_USER={DB_USUARIO}",
            "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e", f"POSTGRES_DB={DB_NOMBRE}",
            "-p", f"{DB_PUERTO}:5432",
            "-d", "postgres:16",
        ])
        if r.returncode != 0:
            fallar(f"No se pudo crear el contenedor. Revisa que el puerto {DB_PUERTO} este libre.")
    elif estado != "running":
        r = silencioso(["docker", "start", CONTENEDOR])
        if r.returncode != 0:
            fallar(f"No se pudo iniciar el contenedor:\n{r.stderr.strip()}")
    else:
        ok("El contenedor ya estaba corriendo")

    limite = time.time() + 60
    while time.time() < limite:
        r = silencioso(["docker", "exec", CONTENEDOR, "pg_isready", "-U", DB_USUARIO, "-d", DB_NOMBRE])
        if r.returncode == 0:
            ok("PostgreSQL acepta conexiones")
            return
        time.sleep(2)
    fallar("PostgreSQL no respondio en 60 segundos. Revisa: docker logs " + CONTENEDOR)


# --- 3. Entorno virtual y dependencias ----------------------------------------

def huella_requirements() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def asegurar_dependencias() -> None:
    paso(3, "Entorno virtual y dependencias")
    if not VENV_PYTHON.exists():
        print("      Creando venv...", flush=True)
        if ejecutar([sys.executable, "-m", "venv", str(VENV)]).returncode != 0:
            fallar("No se pudo crear el entorno virtual.")

    huella = huella_requirements()
    if SELLO_REQUIREMENTS.exists() and SELLO_REQUIREMENTS.read_text().strip() == huella:
        ok("Dependencias al dia")
        return

    print("      Instalando dependencias de requirements.txt...", flush=True)
    silencioso([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    r = ejecutar([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS), "-q"])
    if r.returncode != 0:
        fallar("Fallo la instalacion de dependencias (mira el error de pip arriba).")
    SELLO_REQUIREMENTS.write_text(huella)
    ok("Dependencias instaladas")


# --- 4. .env -------------------------------------------------------------------

def asegurar_env() -> None:
    paso(4, "Archivo .env")
    env = RAIZ / ".env"
    if env.exists():
        ok(".env ya existe (no se modifica)")
        return
    env.write_text(ENV_POR_DEFECTO, encoding="utf-8")
    ok(".env creado con valores de desarrollo")


# --- 5 y 6. Migraciones y seed -------------------------------------------------

def migrar() -> None:
    paso(5, "Migraciones de Alembic")
    r = ejecutar([str(VENV_PYTHON), "-m", "alembic", "upgrade", "head"])
    if r.returncode != 0:
        fallar("Fallaron las migraciones (mira el error de arriba).")
    ok("Base de datos en la ultima version")


def sembrar(saltar: bool) -> None:
    paso(6, "Datos de prueba")
    if saltar:
        ok("Omitido (--sin-seed)")
        return
    r = silencioso([str(VENV_PYTHON), "-m", "app.seed_data"])
    if r.returncode != 0:
        fallar(f"Fallo el seed:\n{r.stderr.strip()}")
    ok("Usuarios de prueba listos")


# --- 7. Servidor ---------------------------------------------------------------

def puerto_ocupado(puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def iniciar_servidor(puerto: int) -> None:
    paso(7, "Servidor de la API")
    if puerto_ocupado(puerto):
        fallar(
            f"El puerto {puerto} ya esta en uso: probablemente el backend ya esta corriendo en otra terminal.\n"
            f"         Cierra esa terminal, o usa otro puerto:  python run.py --puerto 8001"
        )

    print(f"""
  ------------------------------------------------------------
   SICEDU API corriendo
     API:      http://127.0.0.1:{puerto}
     Swagger:  http://127.0.0.1:{puerto}/docs

   Usuarios de prueba:
     profesor.prueba@sicedu.test / ProfesorTest123
     jefa.prueba@sicedu.test     / JefaTest123
     directivo.prueba@sicedu.test / DirectivoTest123

   Ctrl+C para detener (la base de datos sigue corriendo en Docker)
  ------------------------------------------------------------
""", flush=True)

    try:
        ejecutar([
            str(VENV_PYTHON), "-m", "uvicorn", "app.main:app",
            "--reload", "--host", "127.0.0.1", "--port", str(puerto),
        ])
    except KeyboardInterrupt:
        pass
    print("\nServidor detenido.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Levanta todo el backend de SICEDU.")
    parser.add_argument("--sin-seed", action="store_true", help="no cargar datos de prueba")
    parser.add_argument("--puerto", type=int, default=8000, help="puerto de la API (por defecto 8000)")
    args = parser.parse_args()

    os.chdir(RAIZ)
    print("=== Iniciando backend SICEDU ===", flush=True)
    asegurar_docker()
    asegurar_postgres()
    asegurar_dependencias()
    asegurar_env()
    migrar()
    sembrar(args.sin_seed)
    iniciar_servidor(args.puerto)


if __name__ == "__main__":
    main()
