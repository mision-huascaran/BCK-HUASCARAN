"""Integración transversal (T10): vencidos e incompletos de los cuatro módulos en su borde
exacto, Inactividad Operativa computable, Sincronización Olvidada e Incumplimiento de
Sede con los cuatro módulos, Colegios Operando, Salud del Sistema, desglose de
actividades y los registros tocados por módulo (CU011, CU012, CU019-CU021)."""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.evaluacion import LecturaSemanalAlumno, RubricaMensual
from app.models.organizacion import ColegioGrado, DocenteColegioGrado
from app.services import registros_vencidos, salud_sistema
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario_gestion import _registros
from app.services.inicio_service import dias_computables_sin_actividad
from app.services.registros_vencidos import estado_de_registros
from tests.conftest import (
    a_las,
    asignar,
    cabecera_de,
    crear_docente,
    crear_particular,
    decision_de_vuelo,
    iniciar_actividad,
    motivo,
    rangos_relativos,
    registrar_asistencia,
    tabla_lectura,
)

# Miércoles. PA1 del 18/01 al sábado 18/04; PR1 del 19/04 al 17/05; PA2 del lunes 18/05 al
# 17/07; PA3 desde el 19/07. El Docente tiene asignación en PA1 y PA2 (1.º y 2.º del Andino).
HOY = date(2026, 6, 17)
LUNES_8, VIERNES_12, SABADO_13 = date(2026, 6, 8), date(2026, 6, 12), date(2026, 6, 13)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


def lunes_de_clase(db, entorno, grado="2.º") -> None:
    fila = db.get(ColegioGrado, (entorno.colegios["Andino"].id_colegio, entorno.grados[grado]))
    fila.dia_clase = 1
    db.add(fila)
    db.commit()


def del_modulo(registros, modulo) -> list:
    return [r for r in registros if r.modulo == modulo]


# ── Inactividad Operativa: conteo puro ──────────────────────────────────────────────

LUNES, MARTES, MIERCOLES, JUEVES, VIERNES = (date(2026, 6, 15 + i) for i in range(5))


@pytest.mark.parametrize(("dias", "desde", "hasta", "esperado"), [
    # 4 días exactos.
    ([[LUNES, MARTES, MIERCOLES, JUEVES]], LUNES, JUEVES, 4),
    # El fin de semana no es computable: viernes 12 + lunes a miércoles = 4.
    ([[VIERNES_12, LUNES, MARTES, MIERCOLES]], VIERNES_12, MIERCOLES, 4),
    # Un día no lectivo (el martes, ya excluido del calendario) deja 3.
    ([[LUNES, MIERCOLES, JUEVES]], LUNES, JUEVES, 3),
    # Dos colegios el mismo día: cuenta una vez.
    ([[LUNES, MARTES], [MARTES, MIERCOLES]], LUNES, MIERCOLES, 3),
    # Solo desde la habilitación o la última actividad (`desde`).
    ([[LUNES, MARTES, MIERCOLES, JUEVES]], MARTES, JUEVES, 3),
    # Un PR en medio no tiene días computables: nada que contar.
    ([[]], LUNES, VIERNES, 0),
])
def test_dias_computables_sin_actividad(dias, desde, hasta, esperado):
    assert dias_computables_sin_actividad(dias, desde, hasta) == esperado


def inactividad(client, db, entorno) -> list[str]:
    cuerpo = client.get("/inicio/supervisor", headers=cabecera_de(db, entorno.supervisor)).json()
    return [a["tipo"] for a in cuerpo["alertas"] if a["tipo"] == "inactividad_operativa"]


def test_inactividad_cuenta_desde_el_ultimo_inicio_de_actividad(client, db, entorno, miercoles):
    for fila in db.exec(select(DocenteColegioGrado)).all():
        fila.creado_en = a_las(date(2026, 6, 1), 8)
        db.add(fila)
    db.commit()
    assert inactividad(client, db, entorno) == ["inactividad_operativa"]

    miercoles.fijar(a_las(VIERNES_12, 9))
    cabecera = cabecera_de(db, entorno.docente)
    actividad = iniciar_actividad(client, cabecera)
    client.post(f"/actividades/{actividad['id']}/finalizar", json={}, headers=cabecera)
    miercoles.fijar(a_las(HOY, 9))

    # Desde el lunes 15: 2 días hábiles.
    assert inactividad(client, db, entorno) == []


