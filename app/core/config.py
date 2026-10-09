from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    # OBSOLETA, sin efecto: la sesión dura 8 horas por regla de negocio (CU003, CU007) y
    # es una constante del servidor (app/services/sesiones.py, DURACION_SESION). Se
    # mantiene declarada solo para que un .env antiguo que aún la tenga no impida
    # arrancar (Settings rechaza variables desconocidas del .env). Puede borrarse del .env.
    ACCESS_TOKEN_EXPIRE_MINUTES: Optional[int] = None

    # Origenes permitidos por CORS, separados por coma.
    # localhost y 127.0.0.1 son origenes distintos para el navegador: van ambos.
    CORS_ORIGINS: str = (
        "https://localhost:5173,https://127.0.0.1:5173,"
        "https://localhost:3000,https://127.0.0.1:3000"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origen.strip() for origen in self.CORS_ORIGINS.split(",") if origen.strip()]

    GMAIL_SMTP_USER: str = ""
    GMAIL_SMTP_APP_PASSWORD: str = ""

    # Supervisor original (CU001): lo crea `python -m app.cli inicializar` la primera vez.
    # Vacías en local si no se necesita; el comando avisa y lo omite.
    SUPERVISOR_ORIGINAL_CORREO: str = ""
    SUPERVISOR_ORIGINAL_PASSWORD: SecretStr = SecretStr("")
    SUPERVISOR_ORIGINAL_NOMBRES: str = ""
    SUPERVISOR_ORIGINAL_APELLIDOS: str = ""
    SUPERVISOR_ORIGINAL_DNI: str = ""
    # Los 10 hashes bcrypt de las Recovery Keys, separados por comas.
    SUPERVISOR_ORIGINAL_RECOVERY_HASHES: SecretStr = SecretStr("")

    # Solo para la primera carga de los colegios reales; después se apaga (ver README).
    CARGAR_COLEGIOS_INICIALES: bool = False

    class Config:
        env_file = ".env"


settings = Settings()
