"""Rúbrica mensual (CU029, CU030, T8): listado por grupo y mes (R1, R2), registro por
operaciones con control de versión, criterios, catálogo, descriptores y sus
integraciones (Salud del Sistema, productividad, impacto del calendario y detalle del
alumno)."""
import uuid
from datetime import date

import pytest
from sqlmodel import select

from app.cli.catalogos import cargar_catalogos
from app.models.evaluacion import DescriptorRubrica, NivelRubrica, RubricaMensual
from app.models.organizacion import AlumnoPertenencia, CicloEbr, DocenteColegioGrado
from app.models.trazabilidad import Auditoria
from app.services import cambios, rubrica_service
from app.services.calendario import pa_actual_de_colegios
from app.services.registros_vencidos import estado_de_registros
from tests.conftest import (
    a_las,
    agregar_no_lectivo,
    asignar,
    cabecera_de,
    crear_alumno,
    crear_docente,
    crear_permiso,
    fijar_periodo,
    iniciar_actividad,
    motivo,
    rangos_relativos,
    seccion_de,
)
from tests.test_calendario_api import editar

# Miércoles. El PA1 del general va del 18 de enero al 18 de abril; el PR1, del 19 de
# abril al 17 de mayo; el PA2, del lunes 18 de mayo al viernes 17 de julio.
HOY = date(2026, 6, 17)
JUNIO, MAYO, ABRIL, JULIO = 6, 5, 4, 7
CL, ALFA = "Comprensión Lectora", "Alfabetización"
MENSAJE_VALOR = "Selecciona una valoración permitida para esta dimensión."
MENSAJE_EN_OTRO_GRUPO = (
    "Este alumno ya tiene una evaluación registrada para este mes. Para corregirla, contacta al "
    "Docente que la registró o a tu Supervisor."
)


@pytest.fixture(autouse=True)
def miercoles(reloj):
    reloj.fijar(a_las(HOY, 9))
    return reloj


@pytest.fixture(autouse=True)
def alumnos(miercoles, db, entorno):
    """En 2.º del Andino: Carlos pasa de Comprensión Lectora a Alfabetización el 10/06;
    Inés dejó el grupo el 10/06 y hoy está inactiva."""
    andino, segundo, anio = entorno.colegios["Andino"], entorno.grados["2.º"], entorno.anio
    for nombre, apellido in (("Carlos", "Cambio"), ("Inés", "Inactiva")):
        entorno.alumnos[nombre.lower()] = crear_alumno(
            db, nombre, apellido, andino, segundo, entorno.programas[CL], 1, desde=anio.fecha_inicio,
        )
    carlos, ines = entorno.alumnos["carlos"], entorno.alumnos["inés"]
    for alumno in (carlos, ines):
        intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == alumno.id_alumno)).one()
        intervalo.hasta = date(2026, 6, 10)
        db.add(intervalo)
    db.flush()
    db.add(AlumnoPertenencia(
        id_alumno=carlos.id_alumno, id_colegio=andino.id_colegio, id_grado=segundo, id_seccion=carlos.id_seccion,
        id_programa=entorno.programas[ALFA], desde=date(2026, 6, 10), creado_por=1, creado_en=a_las(HOY),
    ))
    carlos.id_programa_actual = entorno.programas[ALFA]
    ines.activo = False
    db.add(carlos)
    db.add(ines)
    db.commit()
    entorno.extra["secciones"] = {
        (colegio, grado): seccion_de(db, entorno.colegios[colegio], entorno.grados[grado]).id_seccion
        for colegio in ("Andino", "Lejano") for grado in ("1.º", "2.º", "3.º")
    }


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


def grupo(entorno, mes=JUNIO, programa=CL, grado="2.º", colegio="Andino") -> dict:
    return {
        "id_colegio": entorno.colegios[colegio].id_colegio, "id_grado": entorno.grados[grado],
        "id_seccion": entorno.extra["secciones"][(colegio, grado)],
        "id_programa": entorno.programas[programa], "id_anio_escolar": entorno.anio.id_anio_escolar, "mes": mes,
    }


