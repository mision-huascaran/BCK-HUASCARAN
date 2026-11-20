"""La plantilla de Excel de "Importar nómina" (CU014), que descarga la Supervisora.

Trae solo los encabezados que lee la importación (`importacion_nomina.ENCABEZADOS`) y una
lista desplegable en SUBPROGRAMA con los nombres del catálogo, para que no se escriban
valores libres. No trae una fila de ejemplo: se importaría como alumno. El ejemplo y las
instrucciones están en el modal de "Importar nómina" y en el mensaje de la celda.
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from app.services.importacion_nomina import MAXIMO_ALUMNOS

NOMBRE_ARCHIVO = "plantilla_nomina.xlsx"
TITULO_HOJA = "Nombre del colegio"
# (encabezado, ancho). SUBPROGRAMA va junto a GRADO.
COLUMNAS = (("II.EE", 22), ("GRADO", 8), ("SUBPROGRAMA", 24), ("NOMBRES Y APELLIDOS", 32), ("DNI", 12))
COLUMNA_SUBPROGRAMA = "C"
# Una celda con lista por cada alumno que admite una importación.
ULTIMA_FILA = MAXIMO_ALUMNOS + 1
# Excel limita a 255 caracteres la lista escrita en la validación y el mensaje de la celda.
LARGO_MAXIMO_LISTA = 255


def generar_plantilla(programas: list[str]) -> bytes:
    """El .xlsx de la plantilla, con la lista de `programas` (nombres del catálogo) en
    SUBPROGRAMA."""
    lista = ",".join(programas)
    if not programas or any("," in p or '"' in p for p in programas) or len(lista) + 2 > LARGO_MAXIMO_LISTA:
        raise ValueError("Los nombres de los subprogramas no caben en una lista de Excel.")

    libro = Workbook()
    hoja = libro.active
    hoja.title = TITULO_HOJA
    hoja.append([encabezado for encabezado, _ in COLUMNAS])
    for indice, (_, ancho) in enumerate(COLUMNAS):
        hoja.column_dimensions[chr(ord("A") + indice)].width = ancho

    validacion = DataValidation(
        type="list",
        formula1=f'"{lista}"',
        allow_blank=True,
        showDropDown=False,  # En openpyxl, False es mostrar la flecha de la lista.
        showErrorMessage=True,
        errorStyle="stop",
        errorTitle="Subprograma no válido",
        error=f"Elige un subprograma de la lista: {', '.join(programas)}.",
        showInputMessage=True,
        promptTitle="Subprograma",
        prompt=(
            "Elige uno de la lista. En 1.° grado, solo Alfabetización. Si lo dejas vacío, el alumno se "
            "importa con la pertenencia pendiente."
        ),
    )
    validacion.add(f"{COLUMNA_SUBPROGRAMA}2:{COLUMNA_SUBPROGRAMA}{ULTIMA_FILA}")
    hoja.add_data_validation(validacion)
    hoja.freeze_panes = "A2"

    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()
