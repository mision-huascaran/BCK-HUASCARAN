"""Colegios (CU013). Gestiona el Supervisor; el Docente lee los de su alcance."""
import pytest
from sqlmodel import select

from app.models.organizacion import Colegio, ColegioGrado
from app.models.trazabilidad import Auditoria
from tests.conftest import crear_alumno, motivo, releer


def datos(entorno, **cambios):
    return {
        "nombre": "Colegio Nuevo",
        "departamento": "Áncash",
        "distrito": "Yungay",
        "provincia": "Yungay",
        "grados": [entorno.grados["1.º"], entorno.grados["2.º"], entorno.grados["2.º"]],
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
    assert [g["nombre"] for g in cuerpo["grados"]] == ["1.º", "2.º"]
    assert [p["nombre"] for p in cuerpo["programas"]] == ["Alfabetización"]
    assert db.exec(select(Auditoria.accion).where(Auditoria.tabla == "colegio")).all() == ["crear"]


@pytest.mark.parametrize("nombre", ["colegio andino", "  COLEGIO ÁNDINO ", "Colegio   Andino"])
def test_cu013_nombre_duplicado_sin_distinguir_mayusculas_tildes_ni_espacios(client, entorno, sup, nombre):
    respuesta = client.post("/colegios", json=datos(entorno, nombre=nombre), headers=sup)

    assert respuesta.status_code == 409
    assert motivo(respuesta) == "colegio_duplicado"


@pytest.mark.parametrize(
    "cambio, esperado",
    [
        ({"departamento": None}, "validacion"),
        ({"distrito": ""}, "validacion"),
        ({"grados": []}, "validacion"),
        ({"grados": [999]}, "grado_inexistente"),
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


def test_cu013_el_directivo_no_tiene_acceso(client, entorno, cabeceras):
    assert client.get("/colegios", headers=cabeceras["Directivo"]).status_code == 403


def test_cu013_detalle_e_inexistente(client, entorno, sup):
    detalle = client.get(f"/colegios/{entorno.colegios['Lejano'].id_colegio}", headers=sup).json()

    assert detalle["provincia"] is None
    assert len(detalle["grados"]) == 6
    respuesta = client.get("/colegios/999", headers=sup)
    assert (respuesta.status_code, motivo(respuesta)) == (404, "colegio_no_encontrado")


def test_cu013_editar_campos_y_oferta(client, db, entorno, sup):
    id_colegio = entorno.colegios["Lejano"].id_colegio
    solo_quinto = [entorno.grados["5.º"], entorno.grados["6.º"]]

    respuesta = client.patch(
        f"/colegios/{id_colegio}",
        json={"nombre": "Colegio Lejano II", "provincia": None, "seccion": "A", "grados": solo_quinto},
        headers=sup,
    )

    assert respuesta.status_code == 409
    assert motivo(respuesta) == "grado_en_uso"
    assert "1 alumno activo" in respuesta.json()["detail"]

    respuesta = client.patch(
        f"/colegios/{id_colegio}",
        json={"nombre": "Colegio Lejano II", "provincia": None, "seccion": "A",
              "grados": [entorno.grados["3.º"], entorno.grados["4.º"]], "programas": [entorno.programas["Comprensión Lectora"]]},
        headers=sup,
    )
    cuerpo = respuesta.json()
    assert (cuerpo["nombre"], cuerpo["provincia"], cuerpo["seccion"]) == ("Colegio Lejano II", None, "A")
    assert [g["nombre"] for g in cuerpo["grados"]] == ["3.º", "4.º"]
    assert releer(db, Colegio, id_colegio).modificado_por == entorno.supervisor.id_usuario
    campos = set(db.exec(select(Auditoria.campo).where(Auditoria.accion == "editar")).all())
    assert campos == {"nombre", "seccion", "grados", "programas"}


def test_cu013_no_se_retira_un_grado_con_asignacion_ni_un_programa_con_alumnos(client, entorno, sup):
    andino = entorno.colegios["Andino"].id_colegio

    grados = client.patch(f"/colegios/{andino}", json={"grados": [entorno.grados["6.º"]]}, headers=sup)
    programas = client.patch(f"/colegios/{andino}", json={"programas": [entorno.programas["Alfabetización"]]}, headers=sup)

    assert "1 alumno activo y 1 asignación vigente" in grados.json()["detail"]
    assert (programas.status_code, motivo(programas)) == (409, "programa_en_uso")


def test_cu013_editar_sin_cambios_no_audita(client, db, entorno, sup):
    andino = entorno.colegios["Andino"]

    respuesta = client.patch(
        f"/colegios/{andino.id_colegio}",
        json={"nombre": andino.nombre, "grados": list(entorno.grados.values()), "programas": list(entorno.programas.values())},
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
        ({"grados": [999]}, 422, "grado_inexistente"),
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
        f"/colegios/{colegio['id']}", json={"grados": [id_alumno_grado, entorno.grados["4.º"]]}, headers=sup
    )

    assert [g["nombre"] for g in respuesta.json()["grados"]] == ["1.º", "4.º"]
    assert len(db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == colegio["id"])).all()) == 2
