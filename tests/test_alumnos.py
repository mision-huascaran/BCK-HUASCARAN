"""Alumnos (CU014) y su vista de detalle (CU015)."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.evaluacion import (
    AsistenciaSemanal,
    EvaluacionDiagnostica,
    EvaluacionDiagnosticaAlumno,
    LibroSubidaNivel,
    NivelFinalMensual,
    NivelGeneral,
    NivelRubrica,
    ReporteSemanalAlumno,
    RubricaRegistroSemanal,
    SemanaReporte,
)
from app.models.organizacion import Alumno, AlumnoProgramaHistorial, CicloEbr, Colegio
from app.models.trazabilidad import Auditoria
from app.services import alumno_detalle_service
from tests.conftest import crear_colegio, motivo, releer


def nuevo(entorno, colegio="Andino", grado="2.º", programa="Comprensión Lectora", **cambios):
    return {
        "nombres": "Nora",
        "apellidos": "Nueva",
        "id_colegio": entorno.colegios[colegio].id_colegio,
        "id_grado": entorno.grados[grado],
        "id_programa": entorno.programas[programa],
        **cambios,
    }


def nombres(respuesta) -> list[str]:
    return [a["nombres"] for a in respuesta.json()["items"]]


# ── Listado ─────────────────────────────────────────────────────────────────────────

def test_cu014_el_supervisor_ve_todos_y_el_docente_su_alcance(client, entorno, cabeceras):
    assert nombres(client.get("/alumnos", headers=cabeceras["Supervisor"])) == ["Mario", "Luis", "Ana"]
    assert nombres(client.get("/alumnos", headers=cabeceras["Docente"])) == ["Luis", "Ana"]


def test_cu014_el_directivo_no_ve_alumnos(client, entorno, cabeceras):
    respuesta = client.get("/alumnos", headers=cabeceras["Directivo"])

    assert (respuesta.status_code, motivo(respuesta)) == (403, "sin_permiso")


@pytest.mark.parametrize(
    "params, esperado",
    [
        ({"q": "perez"}, ["Luis"]),
        ({"q": "QUISPE luis"}, ["Luis"]),
        ({"q": "100%"}, []),
        ({"q": "  "}, ["Mario", "Luis", "Ana"]),
        ({"ciclo": "III"}, ["Luis", "Ana"]),
        ({"ciclo": "IV"}, ["Mario"]),
        ({"activo": "false"}, ["Iván"]),
    ],
)
def test_cu014_busqueda_y_filtros(client, entorno, cabeceras, params, esperado):
    assert nombres(client.get("/alumnos", params=params, headers=cabeceras["Supervisor"])) == esperado


def test_cu014_filtros_por_id(client, entorno, cabeceras):
    sup = cabeceras["Supervisor"]
    params = {
        "id_colegio": entorno.colegios["Andino"].id_colegio,
        "id_grado": entorno.grados["1.º"],
        "id_programa": entorno.programas["Alfabetización"],
    }

    assert nombres(client.get("/alumnos", params=params, headers=sup)) == ["Luis"]


def test_cu014_docente_sin_alcance_recibe_una_pagina_vacia(client, db, entorno, cabeceras):
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()

    assert client.get("/alumnos", headers=cabeceras["Docente"]).json()["total"] == 0


def test_cu014_lista_y_ciclo_calculado(client, entorno, cabeceras):
    item = client.get("/alumnos", params={"q": "mario"}, headers=cabeceras["Supervisor"]).json()["items"][0]

    assert item["ciclo"] == "IV"
    assert item["seccion"] == "Única"
    assert item["colegio"]["nombre"] == "Colegio Lejano"
    assert item["programa"]["nombre"] == "Comprensión Lectora"


# ── Alta ────────────────────────────────────────────────────────────────────────────

def test_cu014_el_docente_crea_con_actividad_y_queda_auditado(client, db, entorno, cabeceras, actividad):
    respuesta = client.post("/alumnos", json=nuevo(entorno), headers=cabeceras["Docente"])

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert (cuerpo["ciclo"], cuerpo["activo"]) == ("III", True)
    fila = db.exec(select(Auditoria).where(Auditoria.tabla == "alumno")).one()
    assert (fila.accion, str(fila.id_actividad)) == ("crear", actividad["id"])
    historial = db.exec(select(AlumnoProgramaHistorial).where(AlumnoProgramaHistorial.id_alumno == cuerpo["id"])).one()
    assert historial.id_periodo_academico == entorno.periodos["vigente"].id_periodo_academico


def test_cu014_el_docente_sin_actividad_no_escribe(client, entorno, cabeceras):
    respuesta = client.post("/alumnos", json=nuevo(entorno), headers=cabeceras["Docente"])

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_requerida")
    assert respuesta.json()["detail"] == "Debe iniciar una actividad para realizar cambios."


def test_cu014_el_supervisor_crea_sin_actividad_en_cualquier_colegio(client, entorno, cabeceras):
    respuesta = client.post("/alumnos", json=nuevo(entorno, "Lejano", "5.º"), headers=cabeceras["Supervisor"])

    assert respuesta.status_code == 201
    assert respuesta.json()["ciclo"] == "V"


@pytest.mark.parametrize("rol, codigo, esperado", [("Docente", 403, "alumno_fuera_de_alcance"), ("Directivo", 403, "sin_permiso")])
def test_cu014_alta_fuera_de_alcance(client, entorno, cabeceras, actividad, rol, codigo, esperado):
    respuesta = client.post("/alumnos", json=nuevo(entorno, "Lejano", "3.º"), headers=cabeceras[rol])

    assert (respuesta.status_code, motivo(respuesta)) == (codigo, esperado)


@pytest.mark.parametrize(
    "preparar, esperado",
    [
        ("primero_en_comprension", "primer_grado_solo_alfabetizacion"),
        ("grado_no_ofrecido", "grado_no_ofrecido"),
        ("programa_no_ofrecido", "programa_no_ofrecido"),
        ("colegio_inexistente", "colegio_inexistente"),
        ("colegio_inactivo", "colegio_inactivo"),
    ],
)
def test_cu014_alta_invalida(client, db, entorno, cabeceras, preparar, esperado):
    datos = nuevo(entorno)
    if preparar == "primero_en_comprension":
        datos = nuevo(entorno, grado="1.º")
    elif preparar in ("grado_no_ofrecido", "programa_no_ofrecido"):
        chico = crear_colegio(
            db, "Colegio Chico", [entorno.grados["1.º"]], [entorno.programas["Alfabetización"]], entorno.supervisor.id_usuario
        )
        db.commit()
        datos = {**datos, "id_colegio": chico.id_colegio}
        if preparar == "programa_no_ofrecido":
            datos["id_grado"] = entorno.grados["1.º"]
    elif preparar == "colegio_inexistente":
        datos["id_colegio"] = 999
    else:
        colegio = entorno.colegios["Andino"]
        colegio.activo = False
        db.add(colegio)
        db.commit()

    respuesta = client.post("/alumnos", json=datos, headers=cabeceras["Supervisor"])

    assert (respuesta.status_code, motivo(respuesta)) == (422, esperado)


def test_cu014_alta_con_campos_vacios(client, entorno, cabeceras):
    respuesta = client.post("/alumnos", json=nuevo(entorno, nombres=" "), headers=cabeceras["Supervisor"])

    assert respuesta.json()["errores"] == [{"campo": "nombres", "mensaje": "No puede estar vacío."}]


# ── Detalle y edición ───────────────────────────────────────────────────────────────

def test_cu015_cabecera_del_detalle(client, entorno, cabeceras):
    luis = entorno.alumnos["luis"].id_alumno

    cuerpo = client.get(f"/alumnos/{luis}", headers=cabeceras["Docente"]).json()

    assert cuerpo["fecha_registro"] == entorno.hoy.isoformat()
    assert cuerpo["grado"]["nombre"] == "1.º"


def test_cu015_detalle_inexistente_y_fuera_de_alcance(client, entorno, cabeceras):
    inexistente = client.get("/alumnos/999", headers=cabeceras["Supervisor"])
    fuera = client.get(f"/alumnos/{entorno.alumnos['mario'].id_alumno}", headers=cabeceras["Docente"])

    assert (inexistente.status_code, motivo(inexistente)) == (404, "alumno_no_encontrado")
    assert (fuera.status_code, motivo(fuera)) == (403, "alumno_fuera_de_alcance")


def test_cu014_editar_y_cambiar_subprograma_registra_el_historial(client, db, entorno, cabeceras, actividad):
    ana = entorno.alumnos["ana"].id_alumno

    respuesta = client.patch(
        f"/alumnos/{ana}", json={"apellidos": "Torres Vega", "id_programa": entorno.programas["Alfabetización"]},
        headers=cabeceras["Docente"],
    )

    assert respuesta.json()["programa"]["nombre"] == "Alfabetización"
    historial = db.exec(select(AlumnoProgramaHistorial).where(AlumnoProgramaHistorial.id_alumno == ana)).one()
    assert historial.id_programa == entorno.programas["Alfabetización"]
    campos = set(db.exec(select(Auditoria.campo).where(Auditoria.accion == "editar")).all())
    assert campos == {"apellidos", "id_programa"}

    client.patch(f"/alumnos/{ana}", json={"id_programa": entorno.programas["Comprensión Lectora"]}, headers=cabeceras["Docente"])
    db.expire_all()
    historial = db.exec(select(AlumnoProgramaHistorial).where(AlumnoProgramaHistorial.id_alumno == ana)).one()
    assert historial.id_programa == entorno.programas["Comprensión Lectora"]


def test_cu014_editar_sin_cambios_no_audita(client, db, entorno, cabeceras):
    luis = entorno.alumnos["luis"]

    respuesta = client.patch(f"/alumnos/{luis.id_alumno}", json={"nombres": luis.nombres}, headers=cabeceras["Supervisor"])

    assert respuesta.status_code == 200
    assert db.exec(select(Auditoria)).all() == []


@pytest.mark.parametrize(
    "rol, cambio, codigo, esperado",
    [
        ("Docente", {"id_colegio": "Lejano"}, 403, "cambio_colegio_no_permitido"),
        ("Docente", {"id_grado": "3.º"}, 403, "alumno_fuera_de_alcance"),
        ("Supervisor", {"id_grado": "1.º"}, 422, "primer_grado_solo_alfabetizacion"),
        ("Supervisor", {"nombres": None}, 422, "validacion"),
    ],
)
def test_cu014_edicion_no_permitida(client, entorno, cabeceras, actividad, rol, cambio, codigo, esperado):
    traducido = {
        "id_colegio": lambda v: entorno.colegios[v].id_colegio,
        "id_grado": lambda v: entorno.grados[v],
    }
    cuerpo = {k: (traducido[k](v) if k in traducido else v) for k, v in cambio.items()}

    respuesta = client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}", json=cuerpo, headers=cabeceras[rol])

    assert (respuesta.status_code, motivo(respuesta)) == (codigo, esperado)


def test_cu014_el_supervisor_rota_un_alumno_de_colegio(client, entorno, cabeceras):
    respuesta = client.patch(
        f"/alumnos/{entorno.alumnos['ana'].id_alumno}",
        json={"id_colegio": entorno.colegios["Lejano"].id_colegio},
        headers=cabeceras["Supervisor"],
    )

    assert respuesta.json()["colegio"]["nombre"] == "Colegio Lejano"


def test_cu013_colegio_inactivo_permite_corregir_nombres_pero_no_mover(client, db, entorno, cabeceras):
    """B3 de la Tanda 8.1: inactivar un colegio no afecta la corrección de sus alumnos."""
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()
    mario = entorno.alumnos["mario"].id_alumno
    sup = cabeceras["Supervisor"]

    corregir = client.patch(f"/alumnos/{mario}", json={"nombres": "Mario José"}, headers=sup)
    mover = client.patch(f"/alumnos/{mario}", json={"id_grado": entorno.grados["4.º"]}, headers=sup)

    assert corregir.status_code == 200
    assert (mover.status_code, motivo(mover)) == (422, "colegio_inactivo")


def test_cu014_activar_e_inactivar(client, db, entorno, cabeceras, actividad):
    ivan = entorno.alumnos["inactivo"].id_alumno
    docente = cabeceras["Docente"]

    activado = client.patch(f"/alumnos/{ivan}/activar", headers=docente)
    repetido = client.patch(f"/alumnos/{ivan}/activar", headers=docente)
    inactivado = client.patch(f"/alumnos/{ivan}/desactivar", headers=docente)

    assert (activado.json()["activo"], repetido.json()["activo"], inactivado.json()["activo"]) == (True, True, False)
    acciones = db.exec(select(Auditoria.accion).order_by(Auditoria.id_auditoria)).all()
    assert acciones == ["activar", "inactivar"]


def test_cu014_activar_exige_colegio_activo(client, db, entorno, cabeceras):
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()

    respuesta = client.patch(f"/alumnos/{entorno.alumnos['inactivo'].id_alumno}/activar", headers=cabeceras["Supervisor"])

    assert motivo(respuesta) == "colegio_inactivo"


# ── CU015: pestañas del detalle ─────────────────────────────────────────────────────

@pytest.fixture
def registros(db, entorno):
    """Registros académicos de Luis en el año escolar vigente."""
    luis = entorno.alumnos["luis"].id_alumno
    anio, periodo = entorno.anio.id_anio_escolar, entorno.periodos["vigente"].id_periodo_academico
    ahora = ahora_utc()
    base = {"creado_por": entorno.supervisor.id_usuario, "creado_en": ahora}
    captura = {"id_docente": entorno.id_docente, "fecha_registro": ahora, **base}
    semanas = []
    for numero in (1, 2):
        lunes = entorno.hoy - timedelta(days=21 - 7 * numero)
        semana = SemanaReporte(id_anio_escolar=anio, id_periodo_academico=periodo, numero_semana=numero,
                               fecha_inicio=lunes, fecha_fin=lunes + timedelta(days=4), **base)
        db.add(semana)
        semanas.append(semana)
    db.flush()
    claves = {"id_alumno": luis, "id_anio_escolar": anio, "id_periodo_academico": periodo}
    reporte = ReporteSemanalAlumno(id_semana=semanas[0].id_semana, cantidad_lsl=3, observaciones="Bien", **claves, **captura)
    db.add(reporte)
    db.add(ReporteSemanalAlumno(id_semana=semanas[1].id_semana, cantidad_lsl=None, **claves, **captura))
    db.flush()
    db.add(LibroSubidaNivel(id_reporte_semanal=reporte.id, titulo_libro="El zorro", aciertos=4, total_preguntas=5, **base))
    for semana, asistio in zip(semanas, (True, False)):
        db.add(AsistenciaSemanal(id_alumno=luis, id_semana=semana.id_semana, asistio=asistio, **captura))
    nivel_rubrica = db.exec(select(NivelRubrica).order_by(NivelRubrica.id_nivel_rubrica)).first()
    db.add(RubricaRegistroSemanal(id_semana=semanas[1].id_semana, id_nivel_fluidez=nivel_rubrica.id_nivel_rubrica,
                                  observacion="Lee fluido", **claves, **captura))
    generales = db.exec(select(NivelGeneral).order_by(NivelGeneral.orden)).all()
    for mes, nivel in ((4, generales[0]), (5, generales[1])):
        db.add(NivelFinalMensual(mes=mes, id_nivel_final=nivel.id_nivel_general, ajustado_por_docente=mes == 5,
                                 justificacion="Ajuste" if mes == 5 else None, fecha_calculo=ahora, **claves, **base))
    corte = EvaluacionDiagnostica(id_periodo_academico=periodo, fecha_realizacion=entorno.hoy, **base)
    db.add(corte)
    db.flush()
    ciclo = db.exec(select(CicloEbr).where(CicloEbr.nombre == "III")).one()
    db.add(EvaluacionDiagnosticaAlumno(id_alumno=luis, id_evaluacion_diagnostica=corte.id_evaluacion_diagnostica,
                                       id_ciclo_evaluado=ciclo.id_ciclo, id_nivel_rk_entrada=2, aciertos_prueba=8,
                                       total_prueba=10, nivel_colocado=3, **captura))
    db.commit()
    return {"luis": luis, "nivel_mayo": generales[1].nombre_nivel}


def test_cu015_resumen_con_registros(client, entorno, cabeceras, registros):
    cuerpo = client.get(f"/alumnos/{registros['luis']}/resumen", headers=cabeceras["Docente"]).json()

    assert cuerpo == {
        "nivel_actual": {"nombre": registros["nivel_mayo"], "mes": 5},
        "libros_leidos_anio": 4,
        "asistencia_porcentaje": 50.0,
    }


def test_cu015_pestanas_con_registros(client, entorno, cabeceras, registros):
    cabecera = cabeceras["Docente"]
    url = f"/alumnos/{registros['luis']}"

    vuelo = client.get(f"{url}/registro-vuelo", headers=cabecera).json()
    rubrica = client.get(f"{url}/rubrica", headers=cabecera).json()
    lectura = client.get(f"{url}/lectura", headers=cabecera).json()

    assert (vuelo[0]["nivel_entrada"], vuelo[0]["nivel_colocado"], vuelo[0]["aciertos"]) == ("A", "B", 8)
    assert [s["semana"] for s in rubrica["semanales"]] == [2]
    assert rubrica["semanales"][0]["fluidez"] is not None
    assert [(m["mes"], m["ajustado"]) for m in rubrica["mensuales"]] == [(5, True), (4, False)]
    assert [(s["semana"], s["cantidad_lsl"]) for s in lectura] == [(2, None), (1, 3)]
    assert lectura[1]["libros_lsb"] == [{"titulo": "El zorro", "aciertos": 4, "total": 5}]


def test_cu015_pestanas_vacias(client, entorno, cabeceras):
    url = f"/alumnos/{entorno.alumnos['ana'].id_alumno}"
    cabecera = cabeceras["Supervisor"]

    assert client.get(f"{url}/resumen", headers=cabecera).json() == {
        "nivel_actual": None, "libros_leidos_anio": None, "asistencia_porcentaje": None,
    }
    assert client.get(f"{url}/registro-vuelo", headers=cabecera).json() == []
    assert client.get(f"{url}/rubrica", headers=cabecera).json() == {"semanales": [], "mensuales": []}
    assert client.get(f"{url}/lectura", headers=cabecera).json() == []


def test_cu015_sin_anio_escolar_las_pestanas_salen_vacias(client, entorno, cabeceras, monkeypatch):
    monkeypatch.setattr(alumno_detalle_service, "anio_de_referencia", lambda _db: None)
    url = f"/alumnos/{entorno.alumnos['luis'].id_alumno}"
    cabecera = cabeceras["Supervisor"]

    assert client.get(f"{url}/resumen", headers=cabecera).json()["nivel_actual"] is None
    assert client.get(f"{url}/registro-vuelo", headers=cabecera).json() == []
    assert client.get(f"{url}/rubrica", headers=cabecera).json() == {"semanales": [], "mensuales": []}
    assert client.get(f"{url}/lectura", headers=cabecera).json() == []
    assert client.get(f"{url}/historial", headers=cabecera).json()["total"] == 0


@pytest.mark.parametrize("pestana", ["resumen", "registro-vuelo", "rubrica", "lectura", "historial"])
def test_cu015_pestanas_respetan_el_alcance(client, entorno, cabeceras, pestana):
    respuesta = client.get(f"/alumnos/{entorno.alumnos['mario'].id_alumno}/{pestana}", headers=cabeceras["Docente"])

    assert respuesta.status_code == 403


def test_cu015_historial_agrupa_por_evento_con_nombres_legibles(client, db, entorno, cabeceras, actividad):
    docente = cabeceras["Docente"]
    creado = client.post("/alumnos", json=nuevo(entorno), headers=docente).json()
    url = f"/alumnos/{creado['id']}"
    client.patch(url, json={"nombres": "Norma", "id_programa": entorno.programas["Alfabetización"]}, headers=docente)
    client.patch(f"{url}/desactivar", headers=docente)

    cuerpo = client.get(f"{url}/historial", headers=docente).json()

    assert [e["accion"] for e in cuerpo["items"]] == ["inactivar", "editar", "crear"]
    inactivar, editar, crear = cuerpo["items"]
    assert inactivar["cambios"] == [{"campo": "Estado", "anterior": "Activo", "nuevo": "Inactivo"}]
    assert {(c["campo"], c["anterior"], c["nuevo"]) for c in editar["cambios"]} == {
        ("Nombres", "Nora", "Norma"),
        ("Subprograma", "Comprensión Lectora", "Alfabetización"),
    }
    assert (crear["usuario"], crear["rol"], crear["cambios"]) == ("Rosa Quispe", "Docente", [])


def test_cu015_historial_pagina_de_10_eventos(client, db, entorno, cabeceras):
    sup = cabeceras["Supervisor"]
    luis = entorno.alumnos["luis"].id_alumno
    for n in range(11):
        client.patch(f"/alumnos/{luis}", json={"nombres": f"Luis {n}"}, headers=sup)

    segunda = client.get(f"/alumnos/{luis}/historial", params={"page": 2}, headers=sup).json()
    tercera = client.get(f"/alumnos/{luis}/historial", params={"page": 3}, headers=sup).json()

    assert (segunda["total"], len(segunda["items"]), tercera["items"]) == (11, 1, [])


def test_cu015_historial_con_catalogo_borrado_muestra_el_id(client, db, entorno, cabeceras):
    luis = entorno.alumnos["luis"].id_alumno
    db.add(Auditoria(tabla="alumno", id_registro=str(luis), accion="editar", campo="id_grado",
                     valor_anterior="98", valor_nuevo="99", id_usuario=999, fecha=ahora_utc()))
    db.commit()

    evento = client.get(f"/alumnos/{luis}/historial", headers=cabeceras["Supervisor"]).json()["items"][0]

    assert (evento["usuario"], evento["rol"]) == ("999", "")
    assert evento["cambios"] == [{"campo": "Grado", "anterior": "98", "nuevo": "99"}]


def test_cu014_crear_desde_supervisor_entre_periodos_usa_el_proximo(client, db, entorno, cabeceras):
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = entorno.hoy - timedelta(days=1)
    db.add(vigente)
    db.commit()

    creado = client.post("/alumnos", json=nuevo(entorno), headers=cabeceras["Supervisor"]).json()

    historial = db.exec(select(AlumnoProgramaHistorial).where(AlumnoProgramaHistorial.id_alumno == creado["id"])).one()
    assert historial.id_periodo_academico == entorno.periodos["futuro"].id_periodo_academico
    assert releer(db, Alumno, creado["id"]).activo is True
    assert releer(db, Colegio, entorno.colegios["Andino"].id_colegio).activo is True