def test_inactividad_no_arrastra_dias_de_un_pa_anterior_ni_cuenta_el_receso(client, db, entorno, miercoles):
    """Recién empezado el PA2 (martes 19/05) solo cuenta el lunes 18; en el PR1 nada,
    aunque tenga una retroactiva del PA1."""
    for fila in db.exec(select(DocenteColegioGrado)).all():
        fila.creado_en, fila.retroactiva = a_las(date(2026, 1, 5), 8), True
        db.add(fila)
    db.commit()
    miercoles.fijar(a_las(date(2026, 5, 19), 9))
    assert inactividad(client, db, entorno) == []

    miercoles.fijar(a_las(date(2026, 5, 6), 9))
    assert inactividad(client, db, entorno) == []


def test_inactividad_una_habilitacion_nueva_reinicia_la_cuenta(client, db, entorno):
    for fila in db.exec(select(DocenteColegioGrado)).all():
        fila.creado_en = a_las(date(2026, 6, 12), 8)
        db.add(fila)
    db.commit()

    # Habilitado el viernes 12: lunes 15 y martes 16 son 2 días.
    assert inactividad(client, db, entorno) == []


# ── Vencidos e incompletos por módulo, en su borde ──────────────────────────────────

def estado_en(miercoles, db, instante: datetime):
    miercoles.fijar(instante)
    return estado_de_registros(db)


def test_lectura_vence_el_sabado_a_las_00(db, entorno, miercoles):
    lunes_de_clase(db, entorno)
    semana = f"{LUNES_8.isoformat()}:{entorno.alumnos['ana'].id_programa_actual}"

    viernes = estado_en(miercoles, db, datetime(2026, 6, 12, 23, 59, tzinfo=tiempo.ZONA_LIMA))
    sabado = estado_en(miercoles, db, datetime(2026, 6, 13, 0, 0, tzinfo=tiempo.ZONA_LIMA))

    assert semana not in {r.referencia for r in del_modulo(viernes.vencidas, "lectura")}
    assert semana in {r.referencia for r in del_modulo(sabado.vencidas, "lectura")}


def test_lectura_tabla_pendiente_incompleta_completa_y_no_realizada(db, entorno, miercoles):
    lunes_de_clase(db, entorno)
    ana = entorno.alumnos["ana"]
    semanas = (date(2026, 5, 18), date(2026, 5, 25), date(2026, 6, 1), LUNES_8)
    registrar_asistencia(db, ana, semanas[1], "presente")
    tabla_lectura(db, ana, semanas[0])  # sin datos: Pendiente
    tabla_lectura(db, ana, semanas[1], cantidad_lsl=2)  # completada
    tabla_lectura(db, ana, semanas[2], estado="no_realizada", motivo="Paro")

    estado = estado_de_registros(db)

    assert [r.referencia[:10] for r in del_modulo(estado.vencidas, "lectura")] == [LUNES_8.isoformat()]
    assert [r.referencia[:10] for r in del_modulo(estado.incompletas, "lectura")] == ["2026-05-18"]
    # La fila completada consolida un registro del colegio en su PA vigente.
    assert entorno.colegios["Andino"].id_colegio in estado.colegios_con_registros


def test_rubrica_vence_el_dia_1_del_mes_siguiente(db, entorno, miercoles):
    ultimo_dia = estado_en(miercoles, db, datetime(2026, 5, 31, 23, 59, tzinfo=tiempo.ZONA_LIMA))
    dia_1 = estado_en(miercoles, db, datetime(2026, 6, 1, 0, 0, tzinfo=tiempo.ZONA_LIMA))

    assert del_modulo(ultimo_dia.vencidas, "rubrica") == []
    ana, luis = entorno.alumnos["ana"].id_alumno, entorno.alumnos["luis"].id_alumno
    assert {r.referencia for r in del_modulo(dia_1.vencidas, "rubrica")} == {f"5:{ana}", f"5:{luis}"}


