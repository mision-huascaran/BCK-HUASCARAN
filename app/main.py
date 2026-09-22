from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import alumnos, auth, catalogos, colegios, profesores

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


@app.get("/")
def read_root():
    return {"status": "ok"}
