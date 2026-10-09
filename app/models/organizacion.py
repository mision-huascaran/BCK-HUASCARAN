from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Index, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime

FK_USUARIO = "usuario.id_usuario"
FK_COLEGIO = "colegio.id_colegio"
FK_GRADO = "grado.id_grado"
FK_PROGRAMA = "programa.id_programa"


class AnioEscolar(SQLModel, table=True):
    """Antes `PeriodoAcademico` (v1). Ver diseno_bd_sicedu_v2.md §1.2 sobre el swap de nombres."""

    __tablename__ = "año_escolar"

    id_anio_escolar: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    fecha_inicio: date
    fecha_fin: date

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class PeriodoAcademico(SQLModel, table=True):
    """Antes `PeriodoEvaluacion` (v1). Ver diseno_bd_sicedu_v2.md §1.2 sobre el swap de nombres."""

    __tablename__ = "periodo_academico"

    id_periodo_academico: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key="año_escolar.id_anio_escolar")
    numero: int
    fecha_inicio: date
    fecha_fin: date

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class Colegio(SQLModel, table=True):
    __tablename__ = "colegio"

    id_colegio: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    nivel_educativo: str = Field(
        default="Primaria", sa_type=Text, sa_column_kwargs={"server_default": "Primaria"}
    )
    departamento: str = Field(sa_type=Text)
    provincia: Optional[str] = Field(default=None)
    distrito: Optional[str] = Field(default=None, sa_type=Text)
    seccion: str = Field(default="Única", sa_type=Text, sa_column_kwargs={"server_default": "Única"})
    activo: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


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

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class Rol(SQLModel, table=True):
    __tablename__ = "rol"

    id_rol: Optional[int] = Field(default=None, primary_key=True)
    nombre: str


class Usuario(SQLModel, table=True):
    __tablename__ = "usuario"
    __table_args__ = (
        UniqueConstraint("dni", name="uq_usuario_dni"),
        UniqueConstraint("id_docente", name="uq_usuario_id_docente"),
        # El operador ~ es de PostgreSQL: en SQLite (los tests) el CHECK no se emite.
        CheckConstraint("dni ~ '^[0-9]{8}$'", name="ck_usuario_dni_formato").ddl_if(
            dialect="postgresql"
        ),
        # Correo siempre normalizado (minúsculas, sin espacios alrededor): las búsquedas por
        # correo usan igualdad exacta y el índice. Solo PostgreSQL, como el CHECK del DNI.
        CheckConstraint(
            "correo = lower(btrim(correo))", name="ck_usuario_correo_normalizado"
        ).ddl_if(dialect="postgresql"),
        # Solo una fila puede ser el Supervisor original.
        Index(
            "uq_usuario_supervisor_original",
            "es_supervisor_original",
            unique=True,
            postgresql_where=text("es_supervisor_original"),
            sqlite_where=text("es_supervisor_original"),
        ),
    )

    id_usuario: Optional[int] = Field(default=None, primary_key=True)
    id_rol: int = Field(foreign_key="rol.id_rol")
    correo: str = Field(unique=True, index=True)
    password_hash: str
    id_docente: Optional[int] = Field(default=None, foreign_key="docente.id_docente")
    # Texto y no número: un DNI puede empezar con 0.
    dni: Optional[str] = Field(default=None, max_length=8)
    nombres: str
    apellidos: str
    activo: bool = Field(default=True)
    es_supervisor_original: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("false")}
    )

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)

    # HMAC-SHA256 del PIN en hexadecimal (ver app/core/pin.py); null si no hay PIN vigente.
    codigo_verificacion: Optional[str] = Field(default=None, max_length=64)
    codigo_verificacion_expira: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    codigo_verificacion_intentos: int = Field(
        default=0, sa_column_kwargs={"server_default": text("0")}
    )


class Alumno(SQLModel, table=True):
    __tablename__ = "alumno"

    id_alumno: Optional[int] = Field(default=None, primary_key=True)
    nombres: str
    apellidos: str
    id_colegio: int = Field(foreign_key=FK_COLEGIO)
    id_grado: int = Field(foreign_key=FK_GRADO)
    id_programa_actual: int = Field(foreign_key=FK_PROGRAMA)
    fecha_registro: date
    activo: bool = Field(default=True)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class DocenteColegioGrado(SQLModel, table=True):
    __tablename__ = "docente_colegio_grado"
    __table_args__ = (
        # Un solo docente por colegio, grado y periodo.
        UniqueConstraint(
            "id_colegio", "id_grado", "id_periodo_academico",
            name="uq_docente_colegio_grado_periodo",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_docente: int = Field(foreign_key="docente.id_docente")
    id_colegio: int = Field(foreign_key=FK_COLEGIO)
    id_grado: int = Field(foreign_key=FK_GRADO)
    id_periodo_academico: int = Field(foreign_key="periodo_academico.id_periodo_academico")

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class AlumnoProgramaHistorial(SQLModel, table=True):
    """Subprograma del alumno en cada periodo: una sola fila por alumno y periodo."""

    __tablename__ = "alumno_programa_historial"
    __table_args__ = (
        UniqueConstraint(
            "id_alumno", "id_periodo_academico", name="uq_alumno_programa_historial_periodo"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key="alumno.id_alumno")
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
    id_periodo_academico: int = Field(foreign_key="periodo_academico.id_periodo_academico")

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class ColegioGrado(SQLModel, table=True):
    """Grados que ofrece cada colegio (CU013)."""

    __tablename__ = "colegio_grado"

    id_colegio: int = Field(foreign_key=FK_COLEGIO, primary_key=True)
    id_grado: int = Field(foreign_key=FK_GRADO, primary_key=True)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class ColegioPrograma(SQLModel, table=True):
    """Subprogramas que ofrece cada colegio (CU013)."""

    __tablename__ = "colegio_programa"

    id_colegio: int = Field(foreign_key=FK_COLEGIO, primary_key=True)
    id_programa: int = Field(foreign_key=FK_PROGRAMA, primary_key=True)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class DiaNoLaborable(SQLModel, table=True):
    """Días que no cuentan como hábiles para las alertas de inactividad (CU011).

    Sábados y domingos no se registran: se excluyen por cálculo.
    """

    __tablename__ = "dia_no_laborable"

    fecha: date = Field(primary_key=True)
    motivo: str = Field(sa_type=Text)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