def test_rubrica_incompleta_y_completa(db, entorno):
    ana, luis = entorno.alumnos["ana"], entorno.alumnos["luis"]
    for alumno, comprension in ((ana, None), (luis, 9)):
        db.add(RubricaMensual(
            id_alumno=alumno.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=5,
            id_colegio=alumno.id_colegio, id_grado=alumno.id_grado, id_seccion=alumno.id_seccion,
            id_programa=alumno.id_programa_actual, id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico,
            id_nivel_fluidez=11 if alumno is ana else 2, id_nivel_comprension=comprension,
            id_docente=entorno.id_docente, fecha_registro=a_las(HOY), creado_por=1, creado_en=a_las(HOY),
        ))
    db.commit()

    estado = estado_de_registros(db)

    assert [r.referencia for r in del_modulo(estado.incompletas, "rubrica")] == [f"5:{ana.id_alumno}"]
    assert f"5:{luis.id_alumno}" not in {r.referencia for r in del_modulo(estado.vencidas, "rubrica")}


def test_registro_de_vuelo_vence_el_dia_siguiente_al_fin_del_pa(db, entorno, miercoles):
    pa1 = entorno.periodos["pasado"].id_periodo_academico
    fin = estado_en(miercoles, db, a_las(entorno.fin("pasado"), 20))
    siguiente = estado_en(miercoles, db, a_las(entorno.fin("pasado") + timedelta(days=1), 0))

    assert del_modulo(fin.vencidas, "registro_vuelo") == []
    assert {r.referencia for r in del_modulo(siguiente.vencidas, "registro_vuelo")} == {
        f"{pa1}:{entorno.alumnos['luis'].id_alumno}", f"{pa1}:{entorno.alumnos['ana'].id_alumno}",
    }


def test_registro_de_vuelo_incompleto_sin_nivel_colocado_y_pa_en_curso_no_vence(db, entorno):
    pasado = entorno.periodos["pasado"]
    decision_de_vuelo(db, entorno.alumnos["ana"], pasado, None, id_programa=entorno.alumnos["ana"].id_programa_actual)
    decision_de_vuelo(db, entorno.alumnos["luis"], pasado, 4)

    estado = estado_de_registros(db)

    vencidas = del_modulo(estado.vencidas, "registro_vuelo")
    incompletas = del_modulo(estado.incompletas, "registro_vuelo")
    assert vencidas == []
    assert [r.referencia for r in incompletas] == [f"{pasado.id_periodo_academico}:{entorno.alumnos['ana'].id_alumno}"]
    assert all(r.referencia.split(":")[0] == str(pasado.id_periodo_academico) for r in incompletas)


def test_inicio_desglosa_los_vencidos_por_modulo(client, db, entorno):
    lunes_de_clase(db, entorno)
    cuerpo = client.get("/inicio/supervisor", headers=cabecera_de(db, entorno.supervisor)).json()

    por_modulo = cuerpo["vencidos_por_modulo"]
    assert set(por_modulo) == {"asistencias", "lectura", "rubrica", "registro_vuelo"}
    assert sum(por_modulo.values()) == cuerpo["registros_vencidos"]
    assert all(por_modulo[m] > 0 for m in por_modulo)
    assert sum(cuerpo["incompletos_por_modulo"].values()) == cuerpo["registros_incompletos"]


# ── Alertas con los cuatro módulos ──────────────────────────────────────────────────

def alertas(client, db, entorno) -> list[str]:
    cuerpo = client.get("/inicio/supervisor", headers=cabecera_de(db, entorno.supervisor)).json()
    return [a["tipo"] for a in cuerpo["alertas"]]


def test_sincronizacion_olvidada_por_vencidos_de_lectura(client, db, entorno, monkeypatch):
    monkeypatch.setattr(registros_vencidos, "EVALUADORES", {"lectura": registros_vencidos.lectura})
    assert "sincronizacion_olvidada" not in alertas(client, db, entorno)

    lunes_de_clase(db, entorno)

    assert "sincronizacion_olvidada" in alertas(client, db, entorno)


def test_incumplimiento_de_sede_cualquier_modulo_consolidado_lo_quita(client, db, entorno):
    assert "incumplimiento_sede" in alertas(client, db, entorno)

    decision_de_vuelo(db, entorno.alumnos["ana"], entorno.periodos["vigente"], 4)

    assert "incumplimiento_sede" not in alertas(client, db, entorno)


# ── CU012 ───────────────────────────────────────────────────────────────────────────

def operando(client, cabeceras) -> int:
    return client.get("/inicio/directivo", headers=cabeceras["Directivo"]).json()["colegios_operando"]


