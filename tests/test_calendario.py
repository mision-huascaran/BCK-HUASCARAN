"""Servicio del calendario académico por colegio (CU027, diseño v4 §3 y §8): periodo de
una fecha, días lectivos y alcance del Docente con calendarios distintos."""
import uuid
from datetime import date, timedelta

import pytest

from app.models.organizacion import ORIGEN_AUTOMATICO
from app.services import calendario as servicio
from app.services.alcance import alcance_docente
from app.services.calendario_gestion import derivar_recesos
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    asignar,
    cabecera_de,
    crear_docente,
    crear_particular,
    fijar_periodo,
    motivo,
    rangos_en_receso,
)

# Miércoles. El general (rangos_relativos) tiene el PA2 del 18/05 al 16/07 y el PR2 el 17/07.
HOY = date(2026, 6, 17)
LUNES = date(2026, 6, 15)
SABADO = date(2026, 6, 13)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


def particular_en_receso(db, entorno, colegio="Lejano"):
    """Calendario particular del colegio: su PA2 terminó el lunes 15 y desde el martes
    está en el PR2. El resto, como el general."""
    return crear_particular(db, entorno, rangos_en_receso(HOY, fin_pa2=LUNES), colegio)


def ids(entorno):
    return entorno.colegios["Andino"].id_colegio, entorno.colegios["Lejano"].id_colegio


# ── periodo_de ──────────────────────────────────────────────────────────────────────

def test_periodo_de_con_el_general(db, entorno):
    andino, _ = ids(entorno)

    periodo = servicio.periodo_de(db, andino, HOY)

    assert (periodo.tipo, periodo.numero, periodo.id_periodo_academico) == (
        "PA", 2, entorno.periodos["vigente"].id_periodo_academico
    )
    assert (periodo.fecha_inicio, periodo.fecha_fin) == entorno.rangos["PA2"]


def test_periodo_de_un_pr_trae_el_pa_anterior(db, entorno):
    andino, _ = ids(entorno)

    receso = servicio.periodo_de(db, andino, entorno.inicio("PR1"))

    assert (receso.tipo, receso.numero, receso.id_periodo_academico) == (
        "PR", 1, entorno.periodos["pasado"].id_periodo_academico
    )


@pytest.mark.parametrize(
    "clave, esperado",
    [("PA2", ("PA", 2)), ("PR2", ("PR", 2)), ("PA3", ("PA", 3)), ("PR4", ("PR", 4))],
)
def test_periodo_de_bordes_inclusivos(db, entorno, clave, esperado):
    andino, _ = ids(entorno)
    inicio, fin = entorno.rangos[clave]

    for fecha in (inicio, fin):
        periodo = servicio.periodo_de(db, andino, fecha)
        assert (periodo.tipo, periodo.numero) == esperado


def test_periodo_de_fuera_de_todo_periodo_es_none(db, entorno):
    """No se aproxima (CU027): antes del PA1 y después del PR4 no hay periodo."""
    andino, _ = ids(entorno)

    assert servicio.periodo_de(db, andino, entorno.inicio("PA1") - timedelta(days=1)) is None
    assert servicio.periodo_de(db, andino, entorno.fin("PR4") + timedelta(days=1)) is None


def test_periodo_de_con_un_particular_que_difiere(db, entorno):
    andino, lejano = ids(entorno)
    particular_en_receso(db, entorno)

    assert servicio.periodo_actual(db, andino).nombre == "PA2"
    assert servicio.periodo_actual(db, lejano).nombre == "PR2"
    assert servicio.periodo_de(db, lejano, LUNES).nombre == "PA2"
    # El general sigue igual para todo lo que no es de un colegio.
    assert servicio.periodo_general_de(db, HOY).nombre == "PA2"


def test_un_particular_inactivo_no_aplica(db, entorno):
    _, lejano = ids(entorno)
    particular = particular_en_receso(db, entorno)
    particular.activo = False
    db.add(particular)
    db.commit()

    assert servicio.periodo_actual(db, lejano).nombre == "PA2"
    assert servicio.calendario_aplicable(db, lejano, entorno.anio.id_anio_escolar).id_colegio is None


def test_calendario_aplicable_y_periodos_del_anio(db, entorno):
    andino, lejano = ids(entorno)
    particular = particular_en_receso(db, entorno)
    anio = entorno.anio.id_anio_escolar

    assert servicio.calendario_aplicable(db, lejano, anio).id_calendario == particular.id_calendario
    assert servicio.calendario_aplicable(db, andino, anio).id_calendario == entorno.calendario.id_calendario
    del_particular = servicio.periodos_del_anio(db, lejano, anio)
    del_general = servicio.periodos_del_anio(db, None, anio)
    assert [p.nombre for p in del_particular] == ["PA1", "PR1", "PA2", "PR2", "PA3", "PR3", "PA4", "PR4"]
    assert (del_particular[2].fecha_fin, del_general[2].fecha_fin) == (LUNES, entorno.fin("vigente"))
    assert servicio.periodos_del_anio(db, None, 999) == []


def test_el_pr4_puede_pasar_al_anio_calendario_siguiente(db, entorno):
    andino, _ = ids(entorno)
    fijar_periodo(db, entorno.calendario, "PR4", fin=date(HOY.year + 1, 2, 28))

    assert servicio.periodo_de(db, andino, date(HOY.year + 1, 1, 15)).nombre == "PR4"


# ── Días lectivos ───────────────────────────────────────────────────────────────────

