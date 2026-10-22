"""Motor de sugerencias de la rúbrica mensual (S10-04): función pura, sin BD."""
import pytest

from app.services.motor_sugerencias import (
    ALFABETIZACION,
    COMPRENSION_LECTORA,
    LETRAS,
    Sugerencia,
    sugerencia,
)

TEXTOS = {"bajar": "Bajar nivel", "mantener": "Mantener nivel", "subir": "Subir nivel"}

# (Fluidez, Comprensión, resultado en décimas, acción), calculados a mano: d = 4·F + 6·C.
ALFABETIZACION_VALIDAS = [
    (0, 1, 6, "bajar"), (0, 2, 12, "bajar"), (0, 3, 18, "bajar"), (0, 4, 24, "mantener"),
    (1, 1, 10, "bajar"), (1, 2, 16, "bajar"), (1, 3, 22, "mantener"), (1, 4, 28, "mantener"),
    (2, 1, 14, "bajar"), (2, 2, 20, "mantener"), (2, 3, 26, "mantener"), (2, 4, 32, "subir"),
    (3, 1, 18, "bajar"), (3, 2, 24, "mantener"), (3, 3, 30, "subir"), (3, 4, 36, "subir"),
    (4, 1, 22, "mantener"), (4, 2, 28, "mantener"), (4, 3, 34, "subir"), (4, 4, 40, "subir"),
]
COMPRENSION_LECTORA_VALIDAS = [
    (1, 1, 10, "bajar"), (1, 2, 16, "bajar"), (1, 3, 22, "mantener"), (1, 4, 28, "mantener"),
    (2, 1, 14, "bajar"), (2, 2, 20, "mantener"), (2, 3, 26, "mantener"), (2, 4, 32, "subir"),
    (3, 1, 18, "bajar"), (3, 2, 24, "mantener"), (3, 3, 30, "subir"), (3, 4, 36, "subir"),
    (4, 1, 22, "mantener"), (4, 2, 28, "mantener"), (4, 3, 34, "subir"), (4, 4, 40, "subir"),
]
PENDIENTE = Sugerencia(accion=None, texto="Pendiente de evaluar", resultado_decimas=None)


def test_las_tablas_cubren_todas_las_combinaciones_validas():
    assert (len(ALFABETIZACION_VALIDAS), len(COMPRENSION_LECTORA_VALIDAS)) == (20, 16)


@pytest.mark.parametrize(("fluidez", "comprension", "decimas", "accion"), ALFABETIZACION_VALIDAS)
def test_alfabetizacion_las_20_combinaciones(fluidez, comprension, decimas, accion):
    assert sugerencia(fluidez, comprension, ALFABETIZACION, "M") == Sugerencia(accion, TEXTOS[accion], decimas)


@pytest.mark.parametrize(("fluidez", "comprension", "decimas", "accion"), COMPRENSION_LECTORA_VALIDAS)
def test_comprension_lectora_las_16_combinaciones(fluidez, comprension, decimas, accion):
    assert sugerencia(fluidez, comprension, COMPRENSION_LECTORA, "M") == Sugerencia(accion, TEXTOS[accion], decimas)


@pytest.mark.parametrize(("fluidez", "comprension", "accion"), [
    (2, 2, "mantener"),  # 20: el borde inferior de mantener
    (3, 3, "subir"),  # 30: el borde inferior de subir
    (0, 3, "bajar"),  # 18
    (4, 1, "mantener"),  # 22
    (1, 3, "mantener"),  # 22
    (4, 2, "mantener"),  # 28: justo debajo de subir
    (3, 1, "bajar"),  # 18: justo debajo de mantener
])
def test_bordes_exactos(fluidez, comprension, accion):
    assert sugerencia(fluidez, comprension, ALFABETIZACION, "M").accion == accion


@pytest.mark.parametrize(("programa", "fluidez", "comprension"), [
    (COMPRENSION_LECTORA, 0, 3),  # Pre-inicio no existe en Comprensión Lectora
    (ALFABETIZACION, 2, 0),  # ni en la Comprensión de Alfabetización
    (COMPRENSION_LECTORA, 2, 0),
    (ALFABETIZACION, 5, 2),  # fuera de rango
    (ALFABETIZACION, -1, 2),
    (COMPRENSION_LECTORA, 2, 5),
    ("Otro programa", 2, 2),
])
def test_combinaciones_invalidas_quedan_pendientes(programa, fluidez, comprension):
    assert sugerencia(fluidez, comprension, programa, "M") == PENDIENTE


@pytest.mark.parametrize(("fluidez", "comprension"), [(None, None), (2, None), (None, 3)])
def test_falta_una_dimension_pendiente_de_evaluar(fluidez, comprension):
    assert sugerencia(fluidez, comprension, ALFABETIZACION, "M") == PENDIENTE


def test_en_aa_no_se_puede_bajar():
    assert sugerencia(0, 1, ALFABETIZACION, "aa") == Sugerencia("mantener", "Mantener, nivel mínimo", 6, "aa")


def test_en_z_no_se_puede_subir():
    assert sugerencia(4, 4, COMPRENSION_LECTORA, "Z") == Sugerencia("mantener", "Mantener, nivel máximo", 40, "Z")


@pytest.mark.parametrize(("fluidez", "comprension", "letra", "accion"), [
    (4, 4, "aa", "subir"),  # en aa un puntaje alto sí sube
    (2, 2, "aa", "mantener"),  # en aa mantener no es excepción
    (1, 1, "Z", "bajar"),  # en Z un puntaje bajo sí baja
    (2, 2, "Z", "mantener"),
])
def test_las_excepciones_solo_bloquean_el_movimiento_imposible(fluidez, comprension, letra, accion):
    resultado = sugerencia(fluidez, comprension, COMPRENSION_LECTORA, letra)

    assert (resultado.accion, resultado.texto, resultado.excepcion) == (accion, TEXTOS[accion], None)


def test_escala_alfabetica_de_27_letras():
    assert (len(LETRAS), LETRAS[0], LETRAS[1], LETRAS[-1]) == (27, "aa", "A", "Z")
