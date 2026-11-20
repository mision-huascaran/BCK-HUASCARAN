"""Regla de nombres de personas (app/core/nombre_persona.py). Datos inventados.

Es la misma tabla de casos que FE-HUASCARAN `src/lib/__tests__/nombrePersona.test.js`: si
una de las dos reglas cambia, esta tabla tiene que cambiar en los dos repos.
"""
import pytest

from app.core.nombre_persona import (
    LARGO_MAXIMO_NOMBRE,
    MOTIVO_CARACTERES,
    MOTIVO_ESPACIOS,
    MOTIVO_EXTREMOS,
    MOTIVO_LARGO,
    motivo_nombre_invalido,
    parece_formula,
)

VALIDOS = ["Rosa", "José María", "Ñahui Quispe", "Huamán-Villar", "O'Higgins", "D’Alessandro", "Müller", "Ana  Luz ", "Ana2", "Luis 3ro"]
CON_SIMBOLOS = ["<script>", "Ana>", "Ana=Luz", "Ana;", "{Ana}", "Ana$", "ana@correo", "Ana, Luz", "=SUM(1)", "+SUM(1,1)", "@Ana", "Ana (2)"]
CON_SIGNO_EN_LOS_EXTREMOS = ["-Ana", "'Ana", "Ana-"]


@pytest.mark.parametrize("nombre", VALIDOS)
def test_acepta(nombre):
    assert motivo_nombre_invalido(nombre) is None


@pytest.mark.parametrize("nombre", ["", None])
def test_vacio_lo_decide_quien_llama(nombre):
    assert motivo_nombre_invalido(nombre) is None


def test_solo_espacios_no_es_un_nombre():
    assert motivo_nombre_invalido("     ") == MOTIVO_ESPACIOS


@pytest.mark.parametrize("largo", [LARGO_MAXIMO_NOMBRE + 1, 300, 10000])
def test_rechaza_textos_largos(largo):
    assert motivo_nombre_invalido("a" * largo) == MOTIVO_LARGO


def test_acepta_justo_el_maximo():
    assert motivo_nombre_invalido("a" * LARGO_MAXIMO_NOMBRE) is None


@pytest.mark.parametrize("nombre", CON_SIMBOLOS)
def test_rechaza_simbolos(nombre):
    assert motivo_nombre_invalido(nombre) == MOTIVO_CARACTERES


@pytest.mark.parametrize("nombre", CON_SIGNO_EN_LOS_EXTREMOS)
def test_rechaza_que_empiece_o_termine_con_guion_o_apostrofo(nombre):
    assert motivo_nombre_invalido(nombre) == MOTIVO_EXTREMOS


@pytest.mark.parametrize(
    "texto", ['=HYPERLINK("http://ejemplo.invalid","x")', "+SUM(1,1)", "-1+1", "@SUM(1)", "\t=1", "\rAna", "\nAna", "  =1"]
)
def test_parece_formula(texto):
    assert parece_formula(texto)


@pytest.mark.parametrize("texto", ["QUISPE, Juan", "—", "Ana-Luz", ""])
def test_no_parece_formula(texto):
    assert not parece_formula(texto)
