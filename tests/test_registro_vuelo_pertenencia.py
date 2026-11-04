"""Registro de Vuelo: pertenencia por PA (S27 y S28).

Cada PA de un alumno dice si pertenecía al grupo algún día del PA (`pertenece`), con la
misma regla con la que se valida el guardado (`alumno_fuera_del_grupo`). Un PA en que no
pertenecía es de solo lectura, no es incompleto y no se exige en el Inicio (CU011)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.models.organizacion import AlumnoPertenencia, PeriodoAcademico
from app.services import pertenencia, registro_vuelo
from app.services.registro_vuelo import AVISO_ENTRADA_CAMBIADA
from app.services.registros_vencidos import REGISTRO_VUELO, estado_de_registros
from tests.conftest import a_las, crear_alumno, crear_permiso, decision_de_vuelo, seccion_de
from tests.test_registro_vuelo import C, HOY, contexto, guardar, item, listar

FUERA_DEL_GRUPO = "El estudiante no pertenecía a este grupo en el periodo seleccionado."
DIA = timedelta(days=1)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def _inicio(entorno, pa: str) -> date:
    return entorno.rangos[pa][0]


def _en_segundo(db, entorno, clave, nombre, desde, hasta=None, colegio="Andino"):
    """Alumno de 2.º (Comprensión Lectora) en el colegio, desde `desde` y, con `hasta`,
    hasta ese día (exclusivo)."""
    alumno = crear_alumno(
        db, nombre, "Prueba", entorno.colegios[colegio], entorno.grados["2.º"],
        entorno.programas["Comprensión Lectora"], 1, desde=desde,
    )
    if hasta is not None:
        intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == alumno.id_alumno)).one()
        intervalo.hasta = hasta
        db.add(intervalo)
    db.commit()
    entorno.alumnos[clave] = alumno
    return alumno


def _trasladar(db, entorno, alumno, fecha, colegio="Andino"):
    """Cierra el intervalo vigente del alumno en `fecha` y abre otro en 2.º del colegio."""
    vigente = db.exec(select(AlumnoPertenencia).where(
        AlumnoPertenencia.id_alumno == alumno.id_alumno, AlumnoPertenencia.hasta.is_(None)
    )).one()
    vigente.hasta = fecha
    destino = entorno.colegios[colegio]
    seccion = seccion_de(db, destino, entorno.grados["2.º"])
    db.add(vigente)
    db.add(AlumnoPertenencia(
        id_alumno=alumno.id_alumno, id_colegio=destino.id_colegio, id_grado=entorno.grados["2.º"],
        id_seccion=seccion.id_seccion, id_programa=vigente.id_programa, desde=fecha, creado_por=1,
        creado_en=vigente.creado_en,
    ))
    alumno.id_colegio, alumno.id_seccion = destino.id_colegio, seccion.id_seccion
    db.add(alumno)
    db.commit()


def _periodos(client, cabecera, entorno, clave, grupo_de="ana") -> list[dict]:
    """Los PA del alumno `clave` en el listado del grupo de `grupo_de`."""
    cuerpo = listar(client, cabecera, entorno, grupo_de).json()
    [fila] = [i for i in cuerpo["items"] if i["id_alumno"] == entorno.alumnos[clave].id_alumno]
    return fila["periodos"]


def _en_grupo_de_ana(entorno, clave, periodo, **campos) -> dict:
    return {**item(entorno, clave, periodo=periodo, **campos), **contexto(entorno.alumnos["ana"])}


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def test_entra_en_pa2_el_pa1_es_de_solo_lectura(client, db, entorno, sup):
    _en_segundo(db, entorno, "nuevo", "Nora", _inicio(entorno, "PA2") + 5 * DIA)

    pa1, pa2, *_ = _periodos(client, sup, entorno, "nuevo")

    assert (pa1["pertenece"], pa1["aviso"], pa1["incompleto"]) == (False, FUERA_DEL_GRUPO, False)
    assert (pa2["pertenece"], pa2["aviso"], pa2["incompleto"]) == (True, None, True)


def test_sale_a_mitad_del_pa3(client, db, entorno, sup):
    _en_segundo(db, entorno, "sale", "Sara", entorno.anio.fecha_inicio, hasta=_inicio(entorno, "PA3") + 10 * DIA)

    pa1, pa2, pa3, pa4 = _periodos(client, sup, entorno, "sale")

    assert [p["pertenece"] for p in (pa1, pa2, pa3, pa4)] == [True, True, True, False]
    assert (pa4["aviso"], pa4["incompleto"]) == (FUERA_DEL_GRUPO, False)


def test_sale_el_dia_que_empieza_el_pa_no_pertenece_en_ese_pa(client, db, entorno, sup):
    """`hasta` es exclusivo: salir el primer día del PA3 es no haber estado en él."""
    _en_segundo(db, entorno, "sale", "Sara", entorno.anio.fecha_inicio, hasta=_inicio(entorno, "PA3"))

    pertenece = [p["pertenece"] for p in _periodos(client, sup, entorno, "sale")]

    assert pertenece == [True, True, False, False]


def test_trasladado_de_otro_colegio(client, db, entorno, sup):
    alumno = _en_segundo(db, entorno, "trasladado", "Teo", entorno.anio.fecha_inicio, colegio="Lejano")
    _trasladar(db, entorno, alumno, _inicio(entorno, "PA2") + 5 * DIA)

    en_andino = _periodos(client, sup, entorno, "trasladado")

    assert [p["pertenece"] for p in en_andino] == [False, True, True, True]
    assert (en_andino[0]["aviso"], en_andino[0]["incompleto"]) == (FUERA_DEL_GRUPO, False)


def test_el_grupo_anterior_del_trasladado_ya_no_le_ofrece_los_pa_posteriores(client, db, entorno, sup):
    alumno = _en_segundo(db, entorno, "trasladado", "Teo", entorno.anio.fecha_inicio, colegio="Lejano")
    lejano = contexto(alumno)
    _trasladar(db, entorno, alumno, _inicio(entorno, "PA2") + 5 * DIA)

    cuerpo = client.get("/registro-vuelo", params={**lejano, "id_anio_escolar": entorno.anio.id_anio_escolar},
                        headers=sup).json()
    [fila] = cuerpo["items"]

    assert [p["pertenece"] for p in fila["periodos"]] == [True, True, False, False]


def test_registro_previo_en_un_pa_sin_pertenencia_se_sigue_mostrando(client, db, entorno, sup):
    """Una fila del grupo en un PA en que hoy el alumno no pertenecía (su pertenencia se
    corrigió después) se sigue mostrando. Si ya hay otro aviso (la entrada cambió), se deja
    ese."""
    alumno = _en_segundo(db, entorno, "nuevo", "Nora", _inicio(entorno, "PA2") + 5 * DIA)
    decision_de_vuelo(db, alumno, entorno.periodos["pasado"], C)

    pa1, *_ = _periodos(client, sup, entorno, "nuevo")

    assert (pa1["pertenece"], pa1["incompleto"], pa1["nivel_colocado"]["letra"]) == (False, False, "C")
    assert (pa1["aviso_entrada_cambio"], pa1["aviso"]) == (True, AVISO_ENTRADA_CAMBIADA)


def test_sin_calendario_ningun_pa_pertenece(entorno):
    datos = registro_vuelo.DatosVuelo(
        anio=entorno.anio, periodos=[entorno.periodos["pasado"]], letras={}, entrada_por_grado={}, escalas={},
        niveles_rubrica={}, programas={},
    )

    assert datos.pertenece(entorno.alumnos["ana"].id_alumno, entorno.periodos["pasado"]) is False


def test_pa_sin_fechas_en_el_calendario_no_avisa_fuera_del_grupo(client, db, entorno, sup):
    """Un PA del año sin fechas en el calendario del colegio: `pertenece` e `incompleto` en
    false, sin el aviso de "no pertenecía" (el motivo ya lo da `periodos[].escritura`)."""
    db.add(PeriodoAcademico(id_anio_escolar=entorno.anio.id_anio_escolar, numero=5, creado_por=1, creado_en=a_las(HOY)))
    db.commit()

    cuerpo = listar(client, sup, entorno).json()
    [ana] = cuerpo["items"]
    sin_fechas = ana["periodos"][4]

    assert (sin_fechas["numero"], sin_fechas["pertenece"], sin_fechas["incompleto"], sin_fechas["aviso"]) == (
        5, False, False, None,
    )
    assert (cuerpo["periodos"][4]["fecha_inicio"], cuerpo["periodos"][4]["escritura"]["permitida"]) == (None, False)
    assert [p["pertenece"] for p in ana["periodos"][:4]] == [True] * 4


# ── Escritura y coherencia ──────────────────────────────────────────────────────────

def test_guardar_en_un_pa_sin_pertenencia_se_rechaza(client, db, entorno, doc, actividad):
    _en_segundo(db, entorno, "nuevo", "Nora", _inicio(entorno, "PA2") + 5 * DIA)
    crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="registro_vuelo")

    [pa1, pa2] = guardar(client, doc, item(entorno, "nuevo", periodo="pasado", id_nivel_colocado=C),
                         item(entorno, "nuevo", periodo="vigente", id_nivel_colocado=C)).json()

    assert (pa1["estado"], pa1["motivo"], pa1["detail"]) == ("rechazada", "alumno_fuera_del_grupo", FUERA_DEL_GRUPO)
    assert pa2["estado"] == "aceptada", pa2
    assert pa2["registro"]["pertenece"] is True


def test_pertenece_coincide_con_lo_que_acepta_el_guardado(client, db, entorno, doc, actividad):
    """Para cada alumno del grupo y cada PA en que el Docente puede escribir (PA1 con
    permiso y PA2 en curso), `pertenece` es true si y solo si el guardado no se rechaza por
    `alumno_fuera_del_grupo`."""
    _en_segundo(db, entorno, "nuevo", "Nora", _inicio(entorno, "PA2") + 5 * DIA)
    _en_segundo(db, entorno, "sale", "Sara", entorno.anio.fecha_inicio, hasta=_inicio(entorno, "PA2"))
    _en_segundo(db, entorno, "breve", "Bea", _inicio(entorno, "PA1") + 3 * DIA, hasta=_inicio(entorno, "PA1") + 4 * DIA)
    trasladado = _en_segundo(db, entorno, "trasladado", "Teo", entorno.anio.fecha_inicio, colegio="Lejano")
    _trasladar(db, entorno, trasladado, _inicio(entorno, "PA1") + 10 * DIA)
    crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="registro_vuelo")

    filas = listar(client, doc, entorno).json()["items"]
    lectura, escritura = {}, {}
    for fila in filas:
        for periodo in fila["periodos"][:2]:
            clave = (fila["id_alumno"], periodo["id_periodo_academico"])
            lectura[clave] = periodo["pertenece"]
            [resultado] = guardar(client, doc, {
                **_en_grupo_de_ana(entorno, "ana", "pasado", id_nivel_colocado=C),
                "id_alumno": fila["id_alumno"], "id_periodo_academico": periodo["id_periodo_academico"],
            }).json()
            assert resultado["estado"] == "aceptada" or resultado["motivo"] == "alumno_fuera_del_grupo", resultado
            escritura[clave] = resultado["estado"] == "aceptada"

    assert len(filas) == 5
    assert lectura == escritura
    assert set(lectura.values()) == {True, False}


# ── Inicio (CU011) ──────────────────────────────────────────────────────────────────

def test_inicio_no_exige_el_pa_en_que_no_pertenecia(db, entorno):
    """Vencidos del PA1 (ya terminado): se exige a quien pertenecía al grupo en el PA1, no
    a quien entró después."""
    nuevo = _en_segundo(db, entorno, "nuevo", "Nora", _inicio(entorno, "PA2") + 5 * DIA)
    ana = entorno.alumnos["ana"]

    vencidas = {o.referencia for o in estado_de_registros(db).vencidas if o.modulo == REGISTRO_VUELO}

    pa1 = entorno.periodos["pasado"].id_periodo_academico
    assert f"{pa1}:{ana.id_alumno}" in vencidas
    assert f"{pa1}:{nuevo.id_alumno}" not in vencidas


# ── Regla y consultas ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("desde", "hasta", "esperado"), [
    (date(2026, 1, 1), None, True),
    (date(2026, 3, 31), None, True),
    (date(2026, 4, 1), None, False),
    (date(2026, 1, 1), date(2026, 3, 1), False),
    (date(2026, 1, 1), date(2026, 3, 2), True),
    (date(2026, 3, 10), date(2026, 3, 11), True),
])
def test_regla_de_pertenencia_en_un_rango(desde, hasta, esperado):
    intervalo = AlumnoPertenencia(id_alumno=1, id_colegio=1, id_grado=1, id_seccion=1, id_programa=1,
                                  desde=desde, hasta=hasta)

    assert pertenencia.pertenecio_en_rango([intervalo], date(2026, 3, 1), date(2026, 3, 31)) is esperado


def test_sin_intervalos_no_pertenece():
    assert pertenencia.pertenecio_en_rango([], date(2026, 3, 1), date(2026, 3, 31)) is False


def _consultas_del_listado(client, db, entorno, sup) -> int:
    contador = {"n": 0}

    def contar(*_):
        contador["n"] += 1

    motor = db.get_bind()
    event.listen(motor, "before_cursor_execute", contar)
    try:
        assert listar(client, sup, entorno).status_code == 200
    finally:
        event.remove(motor, "before_cursor_execute", contar)
    return contador["n"]


def test_la_pertenencia_no_agrega_consultas_por_alumno(client, db, entorno, sup):
    """Un número fijo de consultas por página: la pertenencia se carga una vez para todos."""
    _consultas_del_listado(client, db, entorno, sup)  # la primera petición prepara cachés
    con_uno = _consultas_del_listado(client, db, entorno, sup)
    for numero in range(4):
        _en_segundo(db, entorno, f"extra{numero}", f"Extra{numero}", _inicio(entorno, "PA2") + numero * DIA)
    _consultas_del_listado(client, db, entorno, sup)  # recarga lo que el commit dejó vencido

    con_cinco = _consultas_del_listado(client, db, entorno, sup)

    assert con_cinco == con_uno