def listar(client, cabecera, entorno, **kwargs):
    return client.get("/rubricas", params=grupo(entorno, **kwargs), headers=cabecera)


def item(entorno, alumno="ana", version=None, id_operacion=None, grupo_=None, **valores) -> dict:
    return {"id_operacion": str(id_operacion or uuid.uuid4()), "version": version,
            "id_alumno": entorno.alumnos[alumno].id_alumno, **(grupo_ or grupo(entorno)), **valores}


def guardar(client, cabecera, *items):
    return client.put("/rubricas", json=list(items), headers=cabecera)


def nombres(respuesta) -> list[str]:
    return [i["nombre"] for i in respuesta.json()["items"]]


# ── GET /rubricas (CU029) ───────────────────────────────────────────────────────────

def test_listado_r2_e_inactivos(client, entorno, doc, actividad):
    junio_cl = listar(client, doc, entorno)
    junio_alfa = listar(client, doc, entorno, programa=ALFA)
    mayo_cl = listar(client, doc, entorno, mes=MAYO)

    # Carlos cambió de subprograma a mitad de junio sin evaluación: se lo evalúa solo en el
    # grupo del último día lectivo transcurrido (Alfabetización). Inés salió el 10/06 y
    # sigue en junio.
    assert nombres(junio_cl) == ["Inés Inactiva", "Ana Torres"]
    assert [i["inactivo"] for i in junio_cl.json()["items"]] == [True, False]
    assert nombres(junio_alfa) == ["Carlos Cambio"]
    assert nombres(mayo_cl) == ["Carlos Cambio", "Inés Inactiva", "Ana Torres"]
    cuerpo = junio_cl.json()
    assert (cuerpo["ciclo"], cuerpo["id_periodo_academico"], cuerpo["total"], cuerpo["page_size"]) == (
        "III", entorno.periodos["vigente"].id_periodo_academico, 2, 40,
    )
    assert cuerpo["escritura"]["permitida"] is True
    assert cuerpo["detail"] is None
    assert cuerpo["items"][1] == {
        "id_alumno": entorno.alumnos["ana"].id_alumno, "nombre": "Ana Torres", "inactivo": False, "id_rubrica": None,
        "fluidez": None, "comprension": None, "estado": "pendiente", "resultado_decimas": None, "version": None,
    }


def test_listado_sin_distinguir_mayusculas_ni_tildes(client, db, entorno, doc):
    for nombre, apellidos in (("Zoe", "Ñahui"), ("Iris", "álvarez"), ("Abel", "ALVA"), ("Bea", "Ávila")):
        crear_alumno(db, nombre, apellidos, entorno.colegios["Andino"], entorno.grados["2.º"],
                     entorno.programas[CL], 1, desde=entorno.anio.fecha_inicio)
    db.commit()

    assert nombres(listar(client, doc, entorno)) == [
        "Abel ALVA", "Iris álvarez", "Bea Ávila", "Inés Inactiva", "Zoe Ñahui", "Ana Torres",
    ]


def test_listado_sin_alumnos(client, entorno, cabeceras):
    respuesta = listar(client, cabeceras["Supervisor"], entorno, grado="3.º")

    assert respuesta.status_code == 200
    assert (respuesta.json()["items"], respuesta.json()["detail"]) == (
        [], "No hay alumnos que corresponda evaluar en este grupo y mes.",
    )
    assert respuesta.json()["ciclo"] == "IV"


@pytest.mark.parametrize(("mes", "codigo"), [(JULIO, "mes_futuro"), (12, "mes_sin_actividad")])
def test_mes_futuro_o_sin_actividad(client, entorno, doc, mes, codigo):
    respuesta = listar(client, doc, entorno, mes=mes)

    assert (respuesta.status_code, motivo(respuesta)) == (422, codigo)


