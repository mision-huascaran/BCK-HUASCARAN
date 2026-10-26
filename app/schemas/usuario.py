"""Gestión de cuentas de usuario (CU016). Solo el Supervisor."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.correo import CorreoValido
from app.schemas.comun import Dni, ParcheSinNulos, Texto


# ── Requests ────────────────────────────────────────────────────────────────────────

# Si el colegio está hoy en un receso (PR n): "retroactivo" agrega el PA n recién
# culminado y sigue con el próximo; "proximo" empieza en el PA n + 1 (no existe en el PR4).
# Obligatorio solo en ese caso; en un PA o en un año que no empezó se ignora.
InicioAsignacion = Literal["retroactivo", "proximo"]


class ColegioDeAsignacion(BaseModel):
    id_colegio: int
    # Sin `grados`: en un alta, todos los que ofrece el colegio; en una edición, los que el
    # Docente ya tenía en ese colegio en el periodo de inicio (o todos, si no lo tenía).
    # Si viene, no puede estar vacía.
    grados: Optional[list[int]] = Field(default=None, min_length=1)
    inicio_asignacion: Optional[InicioAsignacion] = None


class AsignacionDocente(BaseModel):
    """Colegios y grados del Docente en un año escolar. En PATCH es el conjunto completo
    deseado para ese año: los colegios que no vienen se liberan en sus PA no terminados."""

    id_anio_escolar: int
    colegios: list[ColegioDeAsignacion] = Field(min_length=1)


class UsuarioCrear(BaseModel):
    nombres: Texto
    apellidos: Texto
    dni: Dni
    correo: CorreoValido
    id_rol: int
    # Obligatoria para Docente, prohibida para los demás roles.
    asignacion: Optional[AsignacionDocente] = None


class UsuarioEditar(ParcheSinNulos):
    nombres: Optional[Texto] = None
    apellidos: Optional[Texto] = None
    dni: Optional[Dni] = None
    correo: Optional[CorreoValido] = None
    id_rol: Optional[int] = None
    asignacion: Optional[AsignacionDocente] = None


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
    # Aún no hizo su primer ingreso (no cambió la contraseña temporal). Solo lectura.
    debe_cambiar_contrasena: bool
    # Docente: colegios de sus asignaciones del periodo vigente. Supervisor y
    # Directivo: ["Global"].
    colegios_asignados: list[str]


class GradoAsignado(BaseModel):
    id_grado: int
    nombre: str


class PeriodoAsignado(BaseModel):
    id_periodo: int
    numero: int
    # Si es el PA en que hoy está ese colegio (CU027).
    vigente: bool
    # Habilitado con "Retroactivo" durante el PR que le sigue.
    retroactiva: bool
    grados: list[GradoAsignado]


class ColegioAsignadoDetalle(BaseModel):
    id_colegio: int
    colegio: str
    periodos: list[PeriodoAsignado]


class AsignacionDetalle(BaseModel):
    id_anio_escolar: int
    colegios: list[ColegioAsignadoDetalle]


class UsuarioDetalle(UsuarioItem):
    # Solo para Docentes con asignaciones; null en los demás casos.
    asignacion: Optional[AsignacionDetalle] = None


class UsuarioCreado(BaseModel):
    usuario: UsuarioItem
    correo_enviado: bool
    # Solo trae valor si el correo de bienvenida no se pudo enviar.
    contraseña_temporal: Optional[str] = None
