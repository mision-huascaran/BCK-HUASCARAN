from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

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
    #GMAIL_SMTP_USER_2: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
