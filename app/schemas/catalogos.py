from pydantic import BaseModel, ConfigDict


class GradoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_grado: int
    nombre: str
    id_ciclo: int


class ProgramaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_programa: int
    nombre: str
