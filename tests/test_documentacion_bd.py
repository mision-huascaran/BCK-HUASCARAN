"""El diseño de la base de datos (docs/base-de-datos.md) documenta exactamente las tablas y
columnas de los modelos: falla si el documento y los modelos se desalinean.

Cada tabla del documento empieza con un encabezado ### `nombre` y sus campos son la
primera columna de la tabla de columnas que lo sigue.
"""
import importlib
import re
from pathlib import Path

from sqlmodel import SQLModel

DOCUMENTO = Path(__file__).resolve().parent.parent / "docs" / "base-de-datos.md"
ENCABEZADO = re.compile(r"^### `([^`]+)`\s*$")
FILA_DE_CAMPO = re.compile(r"^\| `([^`]+)` \|")


def columnas_documentadas(texto: str) -> dict[str, set[str]]:
    """{tabla: campos} leídos del documento."""
    tablas: dict[str, set[str]] = {}
    actual, en_tabla = None, False
    for linea in texto.splitlines():
        encabezado = ENCABEZADO.match(linea)
        if encabezado:
            actual, en_tabla = encabezado.group(1), False
            tablas[actual] = set()
            continue
        if actual is None:
            continue
        campo = FILA_DE_CAMPO.match(linea)
        if campo:
            tablas[actual].add(campo.group(1))
            en_tabla = True
        elif en_tabla and not linea.startswith("|"):
            actual = None
    return tablas


def columnas_de_los_modelos() -> dict[str, set[str]]:
    # Importar el paquete registra todas las tablas en SQLModel.metadata.
    importlib.import_module("app.models")
    return {nombre: {c.name for c in tabla.columns} for nombre, tabla in SQLModel.metadata.tables.items()}


def diferencias(modelos: dict[str, set[str]], documento: dict[str, set[str]]) -> list[str]:
    """Lo que falta o sobra en el documento respecto de los modelos."""
    errores = [f"Falta documentar la tabla {t}" for t in sorted(modelos.keys() - documento.keys())]
    errores += [f"El documento tiene la tabla {t}, que no está en los modelos" for t in sorted(documento.keys() - modelos.keys())]
    for tabla in sorted(modelos.keys() & documento.keys()):
        errores += [f"Falta documentar la columna {tabla}.{c}" for c in sorted(modelos[tabla] - documento[tabla])]
        errores += [f"El documento tiene la columna {tabla}.{c}, que no está en el modelo" for c in sorted(documento[tabla] - modelos[tabla])]
    return errores


def test_el_documento_tiene_las_tablas_y_columnas_de_los_modelos():
    documento = columnas_documentadas(DOCUMENTO.read_text(encoding="utf-8"))

    errores = diferencias(columnas_de_los_modelos(), documento)

    assert not errores, "docs/base-de-datos.md no coincide con los modelos:\n" + "\n".join(errores)


def test_lee_los_campos_de_la_tabla_que_sigue_a_cada_encabezado():
    texto = "\n".join([
        "### `uno`", "", "| Campo | Tipo |", "|---|---|", "| `a` | INTEGER |", "| `b` | TEXT |", "",
        "- Restricción con `c`.", "| `d` | otra tabla después |",
        "### `dos`", "| `e` | DATE |",
    ])

    assert columnas_documentadas(texto) == {"uno": {"a", "b"}, "dos": {"e"}}


def test_diferencias_lista_lo_que_falta_y_lo_que_sobra():
    modelos = {"uno": {"a", "b"}, "dos": {"c"}}
    documento = {"uno": {"a", "x"}, "tres": {"d"}}

    assert diferencias(modelos, documento) == [
        "Falta documentar la tabla dos",
        "El documento tiene la tabla tres, que no está en los modelos",
        "Falta documentar la columna uno.b",
        "El documento tiene la columna uno.x, que no está en el modelo",
    ]