def test_r1_mes_partido_entre_dos_pa(client, db, entorno, doc):
    """Si el PA1 llega al 08/05 y el PA2 empieza el 11/05, mayo es del PA2 (el de más días
    lectivos del mes)."""
    fijar_periodo(db, entorno.calendario, "PA1", fin=date(2026, 5, 8))
    fijar_periodo(db, entorno.calendario, "PR1", inicio=date(2026, 5, 9), fin=date(2026, 5, 10))
    fijar_periodo(db, entorno.calendario, "PA2", inicio=date(2026, 5, 11))

    mayo = listar(client, doc, entorno, mes=MAYO).json()
    abril = listar(client, doc, entorno, mes=ABRIL).json()

    assert mayo["id_periodo_academico"] == entorno.periodos["vigente"].id_periodo_academico
    assert abril["id_periodo_academico"] == entorno.periodos["pasado"].id_periodo_academico


def _pa1_hasta_el_22_de_mayo(db, entorno):
    """PA1 hasta el viernes 22/05 (15 días lectivos de mayo); PA2 desde el miércoles 27/05 (3)."""
    fijar_periodo(db, entorno.calendario, "PA1", fin=date(2026, 5, 22))
    fijar_periodo(db, entorno.calendario, "PR1", inicio=date(2026, 5, 23), fin=date(2026, 5, 26))
    fijar_periodo(db, entorno.calendario, "PA2", inicio=date(2026, 5, 27))


def test_r1_mes_con_mas_dias_del_pa_anterior(client, db, entorno, doc):
    _pa1_hasta_el_22_de_mayo(db, entorno)

    mayo = listar(client, doc, entorno, mes=MAYO).json()

    assert mayo["id_periodo_academico"] == entorno.periodos["pasado"].id_periodo_academico


def test_r1_los_dias_no_lectivos_del_pa_no_cuentan(client, db, entorno, doc):
    _pa1_hasta_el_22_de_mayo(db, entorno)
    agregar_no_lectivo(db, entorno.calendario, date(2026, 5, 4), date(2026, 5, 22))

    mayo = listar(client, doc, entorno, mes=MAYO).json()

    # Al PA1 le queda a lo más el 01/05; el PA2 tiene 3 días lectivos.
    assert mayo["id_periodo_academico"] == entorno.periodos["vigente"].id_periodo_academico


def test_listado_roles_y_alcance(client, entorno, cabeceras):
    supervisor = listar(client, cabeceras["Supervisor"], entorno).json()

    assert supervisor["escritura"]["motivo"] == "El Supervisor consulta las rúbricas en solo lectura."
    assert listar(client, cabeceras["Directivo"], entorno).status_code == 403
    assert listar(client, {}, entorno).status_code == 401
    fuera = listar(client, cabeceras["Docente"], entorno, colegio="Lejano")
    assert (fuera.status_code, motivo(fuera)) == (403, "aula_fuera_de_alcance")


# ── PUT /rubricas (CU029, CU030) ────────────────────────────────────────────────────

def test_guardar_incompleta_y_completar(client, db, entorno, doc, actividad):
    [incompleta] = guardar(client, doc, item(entorno, fluidez=2)).json()
    [completa] = guardar(client, doc, item(entorno, version=1, comprension=3)).json()

    assert incompleta["estado"] == "aceptada", incompleta
    assert (incompleta["registro"]["estado"], incompleta["registro"]["resultado_decimas"]) == ("incompleta", None)
    assert (completa["registro"]["estado"], completa["registro"]["resultado_decimas"]) == ("completada", 26)
    assert (completa["registro"]["fluidez"], completa["registro"]["comprension"], completa["registro"]["version"]) == (
        2, 3, 2,
    )
    fila = db.exec(select(RubricaMensual)).one()
    assert (fila.id_periodo_academico, fila.mes) == (entorno.periodos["vigente"].id_periodo_academico, JUNIO)
    acciones = [a.accion for a in db.exec(select(Auditoria).where(Auditoria.tabla == "rubrica_mensual"))]
    assert set(acciones) == {"crear", "editar"}
    listado = listar(client, doc, entorno).json()["items"]
    assert [(i["estado"], i["resultado_decimas"]) for i in listado] == [("pendiente", None), ("completada", 26)]


