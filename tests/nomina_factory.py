"""Excel ficticios que imitan la nómina real (CU014): encabezados con espacios, columnas
extra y repetidas, filas vacías, y grado y DNI como número o como texto. Todos los nombres
y DNI son inventados.
"""
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Optional

from openpyxl import Workbook

ENCABEZADOS = ["N°", "II.EE", "GRADO ", "NOMBRES Y APELLIDOS ", "DNI ", "SEXO", "PESO", "TALLA", "IMC", "PESO", "IMC"]
SIN_DNI = ["N°", " ii.ee ", "Grado", "Nombres  y  Apellidos", "SEXO"]
EXTRAS = ["M", 21.5, 1.2, 14.9, 22.0, 15.1]
FILA_VACIA: tuple = ()


@dataclass
class Hoja:
    titulo: str
    filas: list[list[Any]] = field(default_factory=list)


def fila(colegio: Any, grado: Any, nombre: Any, dni: Any = None) -> list[Any]:
    """Una fila con las columnas de `ENCABEZADOS`."""
    return [1, colegio, grado, nombre, dni, *EXTRAS]


def fila_sin_dni(colegio: Any, grado: Any, nombre: Any) -> list[Any]:
    """Una fila con las columnas de `SIN_DNI`."""
    return [1, colegio, grado, nombre, "F"]


def hoja(titulo: str, *filas: list[Any], encabezados: Optional[list[str]] = None, filas_previas: int = 0) -> Hoja:
    """Hoja con `filas_previas` filas vacías, los encabezados y sus filas."""
    previas: list[list[Any]] = [[] for _ in range(filas_previas)]
    return Hoja(titulo, [*previas, encabezados or ENCABEZADOS, *[list(f) for f in filas]])


def hoja_sin_encabezados(titulo: str = "NOTAS") -> Hoja:
    return Hoja(titulo, [["Observaciones", "Fecha"], ["Revisar talla", "2026-05-01"]])


def libro(*hojas: Hoja) -> bytes:
    """El .xlsx con esas hojas, en ese orden."""
    wb = Workbook()
    wb.remove(wb.active)
    for datos in hojas:
        ws = wb.create_sheet(datos.titulo)
        for numero, valores in enumerate(datos.filas, start=1):
            for columna, valor in enumerate(valores, start=1):
                if valor is not None:
                    ws.cell(row=numero, column=columna, value=valor)
    salida = BytesIO()
    wb.save(salida)
    return salida.getvalue()


def nomina_ejemplo(colegio: str = "I.E. E2E Playwright") -> bytes:
    """Nómina de ejemplo para los testers, pensada para el colegio del seed E2E."""
    variante = f"{colegio.upper()}  "
    return libro(
        hoja(
            "PLAYWRIGHT",
            fila(variante, 1, "PRUEBA ROJAS, Ana Lucía", 10000001),
            fila(colegio, "2°", "PRUEBA  MENDOZA,  Beto", "10000002"),
            fila(colegio, "3.º", "PRUEBA SALAS Carla Inés", 1000003),
            FILA_VACIA,
            fila(colegio, None, None),
            fila(colegio, "7°", "PRUEBA VEGA, Dario", "ABC123"),
            fila(colegio, 4, "PRUEBA SIN SEPARAR", 10000005),
            fila(colegio, None, "PRUEBA CASAS, Elsa"),
            fila(colegio, 5, "PRUEBA RAMOS, Fabio", 10000006),
        ),
        hoja(
            "OTRO COLEGIO",
            fila("PARIAQAQA", 1, "PRUEBA LUNA, Gina", 10000007),
            fila(None, 2, "PRUEBA PAZ, Hugo", 10000008),
        ),
        hoja(
            "SIN DNI",
            fila_sin_dni(colegio, "6°", "PRUEBA TELLO, Iris"),
            fila_sin_dni(colegio, 2.0, "PRUEBA ROJAS, Ana Lucía"),
            encabezados=SIN_DNI,
        ),
        hoja("REPETIDOS", fila(colegio, 3, "PRUEBA DIAZ, Juan", 10000006), filas_previas=4),
        hoja_sin_encabezados("RESUMEN"),
    )
