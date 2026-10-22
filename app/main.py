from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errores import registrar_manejadores
from app.core.proteccion_http import ProteccionHttp
from app.routers import (
    actividades,
    alumnos,
    asignaciones,
    asistencias,
    auth,
    calendarios,
    catalogos,
    clases,
    colegios,
    inicio,
    lectura,
    password,
    permisos,
    registro_vuelo,
    rubricas,
    seguimiento,
    sincronizacion,
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
# Después de CORS para quedar por fuera: sus cabeceras también llegan a los preflight.
app.add_middleware(ProteccionHttp)

app.include_router(auth.router)
app.include_router(colegios.router)
app.include_router(alumnos.router)
app.include_router(catalogos.router)
app.include_router(calendarios.router)
app.include_router(password.router)
app.include_router(usuarios.router)
app.include_router(permisos.router)
app.include_router(asignaciones.router)
app.include_router(actividades.router)
app.include_router(asistencias.router)
app.include_router(lectura.router)
app.include_router(rubricas.router)
app.include_router(registro_vuelo.router)
app.include_router(clases.router)
app.include_router(inicio.router)
app.include_router(seguimiento.router)
app.include_router(sincronizacion.router)


@app.get("/")
def read_root():
    return {"status": "ok"}
