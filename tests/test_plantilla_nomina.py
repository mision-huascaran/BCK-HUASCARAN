"""Plantilla de la nómina (CU014, DP-12): endpoint, contenido y coherencia con el lector."""
import uuid
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.services import importacion_service
from app.services.importacion_nomina import OBLIGATORIAS, clasificar, columnas_de, leer_nomina
from app.services.plantilla_nomina import (
    AZUL_INTERNO,
    AZUL_SICEDU,
    COLUMNAS_SICEDU,
    TITULOS_PLANTILLA,
    generar_plantilla,
    rango,
)
from tests.conftest import crear_colegio, motivo

PLANTILLA = "/alumnos/importaciones/plantilla"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TITULOS_SICEDU = ("II.EE", "GRADO", "NOMBRES Y APELLIDOS", "DNI")
INYECCION = '=HYPERLINK("http://x","y")'
ANDINO, LEJANO = "Colegio Andino", "Colegio Lejano"


def abrir(datos: bytes):
    return load_workbook(BytesIO(datos))


def hoja_de(datos: bytes):
    return abrir(datos)["Nómina"]


def validaciones(hoja) -> dict[str, object]:
    """{rango: validación} de la hoja."""
    return {str(v.sqref): v for v in hoja.data_validations.dataValidation}


def titulos_de(hoja) -> tuple:
    """Las celdas de la fila 1 hasta la última columna de la plantilla (sin la columna AZ)."""
    return hoja[1][: len(TITULOS_PLANTILLA)]


def colegios_de(hoja) -> list[str]:
    return [c.value for (c,) in hoja.iter_rows(min_row=2, min_col=52, max_col=52) if c.value is not None]


@pytest.fixture
def descarga(client, cabeceras):
    respuesta = client.get(PLANTILLA, headers=cabeceras["Supervisor"])
    assert respuesta.status_code == 200, respuesta.text
    return respuesta


# ── 1. Endpoint y permisos ──────────────────────────────────────────────────────────

def test_el_supervisor_descarga_la_plantilla(descarga):
    assert (
        descarga.headers["content-type"],
        descarga.headers["content-disposition"],
        descarga.headers["cache-control"],
    ) == (XLSX, 'attachment; filename="plantilla_nomina_sicedu.xlsx"', "no-store")


@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_otros_roles_no_pueden_descargarla(client, cabeceras, rol):
    respuesta = client.get(PLANTILLA, headers=cabeceras[rol])

    assert (respuesta.status_code, motivo(respuesta)) == (403, "sin_permiso")


def test_sin_sesion_no_se_descarga(client, entorno):
    respuesta = client.get(PLANTILLA)

    assert (respuesta.status_code, motivo(respuesta)) == (401, "sesion_invalida")


def test_openapi_la_documenta_como_binaria(client):
    contenido = client.get("/openapi.json").json()["paths"][PLANTILLA]["get"]["responses"]["200"]["content"]

    assert contenido == {XLSX: {"schema": {"type": "string", "format": "binary"}}}


# ── 2. Contenido ────────────────────────────────────────────────────────────────────

def test_una_sola_hoja_con_los_titulos_en_orden(descarga):
    libro = abrir(descarga.content)
    hoja = libro.active

    assert (libro.sheetnames, libro.properties.title) == (["Nómina"], "Plantilla de nómina SICEDU")
    assert [c.value for c in titulos_de(hoja)] == list(TITULOS_PLANTILLA)
    assert (hoja.freeze_panes, hoja.row_dimensions[1].height) == ("E2", 30)


def test_formato_y_notas_de_los_titulos(descarga):
    hoja = hoja_de(descarga.content)
    rellenos = {c.value: c.fill.fgColor.rgb[-6:] for c in titulos_de(hoja)}
    con_nota = {c.value: c.comment.text for c in titulos_de(hoja) if c.comment is not None}

    assert rellenos == {t: AZUL_SICEDU if t in TITULOS_SICEDU else AZUL_INTERNO for t in TITULOS_PLANTILLA}
    assert con_nota == {c.titulo: c.nota for c in COLUMNAS_SICEDU}
    assert {c.comment.author for c in titulos_de(hoja) if c.comment is not None} == {"SICEDU"}
    assert {(c.font.color.rgb[-6:], c.font.bold, c.alignment.horizontal, c.alignment.wrap_text) for c in titulos_de(hoja)} == {
        ("FFFFFF", True, "center", True)
    }
    assert {c.border.bottom.style for c in titulos_de(hoja)} == {"thin"}


def test_sin_filas_de_ejemplo(descarga):
    hoja = hoja_de(descarga.content)

    assert all(c.value is None for fila in hoja.iter_rows(min_row=2, max_col=len(TITULOS_PLANTILLA)) for c in fila)


def test_los_titulos_de_sicedu_los_reconoce_el_lector():
    columnas = columnas_de(TITULOS_PLANTILLA)

    assert len(columnas) == 4
    assert OBLIGATORIAS <= columnas.keys()
    assert sorted(columnas.values()) == [TITULOS_PLANTILLA.index(t) for t in TITULOS_SICEDU]


# ── 3. Validaciones ─────────────────────────────────────────────────────────────────

