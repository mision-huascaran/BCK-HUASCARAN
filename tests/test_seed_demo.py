"""Seed de demostración (app/cli/seed_demo.py): candado de entorno, cuenta del Docente
existente intacta, alumnos e historial registrados por los servicios reales, e
idempotencia."""
from datetime import timedelta

import pytest
from sqlmodel import col, func, select

from app.cli import seed_demo
from app.cli.seed_demo import (
    ALUMNOS_POR_GRADO,
    COLEGIO,
    alumnos_ficticios,
    cierres_de,
    entorno_permitido,
    sembrar_demo,
)
from app.core import tiempo
from app.core.security import hash_password, verify_password
from app.models.evaluacion import Asistencia, LecturaSemanal, LecturaSemanalAlumno, RegistroVuelo, RubricaMensual
from app.models.organizacion import Alumno, AlumnoPertenencia, Colegio, DocenteColegioGrado, Usuario
from app.models.trazabilidad import Actividad, OperacionSincronizacion
from app.services.programacion import ClaseProgramada
from tests.conftest import semana_de
from tests.test_seed_e2e import base  # noqa: F401 - fixture: catálogos, Supervisor y calendario
from tests.test_seed_e2e import contar

CORREO = "docente.demo@sicedu.test"
VARIABLES = {"SEED_DEMO": "true", "ENTORNO": "uat", "DEMO_DOCENTE_CORREO": CORREO,
             "DEMO_DOCENTE_PASS": "Docente-Demo-1"}


@pytest.fixture
def semanas(db, base):  # noqa: F811 - la fixture `base` viene de test_seed_e2e
    """Las semanas de reporte del año, como las crea la carga del calendario."""
    hoy = tiempo.hoy_lima()
    dia = hoy - timedelta(days=200)
    while dia <= hoy + timedelta(days=150):
        semana_de(db, dia)
        dia += timedelta(days=7)
    db.commit()
    return base


def colegio_demo(db) -> Colegio:
    return db.exec(select(Colegio).where(Colegio.nombre == COLEGIO["nombre"])).one()


def rechazos(db) -> list[tuple]:
    return db.exec(
        select(OperacionSincronizacion.modulo, OperacionSincronizacion.motivo)
        .where(OperacionSincronizacion.estado != "aceptada")
    ).all()


# ── Sin la BD ───────────────────────────────────────────────────────────────────────

def test_solo_en_dev_qa_y_uat_y_con_la_bandera():
    assert entorno_permitido({"SEED_DEMO": "true", "ENTORNO": "uat"}) is None
    assert entorno_permitido({"SEED_DEMO": "true", "ENTORNO": "qa"}) is None
    assert entorno_permitido({"SEED_DEMO": "false", "ENTORNO": "uat"}) == "SEED_DEMO no es true"
    assert "no está permitido" in entorno_permitido({"SEED_DEMO": "true", "ENTORNO": "produccion"})
    assert "no está permitido" in entorno_permitido({"SEED_DEMO": "true"})


def test_alumnos_sin_repetidos_y_con_los_dos_subprogramas():
    for grado, cantidad in ALUMNOS_POR_GRADO.items():
        alumnos = alumnos_ficticios(grado, cantidad)
        assert len({(n, a) for n, a, _ in alumnos}) == cantidad
        assert {p for _, _, p in alumnos} == {"Alfabetización", "Comprensión Lectora"}
    # Más de una página de 40 en 3.º.
    assert ALUMNOS_POR_GRADO["3.º"] > 40


def test_cierres_de_mes_y_de_pa():
    hoy = tiempo.hoy_lima()
    mes_pasado = (hoy.replace(day=1) - timedelta(days=20))
    def clase(fecha, pa):
        return ClaseProgramada(None, 1, 7, fecha, fecha, "habitual", False, False, pa)

    clases = [clase(mes_pasado, 1), clase(mes_pasado + timedelta(days=7), 1), clase(hoy, 2)]
    cierres = cierres_de(clases, hoy, {1: 1, 2: 2}, {1: hoy - timedelta(days=1), 2: hoy + timedelta(days=30)})
    # La segunda del mes pasado cierra el mes y el PA1; la de hoy no cierra nada.
    assert cierres[(7, clases[0].fecha)].mes is (clases[1].fecha.month != clases[0].fecha.month)
    assert cierres[(7, clases[1].fecha)].numero_pa == 1
    assert cierres[(7, hoy)].mes is False
    assert cierres[(7, hoy)].numero_pa is None


# ── Con la BD ───────────────────────────────────────────────────────────────────────

