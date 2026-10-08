"""Gestión de cuentas de usuario (CU016). Solo el Supervisor."""
from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.core.correo import CorreoValido

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Texto y no número: un DNI puede empezar con 0.
Dni = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{8}$")]


# ── Requests ────────────────────────────────────────────────────────────────────────

class AsignacionCrear(BaseModel):
    id_colegio: int
    id_anio_escolar: int
    # Sin `grados`: todos los que ofrece el colegio. Si viene, no puede estar vacía.
    grados: Optional[list[int]] = Field(default=None, min_length=1)


class UsuarioCrear(BaseModel):
    nombres: Texto
    apellidos: Texto
    dni: Dni
    correo: CorreoValido
    id_rol: int
    # Obligatoria para Docente, prohibida para los demás roles.
    asignacion: Optional[AsignacionCrear] = None


class _SinNulos(BaseModel):
    """En un PATCH, omitir un campo es "no cambiarlo"; enviarlo en null no tiene sentido
    para estos campos y se rechaza con 422 (antes terminaba en un 500 de la BD)."""

    @model_validator(mode="after")
    def rechazar_nulos(self):
        nulos = sorted(c for c in self.model_fields_set if getattr(self, c) is None)
        if nulos:
            raise ValueError(f"Estos campos no pueden ser null: {', '.join(nulos)}")
        return self


class AsignacionEditar(_SinNulos):
    id_colegio: Optional[int] = None
    grados: Optional[list[int]] = Field(default=None, min_length=1)
    id_anio_escolar: Optional[int] = None
    # Id del periodo desde el que aplica el cambio; por defecto el vigente (o el próximo).
    desde_periodo: Optional[int] = None


class UsuarioEditar(_SinNulos):
    nombres: Optional[Texto] = None
    apellidos: Optional[Texto] = None
    dni: Optional[Dni] = None
    correo: Optional[CorreoValido] = None
    id_rol: Optional[int] = None
    asignacion: Optional[AsignacionEditar] = None


# ── Responses ───────────────────────────────────────────────────────────────────────

class UsuarioItem(BaseModel):
    id: int
    nombres: str
    apellidos: str
    dni: Optional[str] = None
    correo: str
    rol: str
    activo: bool
    es_supervisor_original: bool
    # Docente: colegios de sus asignaciones del periodo vigente. Supervisor y
    # Directivo: ["Global"].
    colegios_asignados: list[str]


class GradoAsignado(BaseModel):
    id_grado: int
    nombre: str


class PeriodoAsignado(BaseModel):
    id_periodo: int
    numero: int
    vigente: bool
    id_colegio: int
    colegio: str
    grados: list[GradoAsignado]


class AsignacionDetalle(BaseModel):
    id_anio_escolar: int
    periodos: list[PeriodoAsignado]


class UsuarioDetalle(UsuarioItem):
    # Solo para Docentes con asignaciones; null en los demás casos.
    asignacion: Optional[AsignacionDetalle] = None


class UsuarioCreado(BaseModel):
    usuario: UsuarioItem
    correo_enviado: bool
    # Solo trae valor si el correo de bienvenida no se pudo enviar.
    contraseña_temporal: Optional[str] = None