def test_es_lectivo(db, entorno):
    andino, _ = ids(entorno)
    agregar_no_lectivo(db, entorno.calendario, LUNES, motivo="Feriado regional")

    assert servicio.es_lectivo(db, andino, HOY) is True
    assert servicio.es_lectivo(db, andino, SABADO) is False
    assert servicio.es_lectivo(db, andino, LUNES) is False
    assert servicio.es_lectivo(db, andino, entorno.inicio("PR1") + timedelta(days=3)) is False
    assert servicio.es_lectivo(db, andino, entorno.fin("PR4") + timedelta(days=7)) is False


def test_un_dia_no_lectivo_de_otro_calendario_no_afecta(db, entorno):
    """El lunes es no lectivo solo en el general: Lejano (con particular, aún en PA2 ese
    lunes) lo tiene como lectivo; Andino no."""
    andino, lejano = ids(entorno)
    particular_en_receso(db, entorno)
    agregar_no_lectivo(db, entorno.calendario, LUNES)

    assert servicio.es_lectivo(db, lejano, LUNES) is True
    assert servicio.es_lectivo(db, andino, LUNES) is False


def test_dias_lectivos_con_un_rango_no_lectivo(db, entorno):
    andino, _ = ids(entorno)
    agregar_no_lectivo(db, entorno.calendario, LUNES, LUNES + timedelta(days=1), motivo="Suspensión")

    dias = servicio.dias_lectivos(db, andino, LUNES - timedelta(days=7), LUNES + timedelta(days=6))

    semana_pasada = [LUNES - timedelta(days=7 - i) for i in range(5)]
    assert dias == semana_pasada + [LUNES + timedelta(days=i) for i in (2, 3, 4)]


def test_dias_lectivos_no_cuentan_los_recesos(db, entorno):
    andino, _ = ids(entorno)

    assert servicio.dias_lectivos(db, andino, *entorno.rangos["PR1"]) == []


# ── Derivación de recesos (cargar-calendario y migración) ───────────────────────────

def test_derivar_recesos_cubre_los_huecos_y_el_pr4_llega_al_fin_del_anio():
    pas = [(date(2026, 4, 6), date(2026, 5, 29)), (date(2026, 6, 1), date(2026, 7, 24)),
           (date(2026, 8, 10), date(2026, 10, 16)), (date(2026, 10, 19), date(2026, 12, 11))]

    periodos = derivar_recesos(pas, date(2026, 12, 18))

    assert [(f"{p.tipo}{p.numero}", p.fecha_inicio, p.fecha_fin) for p in periodos if p.tipo == "PR"] == [
        ("PR1", date(2026, 5, 30), date(2026, 5, 31)), ("PR2", date(2026, 7, 25), date(2026, 8, 9)),
        ("PR3", date(2026, 10, 17), date(2026, 10, 18)), ("PR4", date(2026, 12, 12), date(2026, 12, 18)),
    ]


def test_derivar_recesos_sin_hueco_da_un_dia_y_corre_el_pa_siguiente():
    pas = [(date(2026, 4, 6), date(2026, 5, 29)), (date(2026, 5, 30), date(2026, 7, 24)),
           (date(2026, 8, 10), date(2026, 10, 16)), (date(2026, 10, 19), date(2026, 12, 18))]

    periodos = {f"{p.tipo}{p.numero}": (p.fecha_inicio, p.fecha_fin) for p in derivar_recesos(pas, date(2026, 12, 18))}

    assert periodos["PR1"] == (date(2026, 5, 30), date(2026, 5, 30))
    assert periodos["PA2"] == (date(2026, 5, 31), date(2026, 7, 24))
    # El PA4 termina el último día del año: el PR4 dura un día, en el año siguiente.
    assert periodos["PR4"] == (date(2026, 12, 19), date(2026, 12, 19))


# ── Alcance y actividad con dos calendarios el mismo día ────────────────────────────

def test_alcance_solo_cuenta_el_colegio_que_esta_en_un_pa(db, entorno):
    """El docente tiene asignaciones del PA2 en Andino (general, en PA2) y en Lejano
    (particular, en PR2 desde el martes): hoy su alcance es solo Andino."""
    asignar(db, entorno.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    particular_en_receso(db, entorno)

    assert {c for c, _ in alcance_docente(db, entorno.id_docente)} == {entorno.colegios["Andino"].id_colegio}


def test_actividad_solo_con_un_colegio_en_receso_no_inicia(client, db, entorno):
    """Su único colegio (Lejano) está en el PR2 aunque otros colegios estén en PA: no
    tiene asignaciones habilitadas."""
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    particular_en_receso(db, entorno)
    cabecera = cabecera_de(db, otro)

    respuesta = client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabecera)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "sin_asignaciones")


def test_actividad_con_un_colegio_en_pa_inicia(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    asignar(db, otro.id_docente, entorno.colegios["Andino"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    particular_en_receso(db, entorno)

    respuesta = client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabecera_de(db, otro))

    assert respuesta.status_code == 201


def test_feriado_automatico_del_general(db, entorno):
    """Los días no lectivos guardan su origen."""
    agregar_no_lectivo(db, entorno.calendario, LUNES, origen=ORIGEN_AUTOMATICO)

    general = servicio.cargar_calendarios(db).generales[entorno.anio.id_anio_escolar]

    assert [(r.fecha_inicio, r.origen) for r in general.no_lectivos] == [(LUNES, "automatico")]
