"""Colegios (CU013). Gestiona el Supervisor; el Docente lee los de su alcance."""
import pytest
from sqlmodel import select

from app.models.organizacion import Colegio, ColegioGrado
from app.models.trazabilidad import Auditoria
from tests.conftest import crear_alumno, motivo, releer


def oferta(entorno, *grados, dia=1) -> list[dict]:
    """`grados` del cuerpo: cada grado con su día de clase (1 = lunes por defecto)."""
    return [{"id_grado": entorno.grados[g], "dia_clase": dia} for g in grados]


def datos(entorno, **cambios):
    return {
        "nombre": "Colegio Nuevo",
        "departamento": "Áncash",
        "distrito": "Yungay",
        "provincia": "Yungay",
        "grados": [{"id_grado": entorno.grados["1.º"], "dia_clase": 1}, {"id_grado": entorno.grados["2.º"], "dia_clase": 3}],
        "programas": [entorno.programas["Alfabetización"]],
        **cambios,
    }


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def test_cu013_crear_colegio_con_su_oferta(client, db, entorno, sup):
    respuesta = client.post("/colegios", json=datos(entorno), headers=sup)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert (cuerpo["nivel_educativo"], cuerpo["seccion"], cuerpo["activo"]) == ("Primaria", "Única", True)
    assert [(g["nombre"], g["dia_clase"]) for g in cuerpo["grados"]] == [("1.º", 1), ("2.º", 3)]
    assert [p["nombre"] for p in cuerpo["programas"]] == ["Alfabetización"]
    assert db.exec(select(Auditoria.accion).where(Auditoria.tabla == "colegio")).all() == ["crear"]
    assert client.get(f"/colegios/{cuerpo['id']}", headers=sup).json()["grados"] == cuerpo["grados"]


@pytest.mark.parametrize(
    "grados, campo",
    [
        ([{"id_grado": 1}], "grados.0.dia_clase"),
        ([{"id_grado": 1, "dia_clase": None}], "grados.0.dia_clase"),
        ([{"id_grado": 1, "dia_clase": 0}], "grados.0.dia_clase"),
        ([{"id_grado": 1, "dia_clase": 6}], "grados.0.dia_clase"),
        ([1], "grados.0"),
        ([{"id_grado": 1, "dia_clase": 1}, {"id_grado": 1, "dia_clase": 2}], "grados"),
    ],
    ids=["sin_dia", "dia_null", "dia_0", "sabado", "solo_id", "grado_repetido"],
)
def test_cu013_cada_grado_lleva_su_dia_de_clase(client, entorno, sup, grados, campo):
    alta = client.post("/colegios", json=datos(entorno, grados=grados), headers=sup)
    edicion = client.patch(f"/colegios/{entorno.colegios['Andino'].id_colegio}", json={"grados": grados}, headers=sup)

    for respuesta in (alta, edicion):
        assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
        assert [e["campo"] for e in respuesta.json()["errores"]] == [campo]


@pytest.mark.parametrize("nivel", ["Secundaria", "Inicial", "primaria", ""])
def test_cu013_nivel_educativo_solo_primaria(client, db, entorno, sup, nivel):
    alta = client.post("/colegios", json=datos(entorno, nivel_educativo=nivel), headers=sup)
    edicion = client.patch(
        f"/colegios/{entorno.colegios['Andino'].id_colegio}", json={"nivel_educativo": nivel}, headers=sup
    )

    for respuesta in (alta, edicion):
        assert respuesta.status_code == 422
        assert [e["campo"] for e in respuesta.json()["errores"]] == ["nivel_educativo"]
    assert releer(db, Colegio, entorno.colegios["Andino"].id_colegio).nivel_educativo == "Primaria"


def test_cu013_nivel_educativo_primaria_explicito_se_acepta(client, entorno, sup):
    alta = client.post("/colegios", json=datos(entorno, nivel_educativo="Primaria"), headers=sup)
    edicion = client.patch(
        f"/colegios/{entorno.colegios['Andino'].id_colegio}", json={"nivel_educativo": "Primaria"}, headers=sup
    )

    assert (alta.status_code, alta.json()["nivel_educativo"]) == (201, "Primaria")
    assert edicion.status_code == 200