def test_colegios_operando_en_pr_intermedio_por_retroactiva(client, db, entorno, cabeceras):
    """Lejano en su PR2 (particular) sin asignación del PA3: opera con una retroactiva
    del PA2; sin ella, no."""
    rangos = rangos_relativos(HOY)
    rangos["PA2"] = (rangos["PA2"][0], HOY - timedelta(days=1))
    rangos["PR2"] = (HOY, rangos["PR2"][1])
    crear_particular(db, entorno, rangos)
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000020")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    assert operando(client, cabeceras) == 1

    fila = db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == otro.id_docente)).one()
    fila.retroactiva = True
    db.add(fila)
    db.commit()

    assert operando(client, cabeceras) == 2


def test_colegios_operando_necesita_un_docente_activo(client, db, entorno, cabeceras):
    entorno.docente.activo = False
    db.add(entorno.docente)
    db.commit()

    assert operando(client, cabeceras) == 0


def test_salud_del_sistema_suma_los_cuatro_contadores(db, entorno, monkeypatch):
    assert set(salud_sistema.CONTADORES) == {"asistencias", "lectura", "rubrica", "registro_vuelo"}
    llamados = []
    for nombre in list(salud_sistema.CONTADORES):
        monkeypatch.setitem(salud_sistema.CONTADORES, nombre, lambda _db, _pa, n=nombre: llamados.append(n) or 1)

    assert salud_sistema.salud_sistema(db) == 100
    assert sorted(llamados) == ["asistencias", "lectura", "registro_vuelo", "rubrica"]


# ── Calendario ──────────────────────────────────────────────────────────────────────

def test_el_impacto_del_calendario_cubre_los_cuatro_modulos(db, entorno):
    ana = entorno.alumnos["ana"]
    registrar_asistencia(db, ana, LUNES_8, "presente")
    tabla_lectura(db, ana, date(2026, 6, 1))
    decision_de_vuelo(db, ana, entorno.periodos["vigente"], 4)
    db.add(RubricaMensual(
        id_alumno=ana.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=5, id_colegio=ana.id_colegio,
        id_grado=ana.id_grado, id_seccion=ana.id_seccion, id_programa=ana.id_programa_actual,
        id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico, id_docente=entorno.id_docente,
        fecha_registro=a_las(HOY), creado_por=1, creado_en=a_las(HOY),
    ))
    db.commit()

    tipos = {r.tipo for r in _registros(db, {ana.id_colegio}, entorno.anio.id_anio_escolar)}

    assert tipos == {"asistencia", "lectura", "rubrica", "registro_vuelo"}


# ── CU019-CU021: desglose y registros por módulo ────────────────────────────────────

def auditar(db, entorno, actividad, tabla, id_registro, accion=CREAR, segundos=0):
    registrar_auditoria(
        db, tabla=tabla, id_registro=id_registro, accion=accion, id_usuario=entorno.docente.id_usuario,
        fecha=a_las(HOY) + timedelta(seconds=segundos), id_actividad=uuid.UUID(actividad["id"]),
        cambios={"campo": (None, "x")} if accion == EDITAR else None,
    )
    db.commit()


@pytest.fixture
def tocados(db, entorno, actividad) -> dict:
    """Una actividad que tocó una clase (2 alumnos), una tabla de lectura y su fila, una
    rúbrica (2 veces), un registro de vuelo y un alumno."""
    ana, luis = entorno.alumnos["ana"], entorno.alumnos["luis"]
    a1 = registrar_asistencia(db, ana, LUNES_8, "presente", lista=[ana, entorno.alumnos["inactivo"]])
    tabla = tabla_lectura(db, ana, date(2026, 6, 1), cantidad_lsl=1)
    rubrica = RubricaMensual(
        id_alumno=luis.id_alumno, id_anio_escolar=entorno.anio.id_anio_escolar, mes=5, id_colegio=luis.id_colegio,
        id_grado=luis.id_grado, id_seccion=luis.id_seccion, id_programa=luis.id_programa_actual,
        id_periodo_academico=entorno.periodos["vigente"].id_periodo_academico, id_docente=entorno.id_docente,
        fecha_registro=a_las(HOY), creado_por=1, creado_en=a_las(HOY),
    )
    db.add(rubrica)
    db.commit()
    vuelo = decision_de_vuelo(db, ana, entorno.periodos["vigente"], 4)
    auditar(db, entorno, actividad, "asistencia", a1.id)
    auditar(db, entorno, actividad, "lectura_semanal", tabla.id)
    auditar(db, entorno, actividad, "lectura_semanal_alumno", db.exec(select(LecturaSemanalAlumno)).one().id, EDITAR)
    auditar(db, entorno, actividad, "rubrica_mensual", rubrica.id)
    auditar(db, entorno, actividad, "rubrica_mensual", rubrica.id, EDITAR, segundos=60)
    auditar(db, entorno, actividad, "registro_vuelo", vuelo.id)
    auditar(db, entorno, actividad, "alumno", luis.id_alumno, EDITAR)
    return {"actividad": actividad["id"], "tabla": tabla.id, "rubrica": rubrica.id, "vuelo": vuelo.id,
            "clase": a1.id_clase}


