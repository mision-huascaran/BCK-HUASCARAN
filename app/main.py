from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errores import registrar_manejadores
from app.routers import (
    actividades,
    alumnos,
    asignaciones,
    auth,
    catalogos,
    colegios,
    inicio,
    password,
    usuarios,
)

app = FastAPI(title="SICEDU API")
registrar_manejadores(app)

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
app.include_router(catalogos.router)
app.include_router(password.router)
app.include_router(usuarios.router)
app.include_router(asignaciones.router)
app.include_router(actividades.router)
app.include_router(inicio.router)


@app.get("/")
def read_root():
    return {"status": "ok"}
