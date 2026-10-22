"""Secciones (CU013) y pertenencia histórica del alumno (CU014): T3."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.models.organizacion import AlumnoPertenencia, Seccion
from app.models.trazabilidad import Auditoria
from app.services import pertenencia
from app.services.pertenencia import MENSAJE_FECHA_FUTURA
from tests.conftest import agregar_seccion, crear_alumno, hoy_iso, motivo, seccion_de

FECHA_FUTURA_EXACTA = "Selecciona la fecha de hoy o una fecha anterior."


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def intervalos(db, id_alumno) -> list[tuple]:
    db.expire_all()
    filas = db.exec(
        select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == id_alumno).order_by(AlumnoPertenencia.desde)
    ).all()
    return [(f.desde, f.hasta, f.id_seccion, f.id_programa) for f in filas]


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


def nuevo(entorno, **cambios) -> dict:
    return {
        "nombres": "Nora",
        "apellidos": "Nueva",
        "id_colegio": entorno.colegios["Andino"].id_colegio,
        "id_grado": entorno.grados["2.º"],
        "id_programa": entorno.programas["Comprensión Lectora"],
        "pertenece_desde": hoy_iso(),
        **cambios,
    }


# ── Secciones en colegios (CU013) ───────────────────────────────────────────────────

def datos_colegio(entorno, secciones=None) -> dict:
    primero = {"id_grado": entorno.grados["1.º"], "dia_clase": 1}
    if secciones is not None:
        primero["secciones"] = secciones
    return {
        "nombre": "Colegio Nuevo",
        "departamento": "Áncash",
        "provincia": "Yungay",
        "grados": [primero, {"id_grado": entorno.grados["2.º"], "dia_clase": 2}],
        "programas": [entorno.programas["Alfabetización"]],
    }


def secciones_por_grado(cuerpo) -> dict[str, list[str]]:
    return {g["nombre"]: [s["nombre"] for s in g["secciones"]] for g in cuerpo["grados"]}


def test_cu013_crear_colegio_con_secciones(client, entorno, sup):
    cuerpo = client.post("/colegios", json=datos_colegio(entorno, ["A", "B", "a "]), headers=sup).json()

    # "a " es la misma sección que "A": no se duplica. El grado sin secciones nace con "Única".
    assert secciones_por_grado(cuerpo) == {"1.º": ["A", "B"], "2.º": ["Única"]}


def test_cu013_editar_agrega_secciones_sin_borrar_y_se_audita(client, db, entorno, sup):
    id_colegio = client.post("/colegios", json=datos_colegio(entorno, ["A", "B"]), headers=sup).json()["id"]
    grados = [
        {"id_grado": entorno.grados["1.º"], "dia_clase": 1, "secciones": ["C"]},
        {"id_grado": entorno.grados["2.º"], "dia_clase": 2},
        {"id_grado": entorno.grados["3.º"], "dia_clase": 3},
    ]

    cuerpo = client.patch(f"/colegios/{id_colegio}", json={"grados": grados}, headers=sup).json()

    # Las que no vinieron se conservan; el grado nuevo nace con "Única" y los demás no cambian.
    assert secciones_por_grado(cuerpo) == {"1.º": ["A", "B", "C"], "2.º": ["Única"], "3.º": ["Única"]}
    fila = db.exec(select(Auditoria).where(Auditoria.campo == "secciones")).one()
    assert (fila.valor_anterior, fila.valor_nuevo) == (
        "1.º: A, B; 2.º: Única", "1.º: A, B, C; 2.º: Única; 3.º: Única"
    )


def test_cu013_reenviar_las_mismas_secciones_no_cambia_nada(client, db, entorno, sup):
    id_colegio = client.post("/colegios", json=datos_colegio(entorno, ["A"]), headers=sup).json()["id"]

    respuesta = client.patch(
        f"/colegios/{id_colegio}", json={"grados": datos_colegio(entorno, ["a"])["grados"]}, headers=sup
    )

    assert respuesta.status_code == 200
    assert db.exec(select(Auditoria).where(Auditoria.accion == "editar")).all() == []


def test_cu013_cada_grado_tiene_al_menos_una_seccion(client, db, entorno, sup):
    cuerpo = client.post("/colegios", json=datos_colegio(entorno, []), headers=sup).json()

    assert all(g["secciones"] for g in cuerpo["grados"])
    assert {s.nombre for s in db.exec(select(Seccion).where(Seccion.id_colegio == cuerpo["id"])).all()} == {"Única"}


@pytest.mark.parametrize("secciones", [[""], ["   "], [None]])
def test_cu013_seccion_sin_nombre_es_422(client, entorno, sup, secciones):
    respuesta = client.post("/colegios", json=datos_colegio(entorno, secciones), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


def test_cu013_solo_el_supervisor_agrega_secciones(client, entorno, cabeceras):
    id_colegio = entorno.colegios["Andino"].id_colegio
    cuerpo = {"grados": [{"id_grado": entorno.grados["2.º"], "dia_clase": 1, "secciones": ["B"]}]}

    sin_token = client.patch(f"/colegios/{id_colegio}", json=cuerpo)
    docente = client.patch(f"/colegios/{id_colegio}", json=cuerpo, headers=cabeceras["Docente"])

    assert (sin_token.status_code, docente.status_code) == (401, 403)


def test_cu013_no_se_retira_un_grado_con_alumnos_en_su_historial(client, db, entorno, sup):
    """Un alumno inactivo sigue en la historia del grado: sus secciones no se borran."""
    lejano = entorno.colegios["Lejano"]
    crear_alumno(db, "Eva", "Antigua", lejano, entorno.grados["5.º"], entorno.programas["Comprensión Lectora"],
                 entorno.supervisor.id_usuario, activo=False)
    db.commit()
    detalle = client.get(f"/colegios/{lejano.id_colegio}", headers=sup).json()
    sin_quinto = [{"id_grado": g["id"], "dia_clase": 1} for g in detalle["grados"] if g["nombre"] != "5.º"]

    respuesta = client.patch(f"/colegios/{lejano.id_colegio}", json={"grados": sin_quinto}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "grado_en_uso")
    assert "alumnos en su historial" in respuesta.json()["detail"]


# ── Sección del alumno ──────────────────────────────────────────────────────────────

def test_cu014_con_una_sola_seccion_se_asigna_sola(client, entorno, sup):
    cuerpo = client.post("/alumnos", json=nuevo(entorno), headers=sup).json()

    assert cuerpo["seccion"]["nombre"] == "Única"


def test_cu014_con_varias_secciones_hay_que_elegirla(client, db, entorno, sup):
    andino = entorno.colegios["Andino"]
    b = agregar_seccion(db, andino, entorno.grados["2.º"], "B", entorno.supervisor.id_usuario)
    db.commit()

    sin_seccion = client.post("/alumnos", json=nuevo(entorno), headers=sup)
    con_seccion = client.post("/alumnos", json=nuevo(entorno, id_seccion=b.id_seccion), headers=sup)

    assert (sin_seccion.status_code, errores(sin_seccion)) == (422, [("id_seccion", "Selecciona la sección del alumno.")])
    assert (con_seccion.status_code, con_seccion.json()["seccion"]) == (201, {"id": b.id_seccion, "nombre": "B"})


def test_cu014_seccion_de_otro_grado_es_422(client, db, entorno, sup):
    otra = seccion_de(db, entorno.colegios["Andino"], entorno.grados["1.º"])

    respuesta = client.post("/alumnos", json=nuevo(entorno, id_seccion=otra.id_seccion), headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (
        422, [("id_seccion", "La sección no pertenece a ese colegio y grado.")]
    )


def test_cu014_listado_filtra_por_seccion(client, db, entorno, sup):
    andino = entorno.colegios["Andino"]
    b = agregar_seccion(db, andino, entorno.grados["2.º"], "B", entorno.supervisor.id_usuario)
    db.commit()
    client.post("/alumnos", json=nuevo(entorno, nombres="Berta", id_seccion=b.id_seccion), headers=sup)

    items = client.get("/alumnos", params={"id_seccion": b.id_seccion}, headers=sup).json()["items"]

    assert [(a["nombres"], a["seccion"]["nombre"]) for a in items] == [("Berta", "B")]


# ── Pertenencia: alta ───────────────────────────────────────────────────────────────

def test_cu014_pertenece_desde_futura_es_422_con_el_mensaje_exacto(client, entorno, sup):
    manana = (entorno.hoy + timedelta(days=1)).isoformat()

    respuesta = client.post("/alumnos", json=nuevo(entorno, pertenece_desde=manana), headers=sup)

    assert MENSAJE_FECHA_FUTURA == FECHA_FUTURA_EXACTA
    assert (respuesta.status_code, respuesta.json()["detail"]) == (422, FECHA_FUTURA_EXACTA)
    assert errores(respuesta) == [("pertenece_desde", FECHA_FUTURA_EXACTA)]


def test_cu014_pertenece_desde_es_obligatoria(client, entorno, sup):
    datos = nuevo(entorno)
    del datos["pertenece_desde"]

    respuesta = client.post("/alumnos", json=datos, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert [c for c, _ in errores(respuesta)] == ["pertenece_desde"]


def test_cu014_alta_con_fecha_pasada_abre_el_intervalo_y_el_detalle_la_muestra(client, db, entorno, sup):
    hace_diez = entorno.hoy - timedelta(days=10)

    creado = client.post("/alumnos", json=nuevo(entorno, pertenece_desde=hace_diez.isoformat()), headers=sup).json()

    assert [(d, h) for d, h, _, _ in intervalos(db, creado["id"])] == [(hace_diez, None)]
    assert client.get(f"/alumnos/{creado['id']}", headers=sup).json()["pertenece_desde"] == hace_diez.isoformat()


# ── Pertenencia: cambio de grupo ────────────────────────────────────────────────────

@pytest.fixture
def con_dos_secciones(client, db, entorno, sup):
    """Nora en la sección "Única" de 2.º de Andino desde hace 10 días, y la sección B."""
    andino = entorno.colegios["Andino"]
    unica = seccion_de(db, andino, entorno.grados["2.º"])
    b = agregar_seccion(db, andino, entorno.grados["2.º"], "B", entorno.supervisor.id_usuario)
    db.commit()
    desde = (entorno.hoy - timedelta(days=10)).isoformat()
    creado = client.post(
        "/alumnos", json=nuevo(entorno, id_seccion=unica.id_seccion, pertenece_desde=desde), headers=sup
    ).json()
    return creado["id"], unica.id_seccion, b.id_seccion


def test_cu014_cambio_de_grupo_cierra_y_abre_sin_superponer(client, db, entorno, sup, con_dos_secciones):
    nora, unica, b = con_dos_secciones
    hace_tres = entorno.hoy - timedelta(days=3)

    respuesta = client.patch(
        f"/alumnos/{nora}", json={"id_seccion": b, "cambio_grupo_desde": hace_tres.isoformat()}, headers=sup
    )

    assert respuesta.json()["seccion"]["nombre"] == "B"
    cl = entorno.programas["Comprensión Lectora"]
    assert intervalos(db, nora) == [
        (entorno.hoy - timedelta(days=10), hace_tres, unica, cl),
        (hace_tres, None, b, cl),
    ]
    fila = db.exec(select(Auditoria).where(Auditoria.campo == "pertenencia")).all()[-1]
    assert fila.valor_nuevo == hace_tres.isoformat()


def test_cu014_cambio_de_grupo_el_mismo_dia_reemplaza_el_intervalo(client, db, entorno, sup, con_dos_secciones):
    nora, _, b = con_dos_secciones
    desde = entorno.hoy - timedelta(days=10)

    client.patch(f"/alumnos/{nora}", json={"id_seccion": b, "cambio_grupo_desde": desde.isoformat()}, headers=sup)

    assert intervalos(db, nora) == [(desde, None, b, entorno.programas["Comprensión Lectora"])]


@pytest.mark.parametrize(
    "dias, campo_esperado",
    [
        (None, ("cambio_grupo_desde", "Indica desde qué fecha el alumno pertenece a su nuevo grupo.")),
        (1, ("cambio_grupo_desde", FECHA_FUTURA_EXACTA)),
        (-11, None),
    ],
)
def test_cu014_fecha_de_cambio_invalida(client, db, entorno, sup, con_dos_secciones, dias, campo_esperado):
    nora, unica, b = con_dos_secciones
    cuerpo = {"id_seccion": b}
    if dias is not None:
        cuerpo["cambio_grupo_desde"] = (entorno.hoy + timedelta(days=dias)).isoformat()

    respuesta = client.patch(f"/alumnos/{nora}", json=cuerpo, headers=sup)

    assert respuesta.status_code == 422
    campo, mensaje = errores(respuesta)[0]
    if campo_esperado is None:
        # Anterior al inicio del grupo actual: rompería el orden del historial.
        inicio = (entorno.hoy - timedelta(days=10)).strftime("%d/%m/%Y")
        assert (campo, mensaje) == ("cambio_grupo_desde", f"Selecciona el {inicio} o una fecha posterior: ese día empezó su grupo actual.")
    else:
        obtenido = (campo, mensaje)
        assert obtenido == campo_esperado
    assert [s for _, _, s, _ in intervalos(db, nora)] == [unica]


def test_cu014_cambiar_solo_el_nombre_no_toca_la_pertenencia(client, db, entorno, sup, con_dos_secciones):
    nora = con_dos_secciones[0]
    antes = intervalos(db, nora)

    respuesta = client.patch(f"/alumnos/{nora}", json={"nombres": "Norita"}, headers=sup)

    assert respuesta.status_code == 200
    assert intervalos(db, nora) == antes
    assert db.exec(select(Auditoria).where(Auditoria.campo == "pertenencia", Auditoria.accion == "editar")).all() == []


def test_cu014_cambiar_de_grado_toma_la_unica_seccion_del_grado_nuevo(client, db, entorno, sup):
    ana = entorno.alumnos["ana"]
    tercero = seccion_de(db, entorno.colegios["Andino"], entorno.grados["3.º"])

    respuesta = client.patch(
        f"/alumnos/{ana.id_alumno}", json={"id_grado": entorno.grados["3.º"], "cambio_grupo_desde": hoy_iso()},
        headers=sup,
    )

    assert respuesta.json()["seccion"] == {"id": tercero.id_seccion, "nombre": "Única"}


# ── Pertenencia: inactivar y activar ────────────────────────────────────────────────

def test_cu014_inactivar_y_activar_con_fecha(client, db, entorno, sup, con_dos_secciones):
    nora, unica, _ = con_dos_secciones
    hace_cinco, hace_dos = entorno.hoy - timedelta(days=5), entorno.hoy - timedelta(days=2)
    url = f"/alumnos/{nora}"

    client.patch(f"{url}/desactivar", json={"aplicar_desde": hace_cinco.isoformat()}, headers=sup)
    antes_de_salir = client.patch(
        f"{url}/activar", json={"aplicar_desde": (hace_cinco - timedelta(days=1)).isoformat()}, headers=sup
    )
    client.patch(f"{url}/activar", json={"aplicar_desde": hace_dos.isoformat()}, headers=sup)

    assert antes_de_salir.status_code == 422
    assert errores(antes_de_salir)[0][0] == "aplicar_desde"
    assert [(d, h, s) for d, h, s, _ in intervalos(db, nora)] == [
        (entorno.hoy - timedelta(days=10), hace_cinco, unica),
        (hace_dos, None, unica),
    ]


@pytest.mark.parametrize("cuerpo", [None, {}, {"aplicar_desde": "mañana"}])
def test_cu014_inactivar_exige_aplicar_desde(client, entorno, sup, cuerpo):
    respuesta = client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/desactivar", json=cuerpo, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")


def test_cu014_inactivar_con_fecha_futura_es_422(client, entorno, sup):
    manana = (entorno.hoy + timedelta(days=1)).isoformat()

    respuesta = client.patch(
        f"/alumnos/{entorno.alumnos['ana'].id_alumno}/desactivar", json={"aplicar_desde": manana}, headers=sup
    )

    assert errores(respuesta) == [("aplicar_desde", FECHA_FUTURA_EXACTA)]


def test_cu014_inactivar_el_mismo_dia_en_que_empezo_borra_el_intervalo_vacio(client, db, entorno, sup):
    creado = client.post("/alumnos", json=nuevo(entorno), headers=sup).json()

    client.patch(f"/alumnos/{creado['id']}/desactivar", json={"aplicar_desde": hoy_iso()}, headers=sup)

    assert intervalos(db, creado["id"]) == []


def test_cu014_el_docente_necesita_actividad_para_cambiar_la_pertenencia(client, entorno, cabeceras):
    respuesta = client.patch(
        f"/alumnos/{entorno.alumnos['ana'].id_alumno}/desactivar", json={"aplicar_desde": hoy_iso()},
        headers=cabeceras["Docente"],
    )

    assert respuesta.status_code == 409


# ── Consultas reutilizables ─────────────────────────────────────────────────────────

def test_alumnos_del_grupo_segun_la_fecha(client, db, entorno, sup, con_dos_secciones):
    nora, unica, b = con_dos_secciones
    hace_tres = entorno.hoy - timedelta(days=3)
    client.patch(f"/alumnos/{nora}", json={"id_seccion": b, "cambio_grupo_desde": hace_tres.isoformat()}, headers=sup)
    db.expire_all()
    andino, segundo = entorno.colegios["Andino"].id_colegio, entorno.grados["2.º"]

    def en(seccion, dia, **extra):
        return [a.nombres for a in pertenencia.alumnos_del_grupo(db, andino, segundo, seccion, dia, **extra)]

    assert "Nora" in en(unica, hace_tres - timedelta(days=1))
    assert "Nora" not in en(unica, hace_tres)
    assert en(b, hace_tres) == ["Nora"]
    assert en(b, hace_tres - timedelta(days=1)) == []
    assert en(b, hace_tres, id_programa=entorno.programas["Alfabetización"]) == []
    en_rango = pertenencia.alumnos_del_grupo_en_rango(
        db, andino, segundo, b, hace_tres - timedelta(days=5), hace_tres - timedelta(days=1)
    )
    assert en_rango == []
    en_rango = pertenencia.alumnos_del_grupo_en_rango(db, andino, segundo, b, hace_tres - timedelta(days=5), hace_tres)
    assert [a.nombres for a in en_rango] == ["Nora"]
    assert pertenencia.pertenencia_en(db, nora, entorno.hoy - timedelta(days=11)) is None
    assert pertenencia.pertenencia_en(db, nora, hace_tres).id_seccion == b
