"""Registro de Vuelo (CU031, CU032).

Por alumno y PA: la entrada, la sugerencia y el nivel del programa los calcula el
backend; el Docente registra el nivel colocado, la proyección y la observación. Las
escrituras son operaciones (`id_operacion`, `version`, `realizado_en`): ver
app/schemas/sincronizacion.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.paginacion import Paginado
from app.schemas.asistencia import Escritura
from app.schemas.comun import ParcheSinNulos
from app.schemas.sincronizacion import ItemOperacion

OrigenEntrada = Literal["grado", "periodo_anterior", "pendiente_periodo_anterior"]
EstadoPeriodo = Literal["futuro", "en_curso", "culminado"]
MAX_REGISTROS_POR_LOTE = 200


class Letra(BaseModel):
    id: int
    letra: str


class Entrada(BaseModel):
    # null = "Pendiente del periodo anterior".
    nivel: Optional[Letra] = None
    origen: OrigenEntrada
    # Ayuda o texto para mostrar (con " Registrado antes del traslado." si viene de otro colegio).
    texto: str
    # La decisión que da la entrada se registró en otro colegio (traslado). Solo se expone
    # la letra: ver el antecedente no da acceso a esa decisión.
    de_otro_colegio: bool = False


class RubricaReferencia(BaseModel):
    """La evaluación mensual con que se calculó la sugerencia ("Ver rúbrica")."""

    id: int
    # "AAAA-MM".
    mes: str
    fluidez: str
    comprension: str
    resultado_decimas: int


class SugerenciaVuelo(BaseModel):
    accion: Optional[Literal["bajar", "mantener", "subir"]] = None
    # La letra a la que llevaría la acción desde la entrada; null sin acción.
    nivel: Optional[Letra] = None
    texto: str
    resultado_decimas: Optional[int] = None
    excepcion: Optional[Literal["aa", "Z"]] = None
    rubrica: Optional[RubricaReferencia] = None


class PeriodoAlumno(BaseModel):
    numero: int
    id_periodo_academico: int
    # null si todavía no tiene fila.
    id_registro: Optional[int] = None
    version: Optional[int] = None
    entrada: Entrada
    sugerencia: SugerenciaVuelo
    # null = "Sin registrar".
    nivel_colocado: Optional[Letra] = None
    # Inicio, En proceso, Logrado o Avanzado según el grado y el nivel colocado.
    nivel_programa: Optional[str] = None
    # null = "Sin proyección".
    proyeccion: Optional[Letra] = None
    observacion: Optional[str] = None
    aviso_entrada_cambio: bool = False
    aviso: Optional[str] = None
    # Falta el nivel colocado (CU011).
    incompleto: bool


class AlumnoVuelo(BaseModel):
    id_alumno: int
    nombre: str
    id_programa: int
    inactivo: bool
    # 25 por cada PA con nivel colocado.
    avance_anual: int
    periodos: list[PeriodoAlumno]


class PeriodoDelAnio(BaseModel):
    numero: int
    id_periodo_academico: int
    nombre: str
    # null si el calendario del colegio no tiene ese PA.
    fecha_inicio: Optional[str] = None
    fecha_fin: Optional[str] = None
    estado: Optional[EstadoPeriodo] = None
    escritura: Escritura


class RegistrosVueloDelGrupo(Paginado[AlumnoVuelo]):
    periodos: list[PeriodoDelAnio]
    aviso: Optional[str] = None


class RegistroVueloGuardar(ItemOperacion, ParcheSinNulos):
    """Un campo omitido no cambia; `id_nivel_proyeccion` y `observacion` en null se
    borran. La entrada, la sugerencia y el nivel del programa no se aceptan."""

    model_config = ConfigDict(extra="forbid")
    CAMPOS_NULABLES = frozenset({
        "version", "realizado_en", "id_actividad", "id_nivel_colocado", "id_nivel_proyeccion", "observacion",
    })

    id_alumno: int
    id_periodo_academico: int
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int
    id_nivel_colocado: Optional[int] = None
    id_nivel_proyeccion: Optional[int] = None
    observacion: Optional[str] = Field(default=None, max_length=1000)