@pytest.mark.parametrize(("programa", "alumno", "valores"), [
    (CL, "ana", {"fluidez": 0}),  # Pre-inicio solo existe en la Fluidez de Alfabetización
    (ALFA, "carlos", {"comprension": 0}),  # ni en la Comprensión de Alfabetización
    (CL, "ana", {"comprension": 5}),
    (CL, "ana", {"fluidez": -1}),
])
def test_valor_no_permitido_de_otro_programa_o_dimension(client, entorno, doc, actividad, programa, alumno, valores):
    [resultado] = guardar(client, doc, item(entorno, alumno, grupo_=grupo(entorno, programa=programa), **valores)).json()

    assert (resultado["estado"], resultado["motivo"], resultado["detail"]) == (
        "rechazada", "valor_no_permitido", MENSAJE_VALOR,
    )


def test_cero_es_un_valor_y_null_es_sin_evaluar(client, db, entorno, doc, actividad):
    alfa = grupo(entorno, programa=ALFA)
    [cero] = guardar(client, doc, item(entorno, "carlos", grupo_=alfa, fluidez=0)).json()
    antes = len(db.exec(select(Auditoria)).all())
    [igual] = guardar(client, doc, item(entorno, "carlos", version=1, grupo_=alfa, fluidez=0)).json()
    sin_cambio = len(db.exec(select(Auditoria)).all())
    [borrado] = guardar(client, doc, item(entorno, "carlos", version=1, grupo_=alfa, fluidez=None)).json()

    assert (cero["registro"]["fluidez"], cero["registro"]["estado"]) == (0, "incompleta")
    assert (igual["estado"], igual["registro"]["version"], sin_cambio) == ("aceptada", 1, antes)
    assert (borrado["registro"]["fluidez"], borrado["registro"]["estado"], borrado["registro"]["version"]) == (
        None, "pendiente", 2,
    )


def test_conflicto_de_version(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, fluidez=2))
    guardar(client, doc, item(entorno, version=1, fluidez=3))

    [viejo] = guardar(client, doc, item(entorno, version=1, comprension=4)).json()

    assert (viejo["estado"], viejo["motivo"], viejo["detail"]) == (
        "rechazada", "modificacion_concurrente",
        "Esta evaluación fue modificada desde que la consultaste. Revisa la versión actual antes de guardar nuevamente.",
    )
    assert (viejo["registro"]["fluidez"], viejo["registro"]["version"]) == (3, 2)
    assert db.exec(select(RubricaMensual)).one().id_nivel_comprension is None


def test_idempotencia(client, db, entorno, doc, actividad):
    id_operacion = uuid.uuid4()
    primera = guardar(client, doc, item(entorno, id_operacion=id_operacion, fluidez=1)).json()
    reintento = guardar(client, doc, item(entorno, id_operacion=id_operacion, fluidez=1)).json()

    assert primera == reintento
    assert len(db.exec(select(RubricaMensual)).all()) == 1


def test_alumno_que_no_pertenecia_al_grupo_ese_mes(client, entorno, doc, actividad):
    # Carlos en junio se evalúa en Alfabetización, no en Comprensión Lectora; Luis es de 1.º.
    resultados = guardar(client, doc, item(entorno, "carlos", fluidez=2), item(entorno, "luis", fluidez=2)).json()

    assert [(r["motivo"], r["detail"]) for r in resultados] == [
        ("alumno_fuera_del_grupo", "El estudiante no pertenecía a este grupo en el mes seleccionado."),
    ] * 2


def _ana_pasa_a_alfabetizacion(db, entorno, desde=date(2026, 6, 15)):
    ana = entorno.alumnos["ana"]
    intervalo = db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == ana.id_alumno)).one()
    intervalo.hasta = desde
    db.add(intervalo)
    db.add(AlumnoPertenencia(
        id_alumno=ana.id_alumno, id_colegio=intervalo.id_colegio, id_grado=intervalo.id_grado,
        id_seccion=intervalo.id_seccion, id_programa=entorno.programas[ALFA], desde=desde,
        creado_por=1, creado_en=a_las(HOY),
    ))
    db.commit()


