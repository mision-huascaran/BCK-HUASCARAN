"""Comparación de textos (app/core/texto.py): la clave de Python ordena igual que la BD."""
import pytest
from sqlalchemy import literal
from sqlmodel import select

from app.core.texto import comparable, comparable_sql, palabras_buscadas

NOMBRES = ["Ñahui", "QA Ronda3", "de Prueba", "ÁLVAREZ", "Ávila", "alva", "Úrsula", "Güemes", "nuñez"]


@pytest.mark.parametrize(
    "texto, esperado",
    [("Ávila", "avila"), ("ÑAHUI", "nahui"), ("Güemes", "guemes"), ("QA Ronda3", "qa ronda3"), ("", "")],
)
def test_comparable_baja_a_minusculas_y_quita_las_tildes(texto, esperado):
    assert comparable(texto) == esperado


def test_comparable_ordena_sin_distinguir_mayusculas_ni_tildes():
    assert sorted(NOMBRES, key=comparable) == [
        "alva", "ÁLVAREZ", "Ávila", "de Prueba", "Güemes", "Ñahui", "nuñez", "QA Ronda3", "Úrsula",
    ]


def test_comparable_es_igual_a_comparable_sql(db):
    en_la_bd = [db.exec(select(comparable_sql(literal(n)))).one() for n in NOMBRES]

    assert en_la_bd == [comparable(n) for n in NOMBRES]


def test_palabras_buscadas_usa_la_misma_clave():
    assert palabras_buscadas("  Juan  PÉREZ ") == ["juan", "perez"]
