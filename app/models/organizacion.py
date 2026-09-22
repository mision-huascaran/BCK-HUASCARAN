from datetime import date
from typing import Optional

from sqlmodel import Field, SQLModel


class AnioEscolar(SQLModel, table=True):
    """Antes `PeriodoAcademico` (v1). Ver diseno_bd_sicedu_v2.md §1.2 sobre el swap de nombres."""

    __tablename__ = "año_escolar"

    id_anio_escolar: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    fecha_inicio: date
    fecha_fin: date

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class PeriodoAcademico(SQLModel, table=True):
    """Antes `PeriodoEvaluacion` (v1). Ver diseno_bd_sicedu_v2.md §1.2 sobre el swap de nombres."""

    __tablename__ = "periodo_academico"

    id_periodo_academico: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key="año_escolar.id_anio_escolar")
    numero: int
    fecha_inicio: date
    fecha_fin: date

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class Colegio(SQLModel, table=True):
    __tablename__ = "colegio"

    id_colegio: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    zona: Optional[str] = Field(default=None)

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class CicloEbr(SQLModel, table=True):
    __tablename__ = "ciclo_ebr"

    id_ciclo: Optional[int] = Field(default=None, primary_key=True)
    nombre: str


class Grado(SQLModel, table=True):
    __tablename__ = "grado"

    id_grado: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    id_ciclo: int = Field(foreign_key="ciclo_ebr.id_ciclo")


class Programa(SQLModel, table=True):
    __tablename__ = "programa"

    id_programa: Optional[int] = Field(default=None, primary_key=True)
    nombre: str


class Docente(SQLModel, table=True):
    __tablename__ = "docente"

    id_docente: Optional[int] = Field(default=None, primary_key=True)
    nombres: str
    apellidos: str
    activo: bool = Field(default=True)

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class Rol(SQLModel, table=True):
    __tablename__ = "rol"

    id_rol: Optional[int] = Field(default=None, primary_key=True)
    nombre: str


class Usuario(SQLModel, table=True):
    __tablename__ = "usuario"

    id_usuario: Optional[int] = Field(default=None, primary_key=True)
    id_rol: int = Field(foreign_key="rol.id_rol")
    correo: str = Field(unique=True, index=True)
    password_hash: str
    id_docente: Optional[int] = Field(default=None, foreign_key="docente.id_docente")
    nombres: str
    apellidos: str
    activo: bool = Field(default=True)

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class Alumno(SQLModel, table=True):
    __tablename__ = "alumno"

    id_alumno: Optional[int] = Field(default=None, primary_key=True)
    nombres: str
    apellidos: str
    id_colegio: int = Field(foreign_key="colegio.id_colegio")
    id_grado: int = Field(foreign_key="grado.id_grado")
    id_programa_actual: int = Field(foreign_key="programa.id_programa")
    fecha_registro: date
    activo: bool = Field(default=True)

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class DocenteColegioGrado(SQLModel, table=True):
    __tablename__ = "docente_colegio_grado"

    id: Optional[int] = Field(default=None, primary_key=True)
    id_docente: int = Field(foreign_key="docente.id_docente")
    id_colegio: int = Field(foreign_key="colegio.id_colegio")
    id_grado: int = Field(foreign_key="grado.id_grado")
    id_periodo_academico: int = Field(foreign_key="periodo_academico.id_periodo_academico")

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)


class AlumnoProgramaHistorial(SQLModel, table=True):
    __tablename__ = "alumno_programa_historial"

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key="alumno.id_alumno")
    id_programa: int = Field(foreign_key="programa.id_programa")
    id_periodo_academico: int = Field(foreign_key="periodo_academico.id_periodo_academico")

    creado_por: int = Field(foreign_key="usuario.id_usuario")
    creado_en: date
    modificado_por: Optional[int] = Field(default=None, foreign_key="usuario.id_usuario")
    modificado_en: Optional[date] = Field(default=None)
