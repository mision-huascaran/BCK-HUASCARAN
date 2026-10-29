"""A9-08: pruebas de integración habituales de SICEDU.
"""
from datetime import date, timedelta

import pytest

from app.core import tiempo
from app.models.evaluacion import (
    LecturaSemanal, LecturaSemanalAlumno, SemanaReporte,
)
from app.services import dashboard_service as servicio
from app.schemas.dashboard import FiltrosDashboard
from tests.conftest import a_las, clase_guardada, registrar_asistencia


HOY = date(2026, 6, 17)


@pytest.fixture(autouse=True)
def fecha_fija(reloj):
    reloj.fijar(a_las(HOY, 9))


def _f(entorno, **campos):
    return FiltrosDashboard(id_anio_escolar=entorno.anio.id_anio_escolar, **campos)


def _get(client, cabeceras, ruta, rol="Supervisor", **params):
    return client.get("/dashboard/" + ruta, params=params, headers=cabeceras[rol])


def test_dashboard_http_docente_no_autorizado(client, cabeceras):
    respuesta = _get(client, cabeceras, "lectura", rol="Docente")
    assert respuesta.status_code == 403, respuesta.text


def test_dashboard_http_rechaza_colegios_repetidos(client, cabeceras, entorno):
    colegio = entorno.colegios["Andino"].id_colegio
    respuesta = _get(client, cabeceras, "lectura", id_colegios=[colegio, colegio])
    assert respuesta.status_code in (400, 422), respuesta.text


def test_dashboard_http_rechaza_colegio_inexistente(client, cabeceras):
    respuesta = _get(client, cabeceras, "lectura", id_colegio=999999)
    assert respuesta.status_code in (400, 404, 422), respuesta.text


def test_dashboard_sql_lectura_sin_registros(db, entorno):
    resultado = servicio.libros(db, _f(entorno))
    assert (resultado.total_libros, resultado.libros_lsn, resultado.libros_lsl) == (0, 0, 0)


def _agregar_lectura(db, entorno, alumno, fecha, titulo=None, lsl=None, estado="normal"):
    clase = clase_guardada(db, alumno, fecha)
    registrar_asistencia(db, alumno, fecha, "presente")
    ahora = tiempo.ahora_utc()
    lunes = fecha - timedelta(days=fecha.weekday())
    semana = SemanaReporte(id_anio_escolar=entorno.anio.id_anio_escolar,
                           numero_semana=fecha.isocalendar().week, fecha_inicio=lunes,
                           fecha_fin=lunes + timedelta(days=4), creado_por=1, creado_en=ahora)
    db.add(semana)
    db.flush()
    lectura = LecturaSemanal(id_colegio=alumno.id_colegio, id_grado=alumno.id_grado,
                            id_seccion=alumno.id_seccion, id_programa=alumno.id_programa_actual,
                            id_anio_escolar=entorno.anio.id_anio_escolar, mes=fecha.month,
                            id_semana=semana.id_semana, id_clase_referencia=clase.id_clase,
                            estado=estado, id_docente=entorno.id_docente,
                            fecha_registro=ahora, creado_por=1, creado_en=ahora,
                            motivo_no_realizada="Sin sesión" if estado == "no_realizada" else None)
    db.add(lectura)
    db.flush()
    db.add(LecturaSemanalAlumno(id_lectura_semanal=lectura.id, id_alumno=alumno.id_alumno,
                               lsn_titulo=titulo, lsn_nota=5 if titulo else None,
                               lsn_maximo=5 if titulo else None, cantidad_lsl=lsl,
                               id_docente=entorno.id_docente, fecha_registro=ahora,
                               creado_por=1, creado_en=ahora))
    db.commit()


def test_dashboard_sql_lectura_suma_lsn_lsl_y_relecturas(db, entorno):
    alumna = entorno.alumnos["ana"]
    _agregar_lectura(db, entorno, alumna, HOY - timedelta(days=7), "Libro A", 2)
    _agregar_lectura(db, entorno, alumna, HOY, "Libro A", 1)
    resultado = servicio.libros(db, _f(entorno, id_colegio=alumna.id_colegio))
    assert (resultado.libros_lsn, resultado.libros_lsl, resultado.total_libros) == (2, 3, 5)
    assert resultado.alumnos_con_lecturas == 1
    assert sum(resultado.por_mes.values()) == 5
