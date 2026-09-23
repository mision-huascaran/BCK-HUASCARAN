from pydantic import BaseModel, field_validator, model_validator


class VerificarCodigoRequest(BaseModel):
    codigo: str


class CambiarContraseñaRequest(BaseModel):
    codigo: str
    contraseña_nueva: str
    confirmar_contraseña_nueva: str

    @model_validator(mode="after")
    def validar_coinciden(self):
        if self.contraseña_nueva != self.confirmar_contraseña_nueva:
            raise ValueError("La contraseña nueva y su confirmación no coinciden")
        return self

    @field_validator("contraseña_nueva")
    @classmethod
    def validar_formato(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")
        if not v.isalnum():
            raise ValueError("La contraseña solo puede contener letras y números")
        return v
