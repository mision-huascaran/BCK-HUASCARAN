"""CU014 (semana 14): DNI opcional del alumno, de 8 dígitos y único entre alumnos."""
import pytest
from sqlmodel import select

from app.models.organizacion import Alumno
from app.models.trazabilidad import Auditoria
from app.services import alumno_service
from tests.conftest import crear_alumno, hoy_iso, motivo

FORMATO = "El DNI debe tener exactamente 8 dígitos."
DUPLICADO = "Ya existe un alumno registrado con ese DNI."
DNI = "12345678"


def nuevo(entorno, **cambios) -> dict:
    return {
        "nombres": "Nora", "apellidos": "Nueva", "id_colegio": entorno.colegios["Andino"].id_colegio,
        "id_grado": entorno.grados["2.º"], "id_programa": entorno.programas["Comprensión Lectora"],
        "pertenece_desde": hoy_iso(), **cambios,
    }


def con_dni(db, alumno: Alumno, dni: str = DNI) -> Alumno:
    alumno.dni = dni
    db.add(alumno)
    db.commit()
    return alumno


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def url_ana(entorno):
    return f"/alumnos/{entorno.alumnos['ana'].id_alumno}"


# ── Crear ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "enviado, guardado",
    [(DNI, DNI), (f"  {DNI} ", DNI), ("01234567", "01234567"), ("", None), ("   ", None), (None, None)],
    ids=["normal", "con-espacios", "cero-inicial", "vacio", "blanco", "null"],
)
def test_crear_con_dni(client, entorno, sup, enviado, guardado):
    respuesta = client.post("/alumnos", json=nuevo(entorno, dni=enviado), headers=sup)

    assert (respuesta.status_code, respuesta.json()["dni"]) == (201, guardado)


def test_crear_sin_dni(client, entorno, sup):
    respuesta = client.post("/alumnos", json=nuevo(entorno), headers=sup)

    assert (respuesta.status_code, respuesta.json()["dni"]) == (201, None)


@pytest.mark.parametrize("dni", ["1234567", "123456789", "1234567a", "١٢٣٤٥٦٧٨", "1234 678"])
def test_crear_con_dni_de_formato_invalido(client, entorno, sup, dni):
    respuesta = client.post("/alumnos", json=nuevo(entorno, dni=dni), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert errores(respuesta) == [("dni", FORMATO)]


def test_crear_con_dni_duplicado(client, db, entorno, sup):
    con_dni(db, entorno.alumnos["luis"])

    respuesta = client.post("/alumnos", json=nuevo(entorno, dni=DNI), headers=sup)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (409, "dni_duplicado", DUPLICADO)


def test_dos_alumnos_pueden_no_tener_dni(client, entorno, sup):
    assert client.post("/alumnos", json=nuevo(entorno, dni=""), headers=sup).status_code == 201
    assert client.post("/alumnos", json=nuevo(entorno, nombres="Otra", dni=None), headers=sup).status_code == 201


def test_un_usuario_con_el_mismo_dni_no_choca(client, entorno, sup):
    respuesta = client.post("/alumnos", json=nuevo(entorno, dni=entorno.supervisor.dni), headers=sup)

    assert (respuesta.status_code, respuesta.json()["dni"]) == (201, entorno.supervisor.dni)


def test_crear_traduce_la_carrera_a_409(client, db, entorno, sup, monkeypatch):
    con_dni(db, entorno.alumnos["luis"])
    monkeypatch.setattr(alumno_service, "_dni_valido", lambda _db, dni, excepto=None: dni)

    respuesta = client.post("/alumnos", json=nuevo(entorno, dni=DNI), headers=sup)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (409, "dni_duplicado", DUPLICADO)


# ── Editar ──────────────────────────────────────────────────────────────────────────

def test_editar_agrega_y_cambia_el_dni_sin_tocar_el_grupo(client, db, entorno, sup, url_ana):
    primero = client.patch(url_ana, json={"dni": DNI}, headers=sup)
    segundo = client.patch(url_ana, json={"dni": "87654321"}, headers=sup)

    assert (primero.status_code, primero.json()["dni"]) == (200, DNI)
    assert (segundo.status_code, segundo.json()["dni"], segundo.json()["grado"]["nombre"]) == (200, "87654321", "2.º")


@pytest.mark.parametrize("vacio", [None, "", "   "], ids=["null", "vacio", "blanco"])
def test_editar_quita_el_dni(client, db, entorno, sup, url_ana, vacio):
    con_dni(db, entorno.alumnos["ana"])

    respuesta = client.patch(url_ana, json={"dni": vacio}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["dni"]) == (200, None)


def test_editar_con_formato_invalido(client, entorno, sup, url_ana):
    respuesta = client.patch(url_ana, json={"dni": "123"}, headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (422, [("dni", FORMATO)])


def test_editar_con_el_dni_de_un_inactivo_de_otro_colegio(client, db, entorno, sup, url_ana):
    otro = crear_alumno(db, "Otro", "Lejano", entorno.colegios["Lejano"], entorno.grados["3.º"],
                        entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario, activo=False)
    con_dni(db, otro)

    respuesta = client.patch(url_ana, json={"dni": DNI}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (409, "dni_duplicado", DUPLICADO)


def test_editar_con_su_propio_dni_no_cambia_nada(client, db, entorno, sup, url_ana):
    ana = con_dni(db, entorno.alumnos["ana"])

    respuesta = client.patch(url_ana, json={"dni": DNI}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["dni"]) == (200, DNI)
    assert db.exec(select(Auditoria).where(Auditoria.tabla == "alumno", Auditoria.id_registro == str(ana.id_alumno))).all() == []


def test_editar_traduce_la_carrera_a_409(client, db, entorno, sup, url_ana, monkeypatch):
    con_dni(db, entorno.alumnos["luis"])
    monkeypatch.setattr(alumno_service, "_dni_valido", lambda _db, dni, excepto=None: dni)

    respuesta = client.patch(url_ana, json={"dni": DNI}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (409, "dni_duplicado", DUPLICADO)
    db.expire_all()
    assert db.get(Alumno, entorno.alumnos["ana"].id_alumno).dni is None


# ── Historial ───────────────────────────────────────────────────────────────────────

def test_historial_muestra_la_etiqueta_dni(client, entorno, sup, url_ana):
    client.patch(url_ana, json={"dni": DNI}, headers=sup)

    [evento] = client.get(f"{url_ana}/historial", headers=sup).json()["items"]

    assert (evento["accion"], evento["cambios"]) == ("editar", [{"campo": "DNI", "anterior": None, "nuevo": DNI}])