def test_sin_bandera_no_hace_nada(db, semanas):
    resumen = sembrar_demo(db, {"ENTORNO": "uat"})
    assert resumen.omitidos
    assert db.exec(select(Colegio).where(Colegio.nombre == COLEGIO["nombre"])).first() is None


def test_en_produccion_lo_rechaza(db, semanas):
    resumen = sembrar_demo(db, {**VARIABLES, "ENTORNO": "produccion"})
    assert resumen.errores
    assert db.exec(select(Colegio).where(Colegio.nombre == COLEGIO["nombre"])).first() is None


def test_siembra_colegio_alumnos_y_asignaciones_sin_historial(db, semanas):
    resumen = sembrar_demo(db, VARIABLES, con_historial=False)

    assert not resumen.errores
    colegio = colegio_demo(db)
    total = sum(ALUMNOS_POR_GRADO.values())
    assert contar(db, Alumno) == total
    assert db.exec(select(func.count()).select_from(AlumnoPertenencia).where(
        AlumnoPertenencia.id_colegio == colegio.id_colegio)).one() == total
    docente = db.exec(select(Usuario).where(Usuario.correo == CORREO)).one()
    asignaciones = db.exec(select(func.count()).select_from(DocenteColegioGrado).where(
        DocenteColegioGrado.id_docente == docente.id_docente)).one()
    assert asignaciones == len(ALUMNOS_POR_GRADO) * 4


def test_una_cuenta_docente_existente_no_se_toca(db, semanas):
    """Puede ser la cuenta de una persona del equipo: no se le cambia nada."""
    sembrar_demo(db, VARIABLES, con_historial=False)
    docente = db.exec(select(Usuario).where(Usuario.correo == CORREO)).one()
    docente.nombres, docente.dni = "Antonia", "12345678"
    docente.password_hash = hash_password("Otra-Clave-9")
    db.add(docente)
    db.commit()

    resumen = sembrar_demo(db, {**VARIABLES, "DEMO_DOCENTE_PASS": ""}, con_historial=False)

    assert not resumen.errores
    db.expire_all()
    docente = db.exec(select(Usuario).where(Usuario.correo == CORREO)).one()
    assert (docente.nombres, docente.dni) == ("Antonia", "12345678")
    assert verify_password("Otra-Clave-9", docente.password_hash)


def test_una_cuenta_que_no_es_docente_se_rechaza(db, semanas):
    sembrar_demo(db, VARIABLES, con_historial=False)
    supervisor = db.exec(select(Usuario).where(col(Usuario.es_supervisor_original))).one()
    resumen = sembrar_demo(db, {**VARIABLES, "DEMO_DOCENTE_CORREO": supervisor.correo}, con_historial=False)
    assert any("no es una cuenta de Docente activa" in e for e in resumen.errores)


@pytest.fixture
def aulas_chicas(monkeypatch):
    """El historial recorre todo el año: con 72 alumnos son miles de operaciones y la
    prueba tarda minutos. Con 4 y 3 se ejercitan los mismos caminos."""
    monkeypatch.setattr(seed_demo, "ALUMNOS_POR_GRADO", {"3.º": 4, "4.º": 3})


def test_historial_por_los_servicios_sin_rechazos_e_idempotente(db, semanas, aulas_chicas):
    primera = sembrar_demo(db, VARIABLES)

    assert not primera.errores, primera.errores
    assert not primera.advertencias, primera.advertencias
    assert rechazos(db) == []
    assert primera.creados["día de historial demo"] > 0
    assert contar(db, Asistencia) > 0
    assert contar(db, LecturaSemanal) > 0
    assert contar(db, LecturaSemanalAlumno) > 0
    assert contar(db, RubricaMensual) > 0
    assert contar(db, RegistroVuelo) > 0
    # Las actividades del historial quedan cerradas y "sincronizadas" al terminar.
    actividades = db.exec(select(Actividad)).all()
    assert actividades
    assert all(a.fin is not None for a in actividades)
    assert all(a.sincronizado_en == a.fin for a in actividades)

    totales = {m: contar(db, m) for m in (Alumno, Asistencia, LecturaSemanal, RubricaMensual, RegistroVuelo,
                                          Actividad, OperacionSincronizacion)}
    segunda = sembrar_demo(db, VARIABLES)

    assert not segunda.errores
    assert not segunda.creados
    assert {m: contar(db, m) for m in totales} == totales

    # Hoy queda libre para registrarlo en vivo.
    hoy = tiempo.hoy_lima()
    assert db.exec(select(Actividad).where(Actividad.inicio >= tiempo.limites_lima(hoy, hoy)[0])).first() is None
