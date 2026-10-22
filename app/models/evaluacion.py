"""Grupos 2, 3 y 4 del diseño v4: catálogo de evaluación, clases, asistencia, lectura
semanal, rúbrica mensual y Registro de Vuelo.

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
from app.models.organizacion import COLUMNAS_GRUPO, FK_ANIO, FK_DOCENTE, FK_GRADO, FK_GRUPO, FK_PROGRAMA, FK_USUARIO
from app.services.reglas_provisionales import UNICA_TABLA_POR_CLASE_Y_SUBPROGRAMA

FK_ALUMNO = "alumno.id_alumno"
FK_NIVEL_RAZKIDS = "nivel_razkids.id_nivel_rk"
FK_NIVEL_RUBRICA = "nivel_rubrica.id_nivel_rubrica"
FK_PERIODO = "periodo_academico.id_periodo_academico"
FK_ACTIVIDAD = "actividad.id_actividad"
FK_PERMISO = "permiso_correccion.id_permiso"


# ── Grupo 2: catálogo del dominio de evaluación ─────────────────────────────────────


class NivelRazkids(SQLModel, table=True):
    """Niveles de Raz-Kids. Ids ascendentes = niveles ascendentes."""

    __tablename__ = "nivel_razkids"

    id_nivel_rk: Optional[int] = Field(default=None, primary_key=True)
    letra: str = Field(sa_type=Text)


class NivelRubrica(SQLModel, table=True):
    """Niveles de la rúbrica mensual, por programa y dimensión. `valor` es el código 0-4
    (0 Pre-inicio, 1 Inicio, 2 En proceso, 3 Logrado, 4 Avanzado) con que se evalúa y se
    calcula el resultado ponderado."""

    __tablename__ = "nivel_rubrica"
    __table_args__ = (
        CheckConstraint(
            "dimension IN ('Fluidez', 'Comprensión')", name="ck_nivel_rubrica_dimension"
        ),
        CheckConstraint("valor BETWEEN 0 AND 4", name="ck_nivel_rubrica_valor"),
        UniqueConstraint("id_programa", "dimension", "valor", name="uq_nivel_rubrica_programa_dimension_valor"),
    )

    id_nivel_rubrica: Optional[int] = Field(default=None, primary_key=True)
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
    dimension: str = Field(sa_type=Text)
    orden: int
    valor: int
    nombre_nivel: str = Field(sa_type=Text)


class DescriptorRubrica(SQLModel, table=True):
    """Descriptor pedagógico de cada nivel de la rúbrica por ciclo EBR (CU029). Catálogo
    puro, sin auditoría."""

    __tablename__ = "descriptor_rubrica"

    id_nivel_rubrica: int = Field(foreign_key=FK_NIVEL_RUBRICA, primary_key=True)
    id_ciclo: int = Field(foreign_key="ciclo_ebr.id_ciclo", primary_key=True)
    texto: str = Field(sa_type=Text)


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


# ── Grupo 3: semanas, clases, asistencia y lectura ──────────────────────────────────


class SemanaReporte(SQLModel, table=True):
    """Semanas de lunes a viernes de todo el año escolar (no dependen de los PA): una
    semana sin clase del grupo no tiene tabla de lectura ni obligación."""

    __tablename__ = "semana_reporte"

    id_semana: Optional[int] = Field(default=None, primary_key=True)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    numero_semana: int
    fecha_inicio: date
    # Viernes.
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
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key=FK_ACTIVIDAD)
    # Permiso de corrección (CU028) con que se registró; null si fue con una asignación.
    id_permiso: Optional[int] = Field(default=None, foreign_key=FK_PERMISO)
    # Momento real de la captura: con dos dispositivos, gana el más reciente.
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


# Estados de una tabla de lectura semanal (CU023, CU026).
LECTURA_NORMAL = "normal"
LECTURA_NO_REALIZADA = "no_realizada"
# Puntajes máximos posibles de un LSN.
MAXIMOS_LSN = (5, 10)


class LecturaSemanal(SQLModel, table=True):
    """Tabla de Seguimiento de Lectura de un grupo y subprograma en una semana (CU023 a
    CU026), atada a una clase de referencia del grupo en esa semana: la asistencia de esa
    clase decide qué filas admiten datos de lectura. El `mes` es el de la fecha de su
    clase de referencia."""

    __tablename__ = "lectura_semanal"
    __table_args__ = (
        ForeignKeyConstraint(COLUMNAS_GRUPO, FK_GRUPO, name="fk_lectura_semanal_seccion"),
        UniqueConstraint(
            "id_colegio", "id_grado", "id_seccion", "id_programa", "id_anio_escolar", "mes", "id_semana",
            name="uq_lectura_semanal_grupo_semana",
        ),
        UniqueConstraint(*UNICA_TABLA_POR_CLASE_Y_SUBPROGRAMA, name="uq_lectura_semanal_clase_programa"),
        CheckConstraint("mes BETWEEN 1 AND 12", name="ck_lectura_semanal_mes"),
        CheckConstraint("estado IN ('normal', 'no_realizada')", name="ck_lectura_semanal_estado"),
        CheckConstraint(
            "estado <> 'no_realizada' OR length(trim(coalesce(motivo_no_realizada, ''))) > 0",
            name="ck_lectura_semanal_motivo",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    mes: int
    id_semana: int = Field(foreign_key="semana_reporte.id_semana")
    id_clase_referencia: int = Field(foreign_key=FK_CLASE)
    estado: str = Field(default=LECTURA_NORMAL, sa_type=Text, sa_column_kwargs={"server_default": LECTURA_NORMAL})
    motivo_no_realizada: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key=FK_ACTIVIDAD)
    id_permiso: Optional[int] = Field(default=None, foreign_key=FK_PERMISO)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class LecturaSemanalAlumno(SQLModel, table=True):
    """Fila de un alumno de la lista de la clase de referencia (CU024). LSN: uno por
    semana, todo o nada (título + nota + máximo 5 o 10). LSL: cantidad entera; null = no
    registrado (≠ 0). El total de libros y el estado de la fila se calculan."""

    __tablename__ = "lectura_semanal_alumno"
    __table_args__ = (
        UniqueConstraint("id_lectura_semanal", "id_alumno", name="uq_lectura_semanal_alumno_tabla_alumno"),
        CheckConstraint(
            "(lsn_titulo IS NULL AND lsn_nota IS NULL AND lsn_maximo IS NULL) OR "
            "(length(trim(lsn_titulo)) > 0 AND lsn_nota IS NOT NULL AND lsn_maximo IS NOT NULL)",
            name="ck_lectura_semanal_alumno_lsn_completo",
        ),
        CheckConstraint("lsn_maximo IS NULL OR lsn_maximo IN (5, 10)", name="ck_lectura_semanal_alumno_lsn_maximo"),
        CheckConstraint(
            "lsn_nota IS NULL OR (lsn_nota >= 0 AND lsn_nota <= lsn_maximo)", name="ck_lectura_semanal_alumno_lsn_nota"
        ),
        CheckConstraint("cantidad_lsl IS NULL OR cantidad_lsl >= 0", name="ck_lectura_semanal_alumno_cantidad_lsl"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_lectura_semanal: int = Field(foreign_key="lectura_semanal.id")
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    lsn_titulo: Optional[str] = Field(default=None, sa_type=Text)
    lsn_nota: Optional[int] = None
    lsn_maximo: Optional[int] = None
    cantidad_lsl: Optional[int] = None
    observacion: Optional[str] = Field(default=None, sa_type=Text)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key=FK_ACTIVIDAD)
    id_permiso: Optional[int] = Field(default=None, foreign_key=FK_PERMISO)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


# ── Grupo 4: rúbrica mensual y Registro de Vuelo ────────────────────────────────────


class RubricaMensual(SQLModel, table=True):
    """Evaluación mensual de un alumno en su grupo de ese mes (CU029, CU030): Fluidez y
    Comprensión (null = "Sin evaluar"; se puede guardar incompleta). `id_periodo_academico`
    es el PA del mes (REGLA PROVISIONAL R1: el de su último día lectivo)."""

    __tablename__ = "rubrica_mensual"
    __table_args__ = (
        ForeignKeyConstraint(COLUMNAS_GRUPO, FK_GRUPO, name="fk_rubrica_mensual_seccion"),
        UniqueConstraint(
            "id_alumno", "id_anio_escolar", "mes", "id_colegio", "id_grado", "id_seccion", "id_programa",
            name="uq_rubrica_mensual_alumno_mes_grupo",
        ),
        CheckConstraint("mes BETWEEN 1 AND 12", name="ck_rubrica_mensual_mes"),
        Index("ix_rubrica_mensual_colegio_periodo", "id_colegio", "id_periodo_academico"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    mes: int
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    id_nivel_fluidez: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RUBRICA)
    id_nivel_comprension: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RUBRICA)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key=FK_ACTIVIDAD)
    id_permiso: Optional[int] = Field(default=None, foreign_key=FK_PERMISO)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class RegistroVuelo(SQLModel, table=True):
    """Decisión del Docente sobre el nivel Raz-Kids del alumno en un PA (CU031, CU032), en
    el grupo en que pertenecía. La entrada, la sugerencia y el nivel del programa se
    calculan; al registrar el nivel colocado se guarda con qué se decidió (rúbrica de
    referencia y su versión, resultado y entrada vigente)."""

    __tablename__ = "registro_vuelo"
    __table_args__ = (
        ForeignKeyConstraint(COLUMNAS_GRUPO, FK_GRUPO, name="fk_registro_vuelo_seccion"),
        UniqueConstraint(
            "id_alumno", "id_anio_escolar", "id_periodo_academico", "id_colegio", "id_grado", "id_seccion",
            "id_programa", name="uq_registro_vuelo_alumno_periodo_grupo",
        ),
        CheckConstraint("resultado_decimas BETWEEN 0 AND 40", name="ck_registro_vuelo_resultado"),
        Index("ix_registro_vuelo_alumno_periodo", "id_alumno", "id_periodo_academico"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    id_alumno: int = Field(foreign_key=FK_ALUMNO)
    id_anio_escolar: int = Field(foreign_key=FK_ANIO)
    id_periodo_academico: int = Field(foreign_key=FK_PERIODO)
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int = Field(foreign_key=FK_PROGRAMA)
    # null = "Sin registrar".
    id_nivel_colocado: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RAZKIDS)
    # null = "Sin proyección".
    id_nivel_proyeccion: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RAZKIDS)
    observacion: Optional[str] = Field(default=None, sa_type=Text)
    id_rubrica_referencia: Optional[int] = Field(default=None, foreign_key="rubrica_mensual.id")
    rubrica_version: Optional[int] = None
    resultado_decimas: Optional[int] = None
    id_nivel_entrada_al_decidir: Optional[int] = Field(default=None, foreign_key=FK_NIVEL_RAZKIDS)
    id_docente: int = Field(foreign_key=FK_DOCENTE)
    id_actividad: Optional[uuid.UUID] = Field(default=None, sa_type=Uuid, foreign_key=FK_ACTIVIDAD)
    id_permiso: Optional[int] = Field(default=None, foreign_key=FK_PERMISO)
    fecha_registro: datetime = Field(sa_type=UTCDateTime)
    version: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    creado_por: int = Field(foreign_key=FK_USUARIO)
    creado_en: datetime = Field(sa_type=UTCDateTime)
    modificado_por: Optional[int] = Field(default=None, foreign_key=FK_USUARIO)
    modificado_en: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