def test_r2_mes_en_curso_solo_cuentan_los_dias_transcurridos(client, db, entorno, doc):
    """Ana pasa a Alfabetización el lunes 22/06, después de hoy (17/06): en junio todavía
    le corresponde Comprensión Lectora."""
    _ana_pasa_a_alfabetizacion(db, entorno, desde=date(2026, 6, 22))

    assert "Ana Torres" in nombres(listar(client, doc, entorno))
    assert "Ana Torres" not in nombres(listar(client, doc, entorno, programa=ALFA))


def test_r2_la_evaluacion_guardada_conserva_su_grupo(client, db, entorno, doc, actividad):
    [creada] = guardar(client, doc, item(entorno, fluidez=1)).json()
    _ana_pasa_a_alfabetizacion(db, entorno)

    en_cl = listar(client, doc, entorno)
    en_alfa = listar(client, doc, entorno, programa=ALFA)
    [corregida] = guardar(client, doc, item(entorno, version=1, fluidez=2, comprension=3)).json()

    assert creada["estado"] == "aceptada", creada
    assert nombres(en_cl) == ["Inés Inactiva", "Ana Torres"]
    assert nombres(en_alfa) == ["Carlos Cambio"]
    assert corregida["estado"] == "aceptada", corregida
    fila = db.exec(select(RubricaMensual)).one()
    assert (fila.id_programa, fila.version) == (entorno.programas[CL], 2)


def test_r2_no_se_crea_una_segunda_evaluacion_en_otro_grupo(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, fluidez=1))
    _ana_pasa_a_alfabetizacion(db, entorno)

    [resultado] = guardar(client, doc, item(entorno, grupo_=grupo(entorno, programa=ALFA), comprension=1)).json()

    assert (resultado["estado"], resultado["motivo"], resultado["detail"]) == (
        "rechazada", "evaluacion_en_otro_grupo", MENSAJE_EN_OTRO_GRUPO,
    )
    assert len(db.exec(select(RubricaMensual)).all()) == 1


def test_sin_habilitacion_y_con_permiso(client, db, entorno, doc, actividad):
    [sin_permiso] = guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=ABRIL), fluidez=2)).json()
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="rubrica")
    [con_permiso] = guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=ABRIL), fluidez=2)).json()

    assert (sin_permiso["motivo"], sin_permiso["detail"]) == (
        "sin_habilitacion",
        "No tienes una habilitación vigente para registrar o modificar las rúbricas del grupo y mes seleccionados.",
    )
    assert con_permiso["estado"] == "aceptada"
    fila = db.exec(select(RubricaMensual)).one()
    assert (fila.id_permiso, fila.id_periodo_academico) == (
        permiso.id_permiso, entorno.periodos["pasado"].id_periodo_academico,
    )


def test_sin_actividad(client, entorno, doc):
    [sin_actividad] = guardar(client, doc, item(entorno, fluidez=2)).json()

    assert (sin_actividad["motivo"], sin_actividad["detail"]) == (
        "actividad_requerida", "Debes iniciar una actividad de trabajo para registrar o modificar información.",
    )


def test_mes_futuro_al_guardar(client, entorno, doc, actividad):
    [futuro] = guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=JULIO), fluidez=2)).json()

    assert (futuro["estado"], futuro["motivo"]) == ("rechazada", "mes_futuro")


def test_escritura_roles(client, entorno, cabeceras):
    assert guardar(client, cabeceras["Supervisor"], item(entorno, fluidez=1)).status_code == 403
    assert guardar(client, cabeceras["Directivo"], item(entorno, fluidez=1)).status_code == 403
    assert guardar(client, {}, item(entorno, fluidez=1)).status_code == 401
    assert client.put("/rubricas", json=[], headers=cabeceras["Docente"]).status_code == 422


# ── Criterios y catálogo ────────────────────────────────────────────────────────────

def ids_ciclo(db) -> dict[str, int]:
    return {c.nombre: c.id_ciclo for c in db.exec(select(CicloEbr)).all()}


