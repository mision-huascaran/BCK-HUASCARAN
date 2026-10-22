"""Grupos 2, 3 y 4 del diseño v3: catálogo de evaluación, registro semanal y rúbrica.

Los catálogos del Grupo 2 (salvo `evaluacion_diagnostica`) no llevan auditoría
(diseño v3, §1.2). Las tablas de captura guardan en `fecha_registro` el momento real de
captura enviado por el cliente, y en `creado_en` cuándo la guardó el servidor (§1.3).
"""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Text, UniqueConstraint, Uuid, text
from sqlmodel import Field, SQLModel

from app.core.tiempo import UTCDateTime
from app.models.organizacion import COLUMNAS_GRUPO, FK_ANIO, FK_GRADO, FK_GRUPO, FK_PROGRAMA, FK_USUARIO

FK_ALUMNO = "alumno.id_alumno"
FK_DOCENTE = "docente.id_docente"
FK_NIVEL_RAZKIDS = "nivel_razkids.id_nivel_rk"
FK_NIVEL_RUBRICA = "nivel_rubrica.id_nivel_rubrica"
FK_NIVEL_GENERAL = "nivel_general.id_nivel_general"
FK_PERIODO = "periodo_academico.id_periodo_academico"

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
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
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
        foreign_key=FK_GRADO, primary_key=True, sa_column_kwargs={"autoincrement": False}
    )
    id_nivel_rk_esperado: int = Field(foreign_key=FK_NIVEL_RAZKIDS)


# Niveles descriptivos del Registro de Vuelo (CU015), de menor a mayor.
NIVELES_DESCRIPTIVOS = ("Inicio", "En proceso", "Logrado", "Avanzado")


class EscalaNivelDescriptivo(SQLModel, table=True):
    """Escala del nivel descriptivo por grado (CU015): desde qué letra Raz-Kids empieza
    cada nivel. Una letra cae en el nivel de mayor `id_nivel_rk_desde` que no la supere;
    el nivel más bajo empieza en la primera letra, así que toda letra tiene nivel."""

    __tablename__ = "escala_nivel_descriptivo"
    __table_args__ = (
        UniqueConstraint("id_grado", "id_nivel_rk_desde", name="uq_escala_nivel_descriptivo_grado_desde"),
        CheckConstraint(
            "nivel IN ('Inicio', 'En proceso', 'Logrado', 'Avanzado')", name="ck_escala_nivel_descriptivo_nivel"
        ),
    )

    id_grado: int = Field(
        foreign_key=FK_GRADO, primary_key=True, sa_column_kwargs={"autoincrement": False}
    )
    nivel: str = Field(sa_type=Text, primary_key=True)
    id_nivel_rk_desde: int = Field(foreign_key=FK_NIVEL_RAZKIDS)


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


# Valores de `asistencia.estado` (CU022); null = sin marcar.
ESTADOS_ASISTENCIA = ("presente", "tardanza", "ausente")
# Los que cuentan como asistencia en el % del alumno (CU015).
ESTADOS_ASISTIO = ("presente", "tardanza")


TIPO_HABITUAL = "habitual"
TIPO_ADICIONAL = "adicional"
TIPOS_CLASE = (TIPO_HABITUAL, TIPO_ADICIONAL)
FK_CLASE = "clase.id_clase"


class Clase(SQLModel, table=True):
    """Una clase de un grupo (colegio × grado × sección) en una fecha (CU013, CU022).

    Habitual: cada fecha lectiva de un PA en que cae el `dia_clase` del grado; se
    calcula y solo tiene fila cuando hace falta (primera asistencia, reprogramación o
    tabla de lectura). Reprogramada: una habitual con `fecha` distinta de su
    `fecha_original` (la obligación pasa a `fecha`). Adicional: registrada por el Docente
    en una fecha lectiva sin clase programada (`fecha_original = fecha`).
    El subprograma es del alumno, no de la clase.
    """

    __tablename__ = "clase"
    __table_args__ = (
        ForeignKeyConstraint(COLUMNAS_GRUPO, FK_GRUPO, name="fk_clase_seccion"),
        CheckConstraint("tipo IN ('habitual', 'adicional')", name="ck_clase_tipo"),
        UniqueConstraint("id_colegio", "id_grado", "id_seccion", "fecha", name="uq_clase_grupo_fecha"),
        # Una habitual por fecha programada, aunque se haya trasladado.
        Index(
            "uq_clase_habitual_fecha_original", "id_colegio", "id_grado", "id_seccion", "fecha_original",
            unique=True, postgresql_where=text("tipo = 'habitual'"), sqlite_where=text("tipo = 'habitual'"),
        ),
        Index("ix_clase_colegio_fecha", "id_colegio", "fecha"),
    )

    id_clase: Optional[int] = Field(default=None, primary_key=True)
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    tipo: str = Field(sa_type=Text)
    fecha_original: date
    # La vigente (después de reprogramar).
    fecha: date
    # Cuándo se congeló la lista de alumnos (primera asistencia); null = todavía no.
    lista_congelada_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class ClaseAlumno(SQLModel, table=True):
    """Lista histórica de una clase: los alumnos que pertenecían al grupo en su fecha,
    congelada al guardar la primera asistencia. No cambia con la pertenencia posterior."""

    __tablename__ = "clase_alumno"

    id_clase: int = Field(foreign_key=FK_CLASE, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO, primary_key=True)
    # Subprograma del alumno en la fecha de la clase.
    id_programa: int = Field(foreign_key=FK_PROGRAMA)


class ClaseReprogramacion(SQLModel, table=True):
    """Historial de traslados de una clase (CU013)."""

    __tablename__ = "clase_reprogramacion"
    __table_args__ = (
        CheckConstraint("length(trim(motivo)) > 0", name="ck_clase_reprogramacion_motivo"),
        Index("ix_clase_reprogramacion_clase", "id_clase", "fecha"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_clase: int = Field(foreign_key=FK_CLASE)
    fecha_anterior: date
    fecha_nueva: date
    motivo: str = Field(sa_type=Text)
    id_usuario: int = Field(foreign_key=FK_USUARIO)
    fecha: datetime = Field(sa_type=UTCDateTime)


class Asistencia(SQLModel, table=True):
    """Asistencia de un alumno de la lista de una clase (CU022). Fuente única de
    asistencia para la rúbrica y el seguimiento de lectura. `estado` null = sin marcar
    (una observación sola no completa la fila)."""

    __tablename__ = "asistencia"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id_clase", "id_alumno"], ["clase_alumno.id_clase", "clase_alumno.id_alumno"],
            name="fk_asistencia_clase_alumno",
        ),
        UniqueConstraint("id_clase", "id_alumno", name="uq_asistencia_clase_alumno"),
        CheckConstraint(
            "estado IS NULL OR estado IN ('presente', 'tardanza', 'ausente')", name="ck_asistencia_estado"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_clase: int
    id_alumno: int
    estado: Optional[str] = Field(default=None, sa_type=Text)
    observacion: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key="actividad.id_actividad")
    # Permiso de corrección (CU028) con que se registró; null si fue con una asignación.
    id_permiso: Optional[int] = Field(default=None, foreign_key="permiso_correccion.id_permiso")
    # Momento real de la captura: con dos dispositivos, gana el más reciente.
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

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
    """Rúbrica semanal. Sin asistencia (vive en `asistencia`); niveles nullable."""

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