def test_cu013_cambiar_solo_el_dia_de_clase_se_audita(client, db, entorno, sup):
    colegio = client.post("/colegios", json=datos(entorno), headers=sup).json()
    grados = [{"id_grado": entorno.grados["1.º"], "dia_clase": 5}, {"id_grado": entorno.grados["2.º"], "dia_clase": 3}]

    respuesta = client.patch(f"/colegios/{colegio['id']}", json={"grados": grados}, headers=sup)

    assert [g["dia_clase"] for g in respuesta.json()["grados"]] == [5, 3]
    cambio = db.exec(select(Auditoria).where(Auditoria.accion == "editar")).one()
    assert (cambio.campo, cambio.valor_anterior, cambio.valor_nuevo) == (
        "grados", "1.º (lunes), 2.º (miércoles)", "1.º (viernes), 2.º (miércoles)"
    )
    filas = {f.id_grado: f for f in db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == colegio["id"])).all()}
    assert filas[entorno.grados["1.º"]].modificado_por == entorno.supervisor.id_usuario
    assert filas[entorno.grados["2.º"]].modificado_por is None


def test_cu013_colegio_cargado_sin_dias_los_muestra_en_null(client, entorno, sup):
    detalle = client.get(f"/colegios/{entorno.colegios['Andino'].id_colegio}", headers=sup).json()

    assert {g["dia_clase"] for g in detalle["grados"]} == {None}


@pytest.mark.parametrize("nombre", ["colegio andino", "  COLEGIO ÁNDINO ", "Colegio   Andino"])
def test_cu013_nombre_duplicado_sin_distinguir_mayusculas_tildes_ni_espacios(client, entorno, sup, nombre):
    respuesta = client.post("/colegios", json=datos(entorno, nombre=nombre), headers=sup)

    assert respuesta.status_code == 409
    assert motivo(respuesta) == "colegio_duplicado"


@pytest.mark.parametrize(
    "cambio, esperado",
    [
        ({"departamento": None}, "validacion"),
        ({"provincia": None}, "validacion"),
        ({"provincia": "  "}, "validacion"),
        ({"distrito": ""}, "validacion"),
        ({"grados": []}, "validacion"),
        ({"grados": [{"id_grado": 999, "dia_clase": 1}]}, "grado_inexistente"),
        ({"programas": [999]}, "programa_inexistente"),
    ],
)
def test_cu013_alta_invalida(client, entorno, sup, cambio, esperado):
    respuesta = client.post("/colegios", json=datos(entorno, **cambio), headers=sup)

    assert respuesta.status_code == 422
    assert motivo(respuesta) == esperado


def test_cu013_listado_activos_por_defecto_y_filtros(client, db, entorno, sup):
    lejano = entorno.colegios["Lejano"]
    assert [c["nombre"] for c in client.get("/colegios", headers=sup).json()["items"]] == ["Colegio Andino", "Colegio Lejano"]
    por_departamento = client.get("/colegios", params={"departamento": " lima "}, headers=sup).json()
    por_distrito = client.get("/colegios", params={"distrito": "MARCARÁ"}, headers=sup).json()
    assert [c["id"] for c in por_departamento["items"]] == [lejano.id_colegio]
    assert [c["nombre"] for c in por_distrito["items"]] == ["Colegio Andino"]

    client.patch(f"/colegios/{lejano.id_colegio}/desactivar", headers=sup)

    inactivos = client.get("/colegios", params={"activo": False}, headers=sup).json()["items"]
    assert [c["nombre"] for c in inactivos] == ["Colegio Lejano"]


def test_cu013_ubicaciones_para_los_filtros(client, entorno, sup):
    todas = client.get("/colegios/ubicaciones", headers=sup).json()
    de_lima = client.get("/colegios/ubicaciones", params={"departamento": "Lima"}, headers=sup).json()

    assert todas == {"departamentos": ["Lima", "Áncash"], "distritos": ["Marcará", "Miraflores"]}
    assert de_lima["distritos"] == ["Miraflores"]


