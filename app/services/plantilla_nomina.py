"""Plantilla .xlsx para llenar la nómina que se sube en "Importar nómina" (CU014, DP-12).

Trae el núcleo común de columnas de las nóminas de Misión Huascarán. Solo las 4 que importa
SICEDU (II.EE, GRADO, NOMBRES Y APELLIDOS y DNI) tienen nota y validación; las demás son
de uso interno y van sin nota, validaciones ni fórmulas. La lista de colegios va en una
columna oculta de la misma hoja: una hoja aparte, sin encabezados, saldría como "Hoja
omitida" al importar, y las filas de esa columna no tienen nombre, así que el lector las
ignora. No lleva filas de ejemplo: una que nadie borre se importaría como alumno.
"""
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.services.importacion_nomina import MAXIMO_ALUMNOS

MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NOMBRE_ARCHIVO = "plantilla_nomina_sicedu.xlsx"
TITULO_LIBRO = "Plantilla de nómina SICEDU"
NOMBRE_HOJA = "Nómina"
AUTOR_NOTAS = "SICEDU"

TITULOS_PLANTILLA = (
    "N°", "II.EE", "GRADO", "NOMBRES Y APELLIDOS", "DNI", "SEXO", "F. NACIMIENTO", "EDAD", "PESO", "TALLA",
    "IMC", "T/E", "IMC/E", "Dx Nutricional", "Hb", "Hb con descuento", "Dx",
)
ANCHOS = {
    "N°": 6, "II.EE": 30, "GRADO": 10, "NOMBRES Y APELLIDOS": 44, "DNI": 14, "F. NACIMIENTO": 15,
    "Dx Nutricional": 16, "Hb con descuento": 14,
}
ANCHO_POR_DEFECTO = 10
ALTO_TITULOS = 30
PANELES = "E2"

PRIMERA_FILA = 2
ULTIMA_FILA = PRIMERA_FILA + MAXIMO_ALUMNOS - 1
COLUMNA_COLEGIOS = "AZ"
GRADOS = "1°,2°,3°,4°,5°,6°"
FORMATO_TEXTO = "@"

AZUL_SICEDU = "102B55"
AZUL_INTERNO = "2F4E7E"
BLANCO = "FFFFFF"
COLOR_BORDE = "DCE6F5"


@dataclass(frozen=True)
class ColumnaSicedu:
    """Nota y validación de una de las 4 columnas que importa SICEDU. La fórmula va sin el
    "=" inicial, como la guarda Excel en el archivo."""

    titulo: str
    nota: str
    tipo: str
    formula: str
    error_titulo: Optional[str] = None
    error: Optional[str] = None


COLEGIO = ColumnaSicedu(
    "II.EE", "Obligatorio. Elige el colegio de la lista (solo colegios activos de SICEDU).",
    "list", "", "Colegio no registrado", "Elige un colegio de la lista.",
)
COLUMNAS_SICEDU = (
    COLEGIO,
    ColumnaSicedu(
        "GRADO", "Opcional. Grado de 1° a 6°. Si lo dejas vacío, el alumno se importa sin grado.",
        "list", f'"{GRADOS}"', "Grado no reconocido", "Elige un grado de 1° a 6°.",
    ),
    ColumnaSicedu(
        "NOMBRES Y APELLIDOS",
        "Obligatorio. Escribe APELLIDOS, Nombres (con coma). Ej.: QUISPE MAMANI, Juan Carlos",
        "custom", "TRUE",
    ),
    ColumnaSicedu(
        "DNI", "Opcional. 8 dígitos. Si lo dejas vacío, el alumno se importa sin DNI.",
        "custom", "AND(LEN(E2)=8,ISNUMBER(VALUE(E2)))", "DNI no válido", "El DNI debe tener exactamente 8 dígitos.",
    ),
)
POR_TITULO = {c.titulo: c for c in COLUMNAS_SICEDU}


def letra(titulo: str) -> str:
    """La letra de la columna de ese título en la plantilla (A, B, ...)."""
    return chr(ord("A") + TITULOS_PLANTILLA.index(titulo))


def rango(titulo: str) -> str:
    columna = letra(titulo)
    return f"{columna}{PRIMERA_FILA}:{columna}{ULTIMA_FILA}"


def _titulos(hoja: Worksheet) -> None:
    fuente = Font(bold=True, color=BLANCO)
    alineacion = Alignment(horizontal="center", vertical="center", wrap_text=True)
    borde = Border(bottom=Side(style="thin", color=COLOR_BORDE))
    for posicion, titulo in enumerate(TITULOS_PLANTILLA, start=1):
        celda = hoja.cell(row=1, column=posicion, value=titulo)
        color = AZUL_SICEDU if titulo in POR_TITULO else AZUL_INTERNO
        celda.font, celda.alignment, celda.border = fuente, alineacion, borde
        celda.fill = PatternFill(fill_type="solid", start_color=color, end_color=color)
        if titulo in POR_TITULO:
            celda.comment = Comment(POR_TITULO[titulo].nota, AUTOR_NOTAS)
        hoja.column_dimensions[letra(titulo)].width = ANCHOS.get(titulo, ANCHO_POR_DEFECTO)
    hoja.row_dimensions[1].height = ALTO_TITULOS


def _validacion(hoja: Worksheet, columna: ColumnaSicedu, formula: str) -> None:
    validacion = DataValidation(
        type=columna.tipo, formula1=formula, allow_blank=True,
        showInputMessage=True, promptTitle=columna.titulo, prompt=columna.nota,
        showErrorMessage=columna.error is not None, errorTitle=columna.error_titulo, error=columna.error,
    )
    validacion.add(rango(columna.titulo))
    hoja.add_data_validation(validacion)


def _lista_de_colegios(hoja: Worksheet, nombres_colegios: list[str]) -> Optional[str]:
    """Escribe los colegios en la columna oculta y devuelve su rango; None si no hay."""
    hoja.column_dimensions[COLUMNA_COLEGIOS].hidden = True
    for fila, nombre in enumerate(nombres_colegios, start=PRIMERA_FILA):
        celda = hoja[f"{COLUMNA_COLEGIOS}{fila}"]
        celda.value = nombre
        # openpyxl guarda como fórmula todo texto que empiece con "=": siempre texto.
        celda.data_type = "s"
    if not nombres_colegios:
        return None
    ultima = PRIMERA_FILA + len(nombres_colegios) - 1
    return f"${COLUMNA_COLEGIOS}${PRIMERA_FILA}:${COLUMNA_COLEGIOS}${ultima}"


def _validaciones(hoja: Worksheet, nombres_colegios: list[str]) -> None:
    fuente_colegios = _lista_de_colegios(hoja, nombres_colegios)
    if fuente_colegios is not None:
        _validacion(hoja, COLEGIO, fuente_colegios)
    for columna in COLUMNAS_SICEDU[1:]:
        _validacion(hoja, columna, columna.formula)
    for (celda,) in hoja[rango("DNI")]:
        celda.number_format = FORMATO_TEXTO


def generar_plantilla(nombres_colegios: list[str]) -> bytes:
    """El .xlsx de la plantilla, con esos colegios (en ese orden) en la lista de II.EE."""
    libro = Workbook()
    libro.properties.title = TITULO_LIBRO
    hoja = libro.active
    hoja.title = NOMBRE_HOJA
    _titulos(hoja)
    _validaciones(hoja, nombres_colegios)
    hoja.freeze_panes = PANELES
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()
