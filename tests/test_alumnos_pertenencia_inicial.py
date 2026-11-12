"""CU014: "Pertenece al grupo desde" al editar un alumno activo sin pertenencia (D65)."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.organizacion import AlumnoPertenencia
from app.models.trazabilidad import Auditoria
from tests.conftest import clase_guardada, crear_alumno, hoy_iso, motivo, seccion_de

FECHA_FUTURA = "Selecciona la fecha de hoy o una fecha anterior."
CONFIRMA = "Confirma desde qué fecha el alumno forma parte de este grupo."
YA_TIENE = "El alumno ya tiene un inicio de pertenencia registrado."
USA_PERTENECE = "Este alumno todavía no tiene un inicio de pertenencia: usa 'Pertenece al grupo desde'."


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def sin_pertenencia(db, entorno):
    """Rita: activa en 2.º de Andino, sin ningún intervalo de pertenencia."""
    alumno = crear_alumno(
        db, "Rita", "Sin Inicio", entorno.colegios["Andino"], entorno.grados["2.º"],
        entorno.programas["Comprensión Lectora"], entorno.supervisor.id_usuario, activo=False,
    )
    alumno.activo = True
    db.add(alumno)
    db.commit()
    db.refresh(alumno)
    return alumno


def intervalos(db, id_alumno) -> list[AlumnoPertenencia]:
    db.expire_all()
    return list(db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == id_alumno)).all())


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


def test_cu014_sin_pertenencia_con_fecha_abre_el_intervalo_y_se_audita(client, db, entorno, sup, sin_pertenencia):
    hace_cinco = entorno.hoy - timedelta(days=5)

    respuesta = client.patch(
        f"/alumnos/{sin_pertenencia.id_alumno}", json={"pertenece_desde": hace_cinco.isoformat()}, headers=sup
    )

    assert respuesta.status_code == 200
    [intervalo] = intervalos(db, sin_pertenencia.id_alumno)
    assert (intervalo.desde, intervalo.hasta, intervalo.id_grado, intervalo.creado_por) == (
        hace_cinco, None, entorno.grados["2.º"], entorno.supervisor.id_usuario,
    )
    fila = db.exec(
        select(Auditoria).where(Auditoria.id_registro == sin_pertenencia.id_alumno, Auditoria.campo == "pertenencia")
    ).one()
    assert (fila.accion, fila.valor_nuevo) == ("editar", hace_cinco.isoformat())
    detalle = client.get(f"/alumnos/{sin_pertenencia.id_alumno}", headers=sup).json()
    assert detalle["pertenece_desde"] == hace_cinco.isoformat()


def test_cu014_sin_pertenencia_el_intervalo_toma_el_grado_nuevo(client, db, entorno, sup, sin_pertenencia):
    tercero = seccion_de(db, entorno.colegios["Andino"], entorno.grados["3.º"])

    respuesta = client.patch(
        f"/alumnos/{sin_pertenencia.id_alumno}",
        json={"id_grado": entorno.grados["3.º"], "pertenece_desde": hoy_iso()}, headers=sup,
    )

    assert respuesta.status_code == 200
    [intervalo] = intervalos(db, sin_pertenencia.id_alumno)
    assert (intervalo.id_grado, intervalo.id_seccion, intervalo.desde) == (
        entorno.grados["3.º"], tercero.id_seccion, entorno.hoy,
    )


@pytest.mark.parametrize(
    "cuerpo, error",
    [
        (lambda e: {"id_programa": e.programas["Comprensión Lectora"]}, ("pertenece_desde", CONFIRMA)),
        (lambda e: {"pertenece_desde": (e.hoy + timedelta(days=1)).isoformat()}, ("pertenece_desde", FECHA_FUTURA)),
        (
            lambda e: {"id_grado": e.grados["3.º"], "pertenece_desde": hoy_iso(), "cambio_grupo_desde": hoy_iso()},
            ("cambio_grupo_desde", USA_PERTENECE),
        ),
    ],
    ids=["subprograma-sin-fecha", "fecha-futura", "con-cambio-grupo-desde"],
)
def test_cu014_sin_pertenencia_rechazos(client, db, entorno, sup, sin_pertenencia, cuerpo, error):
    respuesta = client.patch(f"/alumnos/{sin_pertenencia.id_alumno}", json=cuerpo(entorno), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert errores(respuesta) == [error]
    assert intervalos(db, sin_pertenencia.id_alumno) == []
    db.refresh(sin_pertenencia)
    assert (sin_pertenencia.nombres, sin_pertenencia.id_grado) == ("Rita", entorno.grados["2.º"])


def test_cu014_sin_pertenencia_sin_fecha_guarda_y_sigue_pendiente(client, db, entorno, sup, sin_pertenencia):
    # Semana 14: confirmar la pertenencia ya no es obligatorio para corregir la ficha.
    url = f"/alumnos/{sin_pertenencia.id_alumno}"

    corregido = client.patch(url, json={"nombres": "Rita María"}, headers=sup)
    vacio = client.patch(url, json={}, headers=sup)

    assert (corregido.status_code, corregido.json()["nombres"]) == (200, "Rita María")
    assert corregido.json()["pertenencia_pendiente"] is True
    assert (vacio.status_code, vacio.json()) == (200, corregido.json())
    assert intervalos(db, sin_pertenencia.id_alumno) == []


@pytest.mark.parametrize("alumno", ["ana", "inactivo"])
def test_cu014_pertenece_desde_se_rechaza_si_no_es_alumno_sin_pertenencia(client, db, entorno, sup, alumno):
    if alumno == "inactivo":
        # Sin intervalos, Iván tendría la pertenencia pendiente (ver test_alumnos_pendientes).
        ivan = entorno.alumnos["inactivo"]
        db.add(AlumnoPertenencia(
            id_alumno=ivan.id_alumno, id_colegio=ivan.id_colegio, id_grado=ivan.id_grado, id_seccion=ivan.id_seccion,
            id_programa=ivan.id_programa_actual, desde=entorno.hoy - timedelta(days=9),
            hasta=entorno.hoy - timedelta(days=2), creado_por=entorno.supervisor.id_usuario, creado_en=ahora_utc(),
        ))
        db.commit()
    id_alumno = entorno.alumnos[alumno].id_alumno
    antes = [(i.desde, i.hasta) for i in intervalos(db, id_alumno)]

    respuesta = client.patch(
        f"/alumnos/{id_alumno}", json={"nombres": "Otro", "pertenece_desde": hoy_iso()}, headers=sup
    )

    assert (respuesta.status_code, errores(respuesta)) == (422, [("pertenece_desde", YA_TIENE)])
    assert [(i.desde, i.hasta) for i in intervalos(db, id_alumno)] == antes


def test_cu014_sin_pertenencia_fecha_que_contradice_una_lista_guardada(client, db, entorno, sup, sin_pertenencia):
    # Ana ya tiene la lista de su clase de hace dos días, sin Rita: Rita no puede pertenecer
    # al grupo desde antes de esa clase (el mismo rechazo que al crear).
    clase_guardada(db, entorno.alumnos["ana"], entorno.hoy - timedelta(days=2))
    db.commit()

    respuesta = client.patch(
        f"/alumnos/{sin_pertenencia.id_alumno}",
        json={"nombres": "Rita María", "pertenece_desde": (entorno.hoy - timedelta(days=5)).isoformat()}, headers=sup,
    )

    assert (respuesta.status_code, motivo(respuesta)) == (409, "conflicto_listas_historicas")
    assert intervalos(db, sin_pertenencia.id_alumno) == []
    db.refresh(sin_pertenencia)
    assert sin_pertenencia.nombres == "Rita"


def test_cu014_sin_pertenencia_docente_sin_actividad(client, db, cabeceras, sin_pertenencia):
    respuesta = client.patch(
        f"/alumnos/{sin_pertenencia.id_alumno}", json={"pertenece_desde": hoy_iso()}, headers=cabeceras["Docente"]
    )

    assert respuesta.status_code == 409
    assert intervalos(db, sin_pertenencia.id_alumno) == []
