from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Index, SmallInteger, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime

FK_USUARIO = "usuario.id_usuario"
FK_COLEGIO = "colegio.id_colegio"
FK_GRADO = "grado.id_grado"
FK_PROGRAMA = "programa.id_programa"
FK_ANIO = "año_escolar.id_anio_escolar"
FK_CALENDARIO = "calendario.id_calendario"

# Calendario académico (CU027): PA = periodo académico, PR = receso posterior al PA n.
TIPO_PA = "PA"
TIPO_PR = "PR"
ORIGEN_AUTOMATICO = "automatico"
ORIGEN_MANUAL = "manual"


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
    """Identidad "PA n del año X" (diseño v4 §3). Sus fechas dependen del colegio: salen
    del calendario que le aplica (`calendario_periodo`, ver app/services/calendario.py)."""

    __tablename__ = "periodo_academico"
    __table_args__ = (
        UniqueConstraint("id_anio_escolar", "numero", name="uq_periodo_academico_anio_numero"),
    )

    id_periodo_academico: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    numero: int

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
    # Obligatoria (CU013); 'Por definir' en los colegios que no la tenían.
    provincia: str
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

    # Cuenta creada con contraseña temporal: debe cambiarla en su primer ingreso (CU001,
    # CU016). La temporal sirve hasta `contrasena_temporal_expira` (null sin marca).
    debe_cambiar_contrasena: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("false")}
    )
    contrasena_temporal_expira: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class Alumno(SQLModel, table=True):
    __tablename__ = "alumno"
    __table_args__ = (
        # Alumnos de un aula (asistencia, vencidos e incompletos de CU011).
        Index("ix_alumno_colegio_grado", "id_colegio", "id_grado"),
    )

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
    """Grados que ofrece cada colegio (CU013), con su día de clase semanal."""

    __tablename__ = "colegio_grado"
    __table_args__ = (
        CheckConstraint("dia_clase BETWEEN 1 AND 5", name="ck_colegio_grado_dia_clase"),
    )

    id_colegio: int = Field(foreign_key=FK_COLEGIO, primary_key=True)
    id_grado: int = Field(foreign_key=FK_GRADO, primary_key=True)
    # 1 = lunes … 5 = viernes. Null en los colegios cargados antes de pedirse (no vencen).
    dia_clase: Optional[int] = Field(default=None, sa_type=SmallInteger)

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


class Calendario(SQLModel, table=True):
    """Calendario académico de un año escolar (CU027): el general (`id_colegio` NULL) o el
    particular de un colegio. "Usar calendario general" desactiva el particular sin
    borrarlo, para conservar su historial."""

    __tablename__ = "calendario"
    __table_args__ = (
        # Un solo general por año.
        Index(
            "uq_calendario_general_por_anio",
            "id_anio_escolar",
            unique=True,
            postgresql_where=text("id_colegio IS NULL"),
            sqlite_where=text("id_colegio IS NULL"),
        ),
        # Como máximo un particular activo por colegio y año.
        Index(
            "uq_calendario_particular_activo",
            "id_anio_escolar",
            "id_colegio",
            unique=True,
            postgresql_where=text("id_colegio IS NOT NULL AND activo"),
            sqlite_where=text("id_colegio IS NOT NULL AND activo"),
        ),
        # El general no se desactiva: solo un particular deja de usarse.
        CheckConstraint("id_colegio IS NOT NULL OR activo", name="ck_calendario_general_activo"),
    )

    id_calendario: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    id_colegio: Optional[int] = Field(default=None, foreign_key=FK_COLEGIO)
    activo: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    # Control de concurrencia entre Supervisores: sube en cada cambio (409 si cambió).
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class CalendarioPeriodo(SQLModel, table=True):
    """Las 8 filas de un calendario: PA1, PR1, ..., PA4, PR4 (fechas inclusivas). Los
    recesos son filas explícitas; PR n es el receso posterior a PA n."""

    __tablename__ = "calendario_periodo"
    __table_args__ = (
        UniqueConstraint("id_calendario", "tipo", "numero", name="uq_calendario_periodo_tipo_numero"),
        CheckConstraint(f"tipo IN ('{TIPO_PA}', '{TIPO_PR}')", name="ck_calendario_periodo_tipo"),
        CheckConstraint("numero BETWEEN 1 AND 4", name="ck_calendario_periodo_numero"),
        CheckConstraint("fecha_fin >= fecha_inicio", name="ck_calendario_periodo_fechas"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_calendario: int = Field(foreign_key=FK_CALENDARIO)
    tipo: str = Field(sa_type=Text)
    numero: int = Field(sa_type=SmallInteger)
    fecha_inicio: date
    fecha_fin: date


class DiaNoLectivo(SQLModel, table=True):
    """Feriado o suspensión de un calendario (CU027): rango inclusivo dentro de un PA, sin
    superponerse con otro del mismo calendario (validado en el servicio). Reemplaza a
    `dia_no_laborable`, que era global."""

    __tablename__ = "dia_no_lectivo"
    __table_args__ = (
        CheckConstraint(
            f"origen IN ('{ORIGEN_AUTOMATICO}', '{ORIGEN_MANUAL}')", name="ck_dia_no_lectivo_origen"
        ),
        CheckConstraint("fecha_fin >= fecha_inicio", name="ck_dia_no_lectivo_fechas"),
        CheckConstraint("length(trim(motivo)) > 0", name="ck_dia_no_lectivo_motivo"),
        Index("ix_dia_no_lectivo_calendario_inicio", "id_calendario", "fecha_inicio"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_calendario: int = Field(foreign_key=FK_CALENDARIO)
    motivo: str = Field(sa_type=Text)
    fecha_inicio: date
    fecha_fin: date
    # 'automatico' = cargado por `cargar-feriados`; 'manual' = registrado por un Supervisor.
    origen: str = Field(sa_type=Text)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
