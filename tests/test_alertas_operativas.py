"""Registros vencidos e incompletos y alertas operativas del Inicio del Supervisor (CU011)."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.evaluacion import Asistencia
from app.models.organizacion import ColegioGrado, DiaNoLaborable, DocenteColegioGrado
from app.services.registros_vencidos import estado_de_registros
from tests.conftest import a_las, asignar, cabecera_de, crear_alumno, crear_docente

# Miércoles. El periodo vigente empieza el lunes 18 de mayo: hasta ayer hubo 5 lunes
# (18 y 25 de mayo; 1, 8 y 15 de junio) y 5 martes.
HOY = date(2026, 6, 17)
LUNES, MARTES = date(2026, 6, 15), date(2026, 6, 16)
SINCRONIZACION = "Sincronización Olvidada - Rosa Quispe: Registros vencidos, último inicio de sesión hace más de 3 días."
INACTIVIDAD = "Inactividad Operativa - Rosa Quispe: Sin actividad registrada en los últimos 4 días hábiles."
SEDE = "Incumplimiento de Sede - Colegio Colegio Andino: Sin registros obligatorios en el periodo académico actual."


@pytest.fixture(autouse=True)
def miercoles(reloj):
    """Fija el reloj antes de armar el entorno: el calendario es relativo a hoy."""
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture
def sup(db, entorno) -> dict[str, str]:
    """Solo la Supervisora inicia sesión: la Docente queda sin inicios de sesión."""
    return cabecera_de(db, entorno.supervisor)


def dias_de_clase(db, entorno, colegio="Andino", **dias) -> None:
    """dias_de_clase(db, entorno, primero=1) -> 1.º del colegio los lunes."""
    nombres = {"primero": "1.º", "segundo": "2.º", "tercero": "3.º"}
    for clave, dia in dias.items():
        fila = db.get(ColegioGrado, (entorno.colegios[colegio].id_colegio, entorno.grados[nombres[clave]]))
        fila.dia_clase = dia
        db.add(fila)
    db.commit()


def registrar(db, entorno, alumno, fecha, estado="presente") -> None:
    """Registro en el aula actual del alumno, como lo crea PUT /asistencias."""
    alumno = entorno.alumnos[alumno] if isinstance(alumno, str) else alumno
    ahora = ahora_utc()
    db.add(Asistencia(
        id_alumno=alumno.id_alumno, fecha=fecha, id_programa=alumno.id_programa_actual, estado=estado,
        id_colegio=alumno.id_colegio, id_grado=alumno.id_grado,
        id_docente=entorno.id_docente, fecha_registro=ahora, creado_por=1, creado_en=ahora,
    ))
    db.commit()


def inicio(client, cabecera) -> dict:
    return client.get("/inicio/supervisor", headers=cabecera).json()


def indicadores(client, cabecera) -> tuple[int, int]:
    cuerpo = inicio(client, cabecera)
    return cuerpo["registros_vencidos"], cuerpo["registros_incompletos"]


def asignado_desde(db, entorno, dia: date) -> None:
    vigente = entorno.periodos["vigente"].id_periodo_academico
    for fila in db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_periodo_academico == vigente)).all():
        fila.creado_en = a_las(dia, 8)
        db.add(fila)
    db.commit()


# ── Vencidos e incompletos ──────────────────────────────────────────────────────────

def test_cu011_sin_dia_de_clase_no_vence_nada(client, entorno, sup):
    assert indicadores(client, sup) == (0, 0)


def test_cu011_cada_clase_pasada_sin_registros_vence(client, db, entorno, sup):
    dias_de_clase(db, entorno, primero=1, segundo=2)

    assert indicadores(client, sup) == (10, 0)


def test_cu011_un_feriado_no_vence_y_la_clase_de_hoy_aun_no(client, db, entorno, sup):
    """1.º los miércoles: vencen los del 20 y 27 de mayo y 3 y 10 de junio (hoy, no)."""
    dias_de_clase(db, entorno, primero=3)
    assert indicadores(client, sup) == (4, 0)

    db.add(DiaNoLaborable(fecha=date(2026, 6, 10), motivo="Feriado", creado_por=1, creado_en=ahora_utc()))
    db.commit()

    assert indicadores(client, sup) == (3, 0)


def test_cu011_un_registro_cumple_la_clase_y_uno_a_medias_la_deja_incompleta(client, db, entorno, sup):
    dias_de_clase(db, entorno, primero=1, segundo=2)
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["1.º"],
                        entorno.programas["Alfabetización"], entorno.supervisor.id_usuario)
    db.commit()
    registrar(db, entorno, "luis", LUNES)
    registrar(db, entorno, "ana", MARTES)

    assert indicadores(client, sup) == (8, 1)  # al 1.º del lunes le falta Beto

    registrar(db, entorno, beto, LUNES, estado=None)  # solo con observación: sigue incompleta
    assert indicadores(client, sup) == (8, 1)


def test_cu011_la_clase_de_hoy_a_medias_ya_es_incompleta(client, db, entorno, sup):
    dias_de_clase(db, entorno, primero=3)
    crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["1.º"],
                 entorno.programas["Alfabetización"], entorno.supervisor.id_usuario)
    db.commit()
    registrar(db, entorno, "luis", HOY)

    assert indicadores(client, sup) == (4, 1)


def test_cu011_los_inactivos_no_se_exigen(client, db, entorno, sup):
    """Iván (2.º) está inactivo: con Ana marcada, la clase está completa."""
    dias_de_clase(db, entorno, segundo=2)
    registrar(db, entorno, "ana", MARTES)

    assert indicadores(client, sup) == (4, 0)


def test_cu011_solo_se_exigen_grados_con_docente_alumnos_y_colegio_activo(client, db, entorno, sup):
    """Lejano 3.º no tiene docente; Andino 3.º tiene docente pero ningún alumno."""
    dias_de_clase(db, entorno, colegio="Lejano", tercero=1)
    dias_de_clase(db, entorno, tercero=1)
    asignar(db, entorno.id_docente, entorno.colegios["Andino"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    assert indicadores(client, sup) == (0, 0)

    dias_de_clase(db, entorno, primero=1)
    assert indicadores(client, sup) == (5, 0)
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()
    assert indicadores(client, sup) == (0, 0)


def test_cu011_sin_periodo_vigente_no_hay_vencidos(db, entorno):
    dias_de_clase(db, entorno, primero=1)
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = HOY - timedelta(days=1)
    db.add(vigente)
    db.commit()

    estado = estado_de_registros(db)

    assert (estado.vencidas, estado.incompletas, estado.colegios_con_registros) == ([], [], set())


def rotar(db, entorno, alumno, colegio="Andino", grado="2.º") -> None:
    """El Supervisor cambia de colegio o de grado al alumno (CU014)."""
    alumno = entorno.alumnos[alumno] if isinstance(alumno, str) else alumno
    alumno.id_colegio, alumno.id_grado = entorno.colegios[colegio].id_colegio, entorno.grados[grado]
    db.add(alumno)
    db.commit()


@pytest.mark.parametrize("colegio, grado", [("Andino", "2.º"), ("Lejano", "1.º")], ids=["de_grado", "de_colegio"])
def test_cu011_rotar_a_un_alumno_no_vuelve_vencida_ni_incompleta_el_aula_original(client, db, entorno, sup,
                                                                                    colegio, grado):
    """Luis y Beto (1.º) se registraron el lunes. Luis rota: su registro sigue en 1.º, que
    queda con la clase del lunes completa (Beto) y solo los 4 lunes anteriores vencidos."""
    dias_de_clase(db, entorno, primero=1)
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["1.º"],
                        entorno.programas["Alfabetización"], entorno.supervisor.id_usuario)
    db.commit()
    registrar(db, entorno, "luis", LUNES)
    registrar(db, entorno, beto, LUNES)
    assert indicadores(client, sup) == (4, 0)

    rotar(db, entorno, "luis", colegio, grado)

    assert indicadores(client, sup) == (4, 0)
    assert db.exec(select(Asistencia.id_colegio, Asistencia.id_grado).where(
        Asistencia.id_alumno == entorno.alumnos["luis"].id_alumno)).one() == (
        entorno.colegios["Andino"].id_colegio, entorno.grados["1.º"])


def test_cu011_quien_rota_ya_registrado_ese_dia_no_se_exige_en_su_aula_nueva(client, db, entorno, sup):
    """1.º y 2.º los lunes. Luis se registró el lunes en 1.º y después pasó a 2.º: la clase
    del lunes de 2.º está completa con Ana, y la de 1.º con el registro de Luis."""
    dias_de_clase(db, entorno, primero=1, segundo=1)
    registrar(db, entorno, "luis", LUNES)
    registrar(db, entorno, "ana", LUNES)
    rotar(db, entorno, "luis", grado="2.º")

    # 1.º se queda sin alumnos activos: ya no se exige. 2.º: los 4 lunes anteriores.
    assert indicadores(client, sup) == (4, 0)


def test_cu011_un_registro_sin_estado_deja_incompleta_su_clase_aunque_el_alumno_rote(client, db, entorno, sup):
    dias_de_clase(db, entorno, primero=1)
    beto = crear_alumno(db, "Beto", "Abad", entorno.colegios["Andino"], entorno.grados["1.º"],
                        entorno.programas["Alfabetización"], entorno.supervisor.id_usuario)
    db.commit()
    registrar(db, entorno, "luis", LUNES, estado=None)
    registrar(db, entorno, beto, LUNES)

    rotar(db, entorno, "luis", grado="2.º")

    assert indicadores(client, sup) == (4, 1)


def test_cu011_los_vencidos_miran_el_aula_guardada_en_el_registro(client, db, entorno, sup):
    """Un registro de 2.º no cumple la clase de 1.º aunque su alumno hoy esté en 1.º."""
    dias_de_clase(db, entorno, primero=1)
    registrar(db, entorno, "ana", LUNES)
    rotar(db, entorno, "ana", grado="1.º")

    # Los 5 lunes de 1.º vencidos: el registro de Ana es de 2.º.
    assert indicadores(client, sup) == (5, 0)


# ── Alertas operativas ──────────────────────────────────────────────────────────────

def alertas(client, cabecera) -> list[dict]:
    return inicio(client, cabecera)["alertas"]


def test_cu011_las_tres_alertas_en_su_orden(client, db, entorno, sup):
    """Rosa: vencidos sin haber iniciado sesión nunca, asignada desde el 1 de junio sin
    actividad, y el Andino sin ningún registro en el periodo."""
    asignado_desde(db, entorno, date(2026, 6, 1))
    dias_de_clase(db, entorno, primero=1)

    assert alertas(client, sup) == [
        {"tipo": "sincronizacion_olvidada", "mensaje": SINCRONIZACION, "critica": False},
        {"tipo": "inactividad_operativa", "mensaje": INACTIVIDAD, "critica": False},
        {"tipo": "incumplimiento_sede", "mensaje": SEDE, "critica": True},
    ]


@pytest.mark.parametrize(
    "hace, alerta", [(timedelta(hours=73), True), (timedelta(hours=71), False)], ids=["mas_de_72_h", "menos_de_72_h"]
)
def test_cu011_sincronizacion_olvidada_por_el_ultimo_inicio_de_sesion(client, db, entorno, sup, miercoles, hace, alerta):
    dias_de_clase(db, entorno, primero=1)
    miercoles.fijar(a_las(HOY, 9) - hace)
    cabecera_de(db, entorno.docente)
    miercoles.fijar(a_las(HOY, 9))

    tipos = [a["tipo"] for a in alertas(client, sup)]

    assert ("sincronizacion_olvidada" in tipos) is alerta


def test_cu011_sin_vencidos_no_hay_sincronizacion_olvidada_ni_incumplimiento(client, db, entorno, sup):
    asignado_desde(db, entorno, date(2026, 6, 1))

    assert [a["tipo"] for a in alertas(client, sup)] == ["inactividad_operativa"]


def test_cu011_un_registro_en_el_periodo_quita_el_incumplimiento_de_sede(client, db, entorno, sup):
    dias_de_clase(db, entorno, primero=1)
    registrar(db, entorno, "ana", date(2026, 5, 20))  # 2.º no tiene día: igual cuenta como registro del colegio

    tipos = [a["tipo"] for a in alertas(client, sup)]

    assert "incumplimiento_sede" not in tipos
    assert "sincronizacion_olvidada" in tipos


def test_cu011_sincronizacion_olvidada_solo_del_docente_de_los_grados_vencidos(client, db, entorno, sup):
    otro = crear_docente(db, entorno, "Beto", "beto@sicedu.test", "70000002")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    dias_de_clase(db, entorno, colegio="Lejano", tercero=2)

    mensajes = [a["mensaje"] for a in alertas(client, sup) if a["tipo"] != "inactividad_operativa"]

    assert mensajes == [
        "Sincronización Olvidada - Beto Prueba: Registros vencidos, último inicio de sesión hace más de 3 días.",
        "Incumplimiento de Sede - Colegio Colegio Lejano: Sin registros obligatorios en el periodo académico actual.",
    ]


def test_cu011_el_incumplimiento_de_sede_mira_el_colegio_guardado_en_el_registro(client, db, entorno, sup):
    """Luis se registró en el Andino y rotó al Lejano (con vencidos): su registro no
    cuenta para el Lejano, que sigue sin registros en el periodo."""
    otro = crear_docente(db, entorno, "Beto", "beto@sicedu.test", "70000002")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    dias_de_clase(db, entorno, colegio="Lejano", tercero=2)
    registrar(db, entorno, "luis", LUNES)

    rotar(db, entorno, "luis", colegio="Lejano", grado="3.º")

    sedes = [a["mensaje"] for a in alertas(client, sup) if a["tipo"] == "incumplimiento_sede"]
    assert sedes == [
        "Incumplimiento de Sede - Colegio Colegio Lejano: Sin registros obligatorios en el periodo académico actual."
    ]