def test_criterios_por_programa_y_ciclo(client, db, entorno, cabeceras):
    ciclos = ids_ciclo(db)

    def criterios(programa, ciclo, cabecera=cabeceras["Docente"]):
        params = {"id_programa": entorno.programas[programa], "id_ciclo": ciclos[ciclo]}
        return client.get("/rubricas/criterios", params=params, headers=cabecera)

    alfa = criterios(ALFA, "III").json()
    cl_cuarto = criterios(CL, "IV").json()

    assert [(c["dimension"], c["valor"], c["nombre"]) for c in alfa] == [
        ("Fluidez", 0, "Pre-inicio"), ("Fluidez", 1, "Inicio"), ("Fluidez", 2, "En proceso"), ("Fluidez", 3, "Logrado"),
        ("Fluidez", 4, "Avanzado"), ("Comprensión", 1, "Inicio"), ("Comprensión", 2, "En proceso"),
        ("Comprensión", 3, "Logrado"), ("Comprensión", 4, "Avanzado"),
    ]
    assert alfa[0]["descriptor"] == "No puede leer porque le cuesta reconocer consonantes y vocales."
    assert len(cl_cuarto) == 8
    assert criterios(ALFA, "IV").json() == []
    assert criterios(ALFA, "III", cabeceras["Directivo"]).status_code == 403
    sin_programa = client.get("/rubricas/criterios", params={"id_programa": 99, "id_ciclo": ciclos["III"]},
                               headers=cabeceras["Supervisor"])
    sin_ciclo = client.get("/rubricas/criterios", params={"id_programa": entorno.programas[CL], "id_ciclo": 99},
                           headers=cabeceras["Supervisor"])
    assert (motivo(sin_programa), motivo(sin_ciclo)) == ("programa_inexistente", "ciclo_inexistente")


@pytest.mark.parametrize("programa, grado, ciclo, descriptores", [(ALFA, "2.º", "III", 9), (CL, "3.º", "IV", 8)])
def test_listado_id_ciclo_sirve_para_criterios(client, db, entorno, cabeceras, programa, grado, ciclo, descriptores):
    cuerpo = listar(client, cabeceras["Supervisor"], entorno, programa=programa, grado=grado).json()
    params = {"id_programa": entorno.programas[programa], "id_ciclo": cuerpo["id_ciclo"]}
    criterios = client.get("/rubricas/criterios", params=params, headers=cabeceras["Supervisor"])

    assert (cuerpo["ciclo"], cuerpo["id_ciclo"]) == (ciclo, ids_ciclo(db)[ciclo])
    assert len(criterios.json()) == descriptores


def test_catalogo_niveles_rubrica_con_valor_y_nombres_nuevos(client, entorno, cabeceras):
    cuerpo = client.get("/niveles-rubrica", headers=cabeceras["Docente"]).json()

    assert len(cuerpo) == 17
    alfa_fluidez = [(n["valor"], n["nombre_nivel"]) for n in cuerpo
                    if n["id_programa"] == entorno.programas[ALFA] and n["dimension"] == "Fluidez"]
    assert alfa_fluidez == [(0, "Pre-inicio"), (1, "Inicio"), (2, "En proceso"), (3, "Logrado"), (4, "Avanzado")]
    assert {n["valor"] for n in cuerpo if n["dimension"] == "Comprensión"} == {1, 2, 3, 4}


def test_carga_idempotente_de_los_33_descriptores(db, entorno):
    segunda = cargar_catalogos(db)

    assert len(db.exec(select(DescriptorRubrica)).all()) == 33
    assert segunda.creados["descriptor_rubrica"] == 0
    assert db.get(NivelRubrica, 1).valor == 0
    assert (db.get(NivelRubrica, 6).valor, db.get(NivelRubrica, 6).nombre_nivel) == (1, "Inicio")


# ── Motor e integraciones ───────────────────────────────────────────────────────────