def test_cu013_el_docente_solo_lee_los_colegios_de_su_alcance(client, entorno, cabeceras):
    docente = cabeceras["Docente"]
    andino, lejano = entorno.colegios["Andino"].id_colegio, entorno.colegios["Lejano"].id_colegio

    assert [c["id"] for c in client.get("/colegios", headers=docente).json()["items"]] == [andino]
    assert client.get("/colegios/ubicaciones", headers=docente).json()["departamentos"] == ["Áncash"]
    assert client.get(f"/colegios/{andino}", headers=docente).status_code == 200
    fuera = client.get(f"/colegios/{lejano}", headers=docente)
    assert (fuera.status_code, motivo(fuera)) == (403, "colegio_fuera_de_alcance")
    assert client.post("/colegios", json=datos(entorno), headers=docente).status_code == 403


def test_cu013_docente_sin_alcance_no_ve_ningun_colegio(client, db, entorno, cabeceras):
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()

    assert client.get("/colegios", headers=cabeceras["Docente"]).json()["total"] == 0
    assert client.get("/colegios/ubicaciones", headers=cabeceras["Docente"]).json() == {"departamentos": [], "distritos": []}


def test_cu013_provincia_obligatoria(client, entorno, sup):
    cuerpo = datos(entorno)
    del cuerpo["provincia"]

    respuesta = client.post("/colegios", json=cuerpo, headers=sup)

    assert respuesta.status_code == 422
    assert motivo(respuesta) == "validacion"
    assert [e["campo"] for e in respuesta.json()["errores"]] == ["provincia"]


@pytest.mark.parametrize("sin_distrito", [{"distrito": None}, {}], ids=["null", "omitido"])
def test_cu013_distrito_opcional(client, entorno, sup, sin_distrito):
    cuerpo = {k: v for k, v in datos(entorno).items() if k != "distrito"} | sin_distrito

    respuesta = client.post("/colegios", json=cuerpo, headers=sup)

    assert respuesta.status_code == 201
    assert (respuesta.json()["provincia"], respuesta.json()["distrito"]) == ("Yungay", None)


def test_cu013_editar_provincia_no_acepta_null_y_distrito_si(client, db, entorno, sup):
    id_colegio = entorno.colegios["Andino"].id_colegio

    sin_provincia = client.patch(f"/colegios/{id_colegio}", json={"provincia": None}, headers=sup)
    sin_distrito = client.patch(f"/colegios/{id_colegio}", json={"distrito": None}, headers=sup)

    assert (sin_provincia.status_code, motivo(sin_provincia)) == (422, "validacion")
    assert sin_provincia.json()["errores"][0]["campo"] == "provincia"
    assert sin_distrito.status_code == 200
    assert (sin_distrito.json()["provincia"], sin_distrito.json()["distrito"]) == ("Carhuaz", None)
    assert releer(db, Colegio, id_colegio).distrito is None


def test_cu013_el_directivo_no_tiene_acceso(client, entorno, cabeceras):
    assert client.get("/colegios", headers=cabeceras["Directivo"]).status_code == 403


def test_cu013_detalle_e_inexistente(client, entorno, sup):
    detalle = client.get(f"/colegios/{entorno.colegios['Lejano'].id_colegio}", headers=sup).json()

    assert detalle["provincia"] == "Lima"
    assert len(detalle["grados"]) == 6
    respuesta = client.get("/colegios/999", headers=sup)
    assert (respuesta.status_code, motivo(respuesta)) == (404, "colegio_no_encontrado")


def test_cu013_editar_campos_y_oferta(client, db, entorno, sup):
    id_colegio = entorno.colegios["Lejano"].id_colegio
    solo_quinto = oferta(entorno, "5.º", "6.º")

    respuesta = client.patch(
        f"/colegios/{id_colegio}",
        json={"nombre": "Colegio Lejano II", "distrito": None, "seccion": "A", "grados": solo_quinto},
        headers=sup,
    )

    assert respuesta.status_code == 409
    assert motivo(respuesta) == "grado_en_uso"
    assert "1 alumno activo" in respuesta.json()["detail"]

    respuesta = client.patch(
        f"/colegios/{id_colegio}",
        json={"nombre": "Colegio Lejano II", "distrito": None, "seccion": "A",
              "grados": oferta(entorno, "3.º", "4.º", dia=2), "programas": [entorno.programas["Comprensión Lectora"]]},
        headers=sup,
    )
    cuerpo = respuesta.json()
    assert (cuerpo["nombre"], cuerpo["distrito"], cuerpo["seccion"]) == ("Colegio Lejano II", None, "A")
    assert [(g["nombre"], g["dia_clase"]) for g in cuerpo["grados"]] == [("3.º", 2), ("4.º", 2)]
    assert releer(db, Colegio, id_colegio).modificado_por == entorno.supervisor.id_usuario
    campos = set(db.exec(select(Auditoria.campo).where(Auditoria.accion == "editar")).all())
    assert campos == {"nombre", "distrito", "seccion", "grados", "programas"}


