from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import (
    alumnos,
    asignaciones,
    auth,
    catalogos,
    colegios,
    password,
    profesores,
    usuarios,
)

app = FastAPI(title="SICEDU API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(colegios.router)
app.include_router(alumnos.router)
app.include_router(profesores.router)
app.include_router(catalogos.router)
app.include_router(password.router)
app.include_router(usuarios.router)
app.include_router(asignaciones.router)


@app.get("/")
def read_root():
    return {"status": "ok"}