def test_ultima_rubrica_completa_del_pa(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=MAYO), fluidez=1, comprension=1),
            item(entorno, fluidez=4))
    pa2 = entorno.periodos["vigente"].id_periodo_academico

    ultima = rubrica_service.ultima_rubrica_completa(db, entorno.alumnos["ana"].id_alumno, pa2)

    assert ultima.mes == MAYO
    assert rubrica_service.ultima_rubrica_completa(db, entorno.alumnos["luis"].id_alumno, pa2) is None


def test_salud_y_productividad(client, db, entorno, doc, actividad):
    guardar(client, doc, item(entorno, fluidez=1, comprension=2), item(entorno, "inés", fluidez=1))

    assert rubrica_service.rubricas_completadas(db, pa_actual_de_colegios(db, solo_activos=True)) == 1
    assert cambios.cambios_por_actividad(db, [uuid.UUID(actividad["id"])])[uuid.UUID(actividad["id"])] == {
        "rubrica": 2,
    }


def test_calendario_no_puede_cambiar_el_pa_de_un_mes_evaluado(client, db, entorno, doc, cabeceras, actividad):
    guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=MAYO), fluidez=1))
    rangos = rangos_relativos(HOY)
    rangos["PR1"] = (rangos["PR1"][0], date(2026, 5, 31))
    rangos["PA2"] = (date(2026, 6, 1), rangos["PA2"][1])

    respuesta = editar(client, cabeceras["Supervisor"], entorno.calendario.id_calendario, rangos)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "calendario_incompatible")
    [incompatible] = respuesta.json()["incompatibilidades"]
    assert (incompatible["tipo_registro"], incompatible["fecha"]) == ("rubrica", "2026-05-01")


def test_detalle_del_alumno_lee_la_rubrica_mensual(client, entorno, doc, actividad):
    guardar(client, doc, item(entorno, grupo_=grupo(entorno, mes=MAYO), fluidez=1, comprension=2),
            item(entorno, fluidez=3))

    rubrica = client.get(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/rubrica", headers=doc).json()

    assert [(e["mes"], e["fluidez"], e["comprension"], e["estado"], e["resultado_decimas"])
            for e in rubrica["evaluaciones"]] == [
        (JUNIO, "Logrado", None, "incompleta", None), (MAYO, "Inicio", "En proceso", "completada", 16),
    ]


# ── DP-1 (D88, provisional): el mes partido entre dos PA ────────────────────────────
# Mayo con el PA1 hasta el 22 (R1: PA1) y el PA2 desde el 27, como octubre entre el PA3 y
# el PA4.

def _docente_solo_de(client, db, entorno, periodo, correo, dni) -> dict:
    """Un Docente asignado a 2.º del Andino solo en ese PA (en lugar del del entorno), con
    actividad en curso."""
    andino, segundo = entorno.colegios["Andino"], entorno.grados["2.º"]
    db.delete(db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_colegio == andino.id_colegio, DocenteColegioGrado.id_grado == segundo,
        DocenteColegioGrado.id_periodo_academico == entorno.periodos[periodo].id_periodo_academico,
    )).one())
    db.flush()
    otro = crear_docente(db, entorno, "Otro", correo, dni)
    asignar(db, otro.id_docente, andino, [segundo], entorno.periodos[periodo], 1)
    db.commit()
    cabecera = cabecera_de(db, otro)
    iniciar_actividad(client, cabecera)
    return cabecera


def test_dp1_un_docente_solo_del_pa_siguiente_evalua_el_mes_partido(client, db, entorno):
    _pa1_hasta_el_22_de_mayo(db, entorno)
    del_pa2 = _docente_solo_de(client, db, entorno, "vigente", "pa2@sicedu.test", "70000002")

    mayo = listar(client, del_pa2, entorno, mes=MAYO).json()
    [guardado] = guardar(client, del_pa2, item(entorno, grupo_=grupo(entorno, mes=MAYO), fluidez=2)).json()

    pa1 = entorno.periodos["pasado"].id_periodo_academico
    assert (mayo["escritura"]["permitida"], mayo["id_periodo_academico"]) == (True, pa1)
    assert guardado["estado"] == "aceptada"
    # La evaluación sigue asociada al PA de R1, y la usa el Registro de Vuelo del PA1.
    fila = db.exec(select(RubricaMensual)).one()
    assert (fila.id_periodo_academico, fila.id_permiso) == (pa1, None)
    guardar(client, del_pa2, item(entorno, grupo_=grupo(entorno, mes=MAYO), version=1, fluidez=2, comprension=3))
    assert rubrica_service.ultima_rubrica_completa(db, entorno.alumnos["ana"].id_alumno, pa1).mes == MAYO