def test_cu013_no_se_retira_un_grado_con_asignacion_ni_un_programa_con_alumnos(client, entorno, sup):
    andino = entorno.colegios["Andino"].id_colegio

    grados = client.patch(f"/colegios/{andino}", json={"grados": oferta(entorno, "6.º")}, headers=sup)
    programas = client.patch(f"/colegios/{andino}", json={"programas": [entorno.programas["Alfabetización"]]}, headers=sup)

    assert "1 alumno activo y 1 asignación vigente" in grados.json()["detail"]
    assert (programas.status_code, motivo(programas)) == (409, "programa_en_uso")


def test_cu013_editar_sin_cambios_no_audita(client, db, entorno, sup):
    andino = entorno.colegios["Andino"]
    for fila in db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == andino.id_colegio)).all():
        fila.dia_clase = 4
        db.add(fila)
    db.commit()

    respuesta = client.patch(
        f"/colegios/{andino.id_colegio}",
        json={"nombre": andino.nombre, "grados": oferta(entorno, *entorno.grados, dia=4),
              "programas": list(entorno.programas.values())},
        headers=sup,
    )

    assert respuesta.status_code == 200
    assert db.exec(select(Auditoria)).all() == []


@pytest.mark.parametrize(
    "cambio, codigo, esperado",
    [
        ({"nombre": None}, 422, "validacion"),
        ({"departamento": None}, 422, "validacion"),
        ({"nombre": "colegio lejano"}, 409, "colegio_duplicado"),
        ({"grados": [{"id_grado": 999, "dia_clase": 1}]}, 422, "grado_inexistente"),
    ],
)
def test_cu013_edicion_invalida(client, entorno, sup, cambio, codigo, esperado):
    respuesta = client.patch(f"/colegios/{entorno.colegios['Andino'].id_colegio}", json=cambio, headers=sup)

    assert respuesta.status_code == codigo
    assert motivo(respuesta) == esperado


def test_cu013_editar_inexistente(client, entorno, sup):
    assert client.patch("/colegios/999", json={"nombre": "X"}, headers=sup).status_code == 404


def test_cu013_inactivar_no_toca_alumnos_y_saca_al_colegio_del_alcance(client, db, entorno, cabeceras, sup):
    andino = entorno.colegios["Andino"].id_colegio

    respuesta = client.patch(f"/colegios/{andino}/desactivar", headers=sup)

    assert respuesta.json()["activo"] is False
    assert client.patch(f"/colegios/{andino}/desactivar", headers=sup).status_code == 200
    assert client.get("/alumnos", headers=sup, params={"id_colegio": andino}).json()["total"] == 2
    assert client.get("/colegios", headers=cabeceras["Docente"]).json()["total"] == 0
    assert client.patch(f"/colegios/{andino}/activar", headers=sup).json()["activo"] is True
    acciones = db.exec(select(Auditoria.accion).order_by(Auditoria.id_auditoria)).all()
    assert acciones == ["inactivar", "activar"]
    assert client.patch("/colegios/999/activar", headers=sup).status_code == 404


def test_cu013_agregar_un_grado_nuevo(client, db, entorno, sup):
    colegio = client.post("/colegios", json=datos(entorno), headers=sup).json()
    id_alumno_grado = entorno.grados["1.º"]
    crear_alumno(db, "Nina", "Nueva", db.get(Colegio, colegio["id"]), id_alumno_grado,
                 entorno.programas["Alfabetización"], entorno.supervisor.id_usuario)
    db.commit()

    respuesta = client.patch(
        f"/colegios/{colegio['id']}", json={"grados": oferta(entorno, "1.º", "4.º", dia=2)}, headers=sup
    )

    assert [(g["nombre"], g["dia_clase"]) for g in respuesta.json()["grados"]] == [("1.º", 2), ("4.º", 2)]
    assert len(db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == colegio["id"])).all()) == 2
