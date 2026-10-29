"""D80 · GET /usuarios/{id} dice qué periodos del Docente son historial (CU016): `estado`
de cada periodo y `solo_historial` de cada colegio, según el calendario de ese colegio."""
import pytest
from sqlmodel import select

from app.models.organizacion import DocenteColegioGrado
from tests.conftest import asignar, crear_particular, rangos_en_receso


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def asignar_en_lejano(db, entorno, *claves, retroactiva=False) -> None:
    lejano = entorno.colegios["Lejano"]
    for clave in claves:
        asignar(db, entorno.id_docente, lejano, [entorno.grados["3.º"]], entorno.periodos[clave], 1)
    if retroactiva:
        for fila in db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_colegio == lejano.id_colegio)):
            fila.retroactiva = True
            db.add(fila)
    db.commit()


def resumen(client, entorno, sup) -> list[tuple]:
    detalle = client.get(f"/usuarios/{entorno.docente.id_usuario}", headers=sup).json()["asignacion"]
    return [
        (c["colegio"], c["solo_historial"], [(p["numero"], p["estado"], p["vigente"]) for p in c["periodos"]])
        for c in detalle["colegios"]
    ]


def test_d80_colegio_solo_con_un_pa_culminado_es_historial(client, db, entorno, sup):
    # Andino: PA1 (culminado) y PA2 (en curso). Lejano: solo el PA1, que ya terminó.
    asignar_en_lejano(db, entorno, "pasado")

    assert resumen(client, entorno, sup) == [
        ("Colegio Andino", False, [(1, "culminado", False), (2, "vigente", True)]),
        ("Colegio Lejano", True, [(1, "culminado", False)]),
    ]


def test_d80_retroactiva_durante_su_pr_es_vigente_y_el_pa_siguiente_es_proximo(client, db, entorno, sup):
    # Lejano culminó su PA2 ayer y hoy está en el PR2: la retroactiva del PA2 todavía sirve.
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))
    asignar_en_lejano(db, entorno, "vigente", retroactiva=True)
    asignar_en_lejano(db, entorno, "futuro")

    assert resumen(client, entorno, sup)[1] == (
        "Colegio Lejano", False, [(2, "vigente", False), (3, "proximo", False)],
    )


def test_d80_sin_retroactiva_el_pa_recien_terminado_es_culminado(client, db, entorno, sup):
    crear_particular(db, entorno, rangos_en_receso(entorno.hoy))
    asignar_en_lejano(db, entorno, "vigente")

    assert resumen(client, entorno, sup)[1] == ("Colegio Lejano", True, [(2, "culminado", False)])


def test_d80_un_colegio_solo_con_pa_proximos_no_es_historial(client, db, entorno, sup):
    asignar_en_lejano(db, entorno, "futuro", "cuarto")

    assert resumen(client, entorno, sup)[1] == (
        "Colegio Lejano", False, [(3, "proximo", False), (4, "proximo", False)],
    )