def test_dp1_un_permiso_de_un_pa_sin_dias_en_el_mes_no_sirve(client, db, entorno):
    """Junio es íntegro del PA2: un permiso del PA1 no lo habilita (como el PA2 en octubre)."""
    otro = crear_docente(db, entorno, "Otro", "pa1@sicedu.test", "70000003")
    crear_permiso(db, entorno, otro.id_docente, "Andino", "2.º", modulo="rubrica")
    db.commit()
    cabecera = cabecera_de(db, otro)
    iniciar_actividad(client, cabecera)

    junio = listar(client, cabecera, entorno, mes=JUNIO).json()
    [rechazado] = guardar(client, cabecera, item(entorno, fluidez=2)).json()

    assert junio["escritura"]["permitida"] is False
    assert rechazado["motivo"] == "sin_habilitacion"


def test_dp1_un_permiso_del_pa_de_r1_sigue_sirviendo(client, db, entorno):
    _pa1_hasta_el_22_de_mayo(db, entorno)
    otro = crear_docente(db, entorno, "Otro", "permiso@sicedu.test", "70000004")
    permiso = crear_permiso(db, entorno, otro.id_docente, "Andino", "2.º", modulo="rubrica")
    db.commit()
    cabecera = cabecera_de(db, otro)
    iniciar_actividad(client, cabecera)

    mayo = listar(client, cabecera, entorno, mes=MAYO).json()
    [guardado] = guardar(client, cabecera, item(entorno, grupo_=grupo(entorno, mes=MAYO), fluidez=2)).json()

    assert (mayo["escritura"]["permitida"], mayo["escritura"]["id_permiso"]) == (True, permiso.id_permiso)
    assert guardado["estado"] == "aceptada"
    assert db.exec(select(RubricaMensual)).one().id_permiso == permiso.id_permiso


def _rubricas_vencidas(db) -> set[str]:
    return {r.referencia for r in estado_de_registros(db).vencidas if r.modulo == "rubrica"}


def test_dp1_el_mes_partido_vence_en_el_pa_siguiente_y_el_mes_integro_anterior_no(db, entorno, miercoles):
    _pa1_hasta_el_22_de_mayo(db, entorno)
    ana = entorno.alumnos["ana"].id_alumno

    miercoles.fijar(a_las(date(2026, 5, 31), 9))
    antes = _rubricas_vencidas(db)
    miercoles.fijar(a_las(date(2026, 6, 1), 9))
    el_1_de_junio = _rubricas_vencidas(db)

    assert f"{MAYO}:{ana}" not in antes
    assert f"{MAYO}:{ana}" in el_1_de_junio
    # Abril es íntegro del PA1: no se exige durante el PA2.
    assert not any(r.startswith(f"{ABRIL}:") for r in el_1_de_junio)


def test_dp2_durante_el_pr2_la_rubrica_del_ultimo_mes_del_pa2_figura_vencida(db, entorno, miercoles):
    """S21 (provisional), como septiembre en "QA Sur": julio es del PA2 (hasta el 17/07) y
    vence el 01/08, ya en el PR2. Antes no figuraba (en un PR no había vencidos)."""
    fijar_periodo(db, entorno.calendario, "PR2", fin=date(2026, 8, 5))
    fijar_periodo(db, entorno.calendario, "PA3", inicio=date(2026, 8, 6))
    ana = entorno.alumnos["ana"].id_alumno

    miercoles.fijar(a_las(date(2026, 8, 1), 9))

    assert f"{JULIO}:{ana}" in _rubricas_vencidas(db)