def test_validaciones_de_las_cuatro_columnas(descarga):
    hoja = hoja_de(descarga.content)
    encontradas = validaciones(hoja)

    assert sorted(encontradas) == ["B2:B3001", "C2:C3001", "D2:D3001", "E2:E3001"]
    resumen = {r: (v.type, v.formula1, v.allow_blank, v.showErrorMessage) for r, v in encontradas.items()}
    assert resumen == {
        "B2:B3001": ("list", "$AZ$2:$AZ$3", True, True),
        "C2:C3001": ("list", '"1°,2°,3°,4°,5°,6°"', True, True),
        "D2:D3001": ("custom", "TRUE", True, False),
        "E2:E3001": ("custom", "AND(LEN(E2)=8,ISNUMBER(VALUE(E2)))", True, True),
    }
    assert (encontradas["E2:E3001"].errorTitle, encontradas["E2:E3001"].error) == (
        "DNI no válido", "El DNI debe tener exactamente 8 dígitos."
    )
    assert {r: (v.promptTitle, v.prompt) for r, v in encontradas.items()} == {
        rango(c.titulo): (c.titulo, c.nota) for c in COLUMNAS_SICEDU
    }


def test_el_dni_tiene_formato_de_texto(descarga):
    hoja = hoja_de(descarga.content)

    assert {c.number_format for (c,) in hoja["E2:E3001"]} == {"@"}


# ── 4. Lista de colegios ────────────────────────────────────────────────────────────

def test_solo_colegios_activos_y_ordenados(client, db, entorno, cabeceras):
    id_actor = entorno.supervisor.id_usuario
    crear_colegio(db, "álamo", [], [], id_actor)
    crear_colegio(db, "Ávila Cerrado", [], [], id_actor, activo=False)
    db.commit()

    hoja = hoja_de(client.get(PLANTILLA, headers=cabeceras["Supervisor"]).content)

    assert colegios_de(hoja) == ["álamo", ANDINO, LEJANO]
    assert hoja["AZ1"].value is None
    assert hoja.column_dimensions["AZ"].hidden
    assert validaciones(hoja)["B2:B3001"].formula1 == "$AZ$2:$AZ$4"


def test_sin_colegios_activos_no_hay_lista():
    hoja = hoja_de(generar_plantilla([]))

    assert sorted(validaciones(hoja)) == ["C2:C3001", "D2:D3001", "E2:E3001"]
    assert colegios_de(hoja) == []


# ── 5. Inyección de fórmulas ────────────────────────────────────────────────────────

def test_un_colegio_con_formula_queda_como_texto(client, db, entorno, cabeceras):
    crear_colegio(db, INYECCION, [], [], entorno.supervisor.id_usuario)
    db.commit()

    hoja = hoja_de(client.get(PLANTILLA, headers=cabeceras["Supervisor"]).content)
    celda = next(c for (c,) in hoja.iter_rows(min_row=2, min_col=52, max_col=52) if c.value == INYECCION)

    assert celda.data_type == "s"


# ── 6. Ida y vuelta con el lector ───────────────────────────────────────────────────

def analizar(db, datos: bytes):
    nomina = leer_nomina(datos)
    return clasificar(nomina, importacion_service._contexto(db, nomina))


def llenar(datos: bytes, *filas: dict[str, object]) -> bytes:
    libro = abrir(datos)
    hoja = libro.active
    for numero, valores in enumerate(filas, start=2):
        for titulo, valor in valores.items():
            hoja.cell(row=numero, column=TITULOS_PLANTILLA.index(titulo) + 1, value=valor)
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()


def test_la_plantilla_vacia_no_trae_alumnos_ni_observaciones(db, descarga):
    analisis = analizar(db, descarga.content)

    assert (analisis.listos, analisis.observaciones) == (0, [])


def test_la_plantilla_llena_se_importa_sin_observaciones(db, descarga):
    llena = llenar(
        descarga.content,
        {"N°": 1, "II.EE": ANDINO, "GRADO": "1°", "NOMBRES Y APELLIDOS": "PRUEBA ROJAS, Elena", "DNI": "01234567"},
        {"N°": 2, "II.EE": LEJANO, "NOMBRES Y APELLIDOS": "PRUEBA SALAS, Hugo", "DNI": "70000011"},
        {
            "N°": 3, "II.EE": ANDINO, "GRADO": "6°", "NOMBRES Y APELLIDOS": "PRUEBA VEGA, Noa", "SEXO": "F",
            "PESO": 30.5, "TALLA": 1.35, "IMC": 16.7, "Hb": 12.1, "Hb con descuento": 11.4, "Dx": "Normal",
        },
    )

    analisis = analizar(db, llena)

    assert (analisis.listos, analisis.observaciones) == (3, [])
    assert [a.dni for a in analisis.altas] == ["01234567", "70000011", None]
    assert analisis.altas[1].id_grado is None


# ── 7. Rutas vecinas ────────────────────────────────────────────────────────────────

def test_las_rutas_vecinas_no_cambian(client, entorno, cabeceras):
    importacion = client.get(f"/alumnos/importaciones/{uuid.uuid4()}", headers=cabeceras["Supervisor"])
    alumno = client.get("/alumnos/123", headers=cabeceras["Supervisor"])

    assert (importacion.status_code, motivo(importacion)) == (404, "importacion_no_encontrada")
    assert (alumno.status_code, motivo(alumno)) == (404, "alumno_no_encontrado")
