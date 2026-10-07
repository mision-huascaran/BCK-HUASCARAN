"""Grupos 2, 3 y 4 del diseño v3: catálogo de evaluación, registro semanal y rúbrica.

Los catálogos del Grupo 2 (salvo `evaluacion_diagnostica`) no llevan auditoría
(diseño v3, §1.2). Las tablas de captura guardan en `fecha_registro` el momento real de
captura enviado por el cliente, y en `creado_en` cuándo la guardó el servidor (§1.3).
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime
from app.models.organizacion import FK_USUARIO

FK_ALUMNO = "alumno.id_alumno"
FK_DOCENTE = "docente.id_docente"
FK_NIVEL_RAZKIDS = "nivel_razkids.id_nivel_rk"
FK_NIVEL_RUBRICA = "nivel_rubrica.id_nivel_rubrica"
FK_NIVEL_GENERAL = "nivel_general.id_nivel_general"
FK_PERIODO = "periodo_academico.id_periodo_academico"
FK_ANIO = "año_escolar.id_anio_escolar"

# Columnas de semana_reporte a las que apuntan las FK compuestas de las tablas semanales.
SEMANA_PERIODO_ANIO = ("id_semana", "id_periodo_academico", "id_anio_escolar")
REF_SEMANA_PERIODO_ANIO = tuple(f"semana_reporte.{c}" for c in SEMANA_PERIODO_ANIO)


# ── Grupo 2: catálogo del dominio de evaluación ─────────────────────────────────────


class NivelRazkids(SQLModel, table=True):
    """Niveles de Raz-Kids. Ids ascendentes = niveles ascendentes."""

    __tablename__ = "nivel_razkids"

    id_nivel_rk: Optional[int] = Field(default=None, primary_key=True)
    letra: str = Field(sa_type=Text)


class NivelRubrica(SQLModel, table=True):
    """Niveles de la rúbrica semanal, por programa y dimensión."""

    __tablename__ = "nivel_rubrica"
    __table_args__ = (
        CheckConstraint(
            "dimension IN ('Fluidez', 'Comprensión')", name="ck_nivel_rubrica_dimension"
        ),
    )

    id_nivel_rubrica: Optional[int] = Field(default=None, primary_key=True)
    id_programa: int = Field(foreign_key="programa.id_programa")
    dimension: str = Field(sa_type=Text)
    orden: int
    nombre_nivel: str = Field(sa_type=Text)


class NivelGeneral(SQLModel, table=True):
    __tablename__ = "nivel_general"

    id_nivel_general: Optional[int] = Field(default=None, primary_key=True)
    orden: int
    nombre_nivel: str = Field(sa_type=Text)


class NivelEsperadoPorGrado(SQLModel, table=True):
    __tablename__ = "nivel_esperado_por_grado"

    id_grado: int = Field(
        foreign_key="grado.id_grado", primary_key=True, sa_column_kwargs={"autoincrement": False}
    )
    id_nivel_rk_esperado: int = Field(foreign_key=FK_NIVEL_RAZKIDS)


class EvaluacionDiagnostica(SQLModel, table=True):
    """Cortes del Registro de Vuelo, uno por periodo académico."""

    __tablename__ = "evaluacion_diagnostica"
    __table_args__ = (
        UniqueConstraint("id_periodo_academico", name="uq_evaluacion_diagnostica_periodo"),
    )

    id_evaluacion_diagnostica: Optional[int] = Field(default=None, primary_key=True)
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    fecha_realizacion: date

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


# ── Grupo 3: registro semanal y diagnóstico del alumno ──────────────────────────────


class SemanaReporte(SQLModel, table=True):
    __tablename__ = "semana_reporte"
    __table_args__ = (
        # Habilita la FK compuesta desde las tablas semanales.
        UniqueConstraint(*SEMANA_PERIODO_ANIO, name="uq_semana_reporte_semana_periodo_anio"),
    )

    id_semana: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    numero_semana: int
    fecha_inicio: date
    # Fecha de cumplimiento para los registros pendientes (CU011).
    fecha_fin: date

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class AsistenciaSemanal(SQLModel, table=True):
    """Fuente única de asistencia para la rúbrica y el seguimiento de lectura."""

    __tablename__ = "asistencia_semanal"
    __table_args__ = (
        UniqueConstraint("id_alumno", "id_semana", name="uq_asistencia_semanal_alumno_semana"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_semana: int = Field(foreign_key="semana_reporte.id_semana")
    asistio: bool
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class EvaluacionDiagnosticaAlumno(SQLModel, table=True):
    """Registro de Vuelo. Los campos de resultado admiten null (registro incompleto)."""

    __tablename__ = "evaluacion_diagnostica_alumno"
    __table_args__ = (
        UniqueConstraint(
            "id_alumno",
            "id_evaluacion_diagnostica",
            name="uq_evaluacion_diagnostica_alumno_alumno_evaluacion",
        ),
        CheckConstraint(
            "aciertos_prueba IS NULL OR total_prueba IS NULL OR aciertos_prueba <= total_prueba",
            name="ck_evaluacion_diagnostica_alumno_aciertos",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_evaluacion_diagnostica: int = Field(
        foreign_key="evaluacion_diagnostica.id_evaluacion_diagnostica"
    )
    # Puede diferir del ciclo calculado del alumno.
    id_ciclo_evaluado: int = Field(foreign_key="ciclo_ebr.id_ciclo")
    id_nivel_rk_entrada: int = Field(foreign_key=FK_NIVEL_RAZKIDS)
    aciertos_prueba: Optional[int] = Field(default=None)
    total_prueba: Optional[int] = Field(default=None)
    # Decisión del docente.
    nivel_colocado: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RAZKIDS)
    id_nivel_general: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_GENERAL)
    observacion: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class ReporteSemanalAlumno(SQLModel, table=True):
    """Seguimiento de Lectura."""

    __tablename__ = "reporte_semanal_alumno"
    __table_args__ = (
        UniqueConstraint("id_alumno", "id_semana", name="uq_reporte_semanal_alumno_alumno_semana"),
        # Impide guardar una semana con un periodo o año que no le corresponde.
        ForeignKeyConstraint(
            SEMANA_PERIODO_ANIO,
            REF_SEMANA_PERIODO_ANIO,
            name="fk_reporte_semanal_alumno_semana_periodo_anio",
        ),
        CheckConstraint(
            "cantidad_lsl IS NULL OR cantidad_lsl >= 0",
            name="ck_reporte_semanal_alumno_cantidad_lsl",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_semana: int
    id_anio_escolar: int
    id_periodo_academico: int
    # Libros de sala de lectura.
    cantidad_lsl: Optional[int] = Field(default=None)
    observaciones: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class LibroSubidaNivel(SQLModel, table=True):
    """Libros de subir de nivel (LSB) de un reporte semanal."""

    __tablename__ = "libro_subida_nivel"
    __table_args__ = (
        CheckConstraint("aciertos <= total_preguntas", name="ck_libro_subida_nivel_aciertos"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_reporte_semanal: int = Field(foreign_key="reporte_semanal_alumno.id")
    titulo_libro: str = Field(sa_type=Text)
    aciertos: int
    total_preguntas: int

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


# ── Grupo 4: rúbrica semanal y nivel final ──────────────────────────────────────────


class RubricaRegistroSemanal(SQLModel, table=True):
    """Rúbrica semanal. Sin asistencia (vive en asistencia_semanal); niveles nullable."""

    __tablename__ = "rubrica_registro_semanal"
    __table_args__ = (
        UniqueConstraint("id_alumno", "id_semana", name="uq_rubrica_registro_semanal_alumno_semana"),
        ForeignKeyConstraint(
            SEMANA_PERIODO_ANIO,
            REF_SEMANA_PERIODO_ANIO,
            name="fk_rubrica_registro_semanal_semana_periodo_anio",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_semana: int
    id_anio_escolar: int
    id_periodo_academico: int
    id_nivel_fluidez: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RUBRICA)
    id_nivel_comprension: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RUBRICA)
    observacion: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class NivelFinalMensual(SQLModel, table=True):
    """Nivel vigente del alumno cada mes; fuente de "Nivel actual" (CU015)."""

    __tablename__ = "nivel_final_mensual"
    __table_args__ = (
        UniqueConstraint(
            "id_alumno", "id_anio_escolar", "mes", name="uq_nivel_final_mensual_alumno_anio_mes"
        ),
        CheckConstraint("mes BETWEEN 1 AND 12", name="ck_nivel_final_mensual_mes"),
        CheckConstraint(
            "ajustado_por_docente = false OR justificacion IS NOT NULL",
            name="ck_nivel_final_mensual_justificacion",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    mes: int
    id_nivel_final: int = Field(foreign_key=FK_NIVEL_GENERAL)
    ajustado_por_docente: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("false")}
    )
    id_docente_ajuste: Optional[int] = Field(default=None, foreign_key=FK_DOCENTE)
    justificacion: Optional[str] = Field(default=None, sa_type=Text)
    fecha_calculo: datetime = Field(sa_type=UTCDateTime)
    fecha_ajuste: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