def test_desglose_por_modulo_suma_el_total(client, cabeceras, tocados):
    detalle = client.get(f"/actividades/{tocados['actividad']}", headers=cabeceras["Docente"]).json()

    assert sum(m["total"] for m in detalle["cambios_por_modulo"]) == detalle["cambios"] == 6
    assert {m["modulo"] for m in detalle["cambios_por_modulo"]} == {
        "asistencia", "seguimiento_lectura", "rubrica", "registro_vuelo", "alumnos",
    }


def registros(client, cabecera, entorno, id_actividad, modulo, id_docente=None):
    url = f"/seguimiento/docentes/{id_docente or entorno.id_docente}/actividades/{id_actividad}/registros"
    return client.get(url, params={"modulo": modulo}, headers=cabecera)


@pytest.mark.parametrize(("modulo", "clave", "cambios", "extra"), [
    ("asistencias", "clase", 1, {"fecha": LUNES_8.isoformat()}),
    ("lectura", "tabla", 2, {"semana_inicio": "2026-06-01", "mes": 6}),
    ("rubrica", "rubrica", 2, {"mes": 5}),
    ("registro_vuelo", "vuelo", 1, {}),
])
def test_registros_de_la_actividad_por_modulo(client, entorno, cabeceras, tocados, modulo, clave, cambios, extra):
    cuerpo = registros(client, cabeceras["Supervisor"], entorno, tocados["actividad"], modulo).json()

    [item] = cuerpo["items"]
    assert (item["modulo"], item["id"], item["cambios"], cuerpo["total"], cuerpo["page_size"]) == (
        modulo, tocados[clave], cambios, 1, 40,
    )
    assert item["colegio"]["nombre"] == "Colegio Andino"
    assert {k: item[k] for k in extra} == extra


def test_registros_de_alumnos_y_contexto(client, entorno, cabeceras, tocados):
    [alumno] = registros(client, cabeceras["Supervisor"], entorno, tocados["actividad"], "alumnos").json()["items"]
    [rubrica] = registros(client, cabeceras["Supervisor"], entorno, tocados["actividad"], "rubrica").json()["items"]

    assert (alumno["alumno"]["nombre"], alumno["grado"]["nombre"]) == ("Luis Pérez Quispe", "1.º")
    assert (rubrica["alumno"]["nombre"], rubrica["programa"]["nombre"]) == ("Luis Pérez Quispe", "Alfabetización")


def test_registros_de_la_actividad_errores(client, db, entorno, cabeceras, tocados):
    actividad = tocados["actividad"]
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000030")
    db.commit()

    assert registros(client, {}, entorno, actividad, "lectura").status_code == 401
    assert registros(client, cabeceras["Docente"], entorno, actividad, "lectura").status_code == 403
    assert registros(client, cabeceras["Directivo"], entorno, actividad, "lectura").status_code == 403
    sin_docente = registros(client, cabeceras["Supervisor"], entorno, actividad, "lectura", id_docente=999)
    ajena = registros(client, cabeceras["Supervisor"], entorno, actividad, "lectura", id_docente=otro.id_docente)
    inexistente = registros(client, cabeceras["Supervisor"], entorno, uuid.uuid4(), "lectura")
    invalido = registros(client, cabeceras["Supervisor"], entorno, actividad, "otro")
    assert (sin_docente.status_code, motivo(sin_docente)) == (404, "docente_no_encontrado")
    assert (ajena.status_code, motivo(ajena)) == (404, "actividad_no_encontrada")
    assert (inexistente.status_code, motivo(inexistente)) == (404, "actividad_no_encontrada")
    assert (invalido.status_code, motivo(invalido)) == (422, "validacion")
