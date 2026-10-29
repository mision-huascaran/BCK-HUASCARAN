"""Dashboard D7: el rol permitido de cada endpoint, contra lo planeado.

Solo Supervisor: los que tienen nombres de alumnos, listados nominales o detalle de un
colegio (CU035: el Directivo solo ve agregados). El Docente no tiene Dashboard.
"""
import pytest

from app.main import app

SOLO_SUPERVISOR = {
    "distribucion-subprograma", "asistencia/clases", "comparacion-resultados", "libros/colegios/{id_colegio}",
    "libros/desglose",
}
SUPERVISOR_O_DIRECTIVO = {
    "filtros", "resultados-aprendizaje", "asistencia", "libros", "evolucion-anual", "comparacion-colegios",
}
DEL_PERIODO = {"distribucion-subprograma", "resultados-aprendizaje", "asistencia", "asistencia/clases",
               "comparacion-resultados", "comparacion-colegios"}


def _peticion(entorno, ruta) -> tuple[str, dict]:
    andino = entorno.colegios["Andino"].id_colegio
    params = {"id_anio_escolar": entorno.anio.id_anio_escolar, "todos_los_colegios": True}
    if ruta in DEL_PERIODO:
        params["id_periodo_academico"] = entorno.periodos["vigente"].id_periodo_academico
    extra = {
        "asistencia/clases": {"id_colegio": andino},
        "comparacion-resultados": {"id_programa": entorno.programas["Comprensión Lectora"], "todos_los_grados": True},
        "libros/desglose": {"id_alumno": entorno.alumnos["ana"].id_alumno, "anio": entorno.hoy.year,
                            "mes": entorno.hoy.month},
        "comparacion-colegios": {"indicador": "asistencia"},
    }
    params.update(extra.get(ruta, {}))
    return "/dashboard/" + ruta.replace("{id_colegio}", str(andino)), ({} if ruta == "filtros" else params)


def test_los_endpoints_planeados_son_los_publicados():
    publicados = {r.removeprefix("/dashboard/") for r in app.openapi()["paths"] if r.startswith("/dashboard/")}
    assert publicados == SOLO_SUPERVISOR | SUPERVISOR_O_DIRECTIVO


@pytest.mark.parametrize("ruta", sorted(SOLO_SUPERVISOR | SUPERVISOR_O_DIRECTIVO))
def test_rol_permitido_de_cada_endpoint(client, cabeceras, entorno, ruta):
    url, params = _peticion(entorno, ruta)
    codigos = {rol: client.get(url, params=params, headers=cabeceras[rol]).status_code
               for rol in ("Supervisor", "Directivo", "Docente")}
    esperado_directivo = 403 if ruta in SOLO_SUPERVISOR else 200
    assert codigos == {"Supervisor": 200, "Directivo": esperado_directivo, "Docente": 403}
    assert client.get(url, params=params).status_code == 401
