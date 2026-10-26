"""D67 · Un Docente con varios colegios (CU016): una asignación por colegio, cada una con su
inicio según el calendario de ese colegio, validadas todas antes de escribir."""
import pytest
from sqlmodel import select

from app.models.organizacion import DocenteColegioGrado, PeriodoAcademico, Usuario
from app.models.trazabilidad import Auditoria
from tests.conftest import asignar, cabecera_de, crear_particular, motivo, rangos_en_receso, releer


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def lejano_en_receso(db, entorno):
    """Lejano (calendario particular) culminó su PA2 ayer y hoy está en el PR2; Andino
    (general) sigue en el PA2."""
    return crear_particular(db, entorno, rangos_en_receso(entorno.hoy))


def colegio(entorno, nombre, *grados, **extra) -> dict:
    datos = {"id_colegio": entorno.colegios[nombre].id_colegio, **extra}
    if grados:
        datos["grados"] = [entorno.grados[g] for g in grados]
    return datos


def asignacion(entorno, *colegios) -> dict:
    return {"id_anio_escolar": entorno.anio.id_anio_escolar, "colegios": list(colegios)}


def nuevo_docente(entorno, *colegios) -> dict:
    return {
        "nombres": "Nuevo", "apellidos": "Docente", "dni": "60000001", "correo": "nuevo@sicedu.test",
        "id_rol": entorno.roles["Docente"], "asignacion": asignacion(entorno, *colegios),
    }


def filas(db, entorno, id_docente) -> dict[str, list[tuple]]:
    """{colegio ("Andino"/"Lejano"): [(PA, grado, retroactiva)]} de las asignaciones del docente."""
    db.expire_all()
    nombres = {c.id_colegio: nombre for nombre, c in entorno.colegios.items()}
    resultado = db.exec(
        select(DocenteColegioGrado, PeriodoAcademico.numero)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(DocenteColegioGrado.id_docente == id_docente)
    ).all()
    por_colegio: dict[str, list[tuple]] = {}
    for fila, numero in resultado:
        por_colegio.setdefault(nombres[fila.id_colegio], []).append((numero, fila.id_grado, fila.retroactiva))
    return {c: sorted(v) for c, v in por_colegio.items()}


def de(entorno, *triples) -> list[tuple]:
    """[(PA, nombre del grado, retroactiva)] -> [(PA, id_grado, retroactiva)], ordenado."""
    return sorted((pa, entorno.grados[g], r) for pa, g, r in triples)


def id_docente_de(db, correo="nuevo@sicedu.test") -> int:
    db.expire_all()
    return db.exec(select(Usuario).where(Usuario.correo == correo)).one().id_docente


# ── Alta ────────────────────────────────────────────────────────────────────────────

def test_d67_alta_con_dos_colegios_y_grados_distintos(client, db, entorno, sup):
    datos = nuevo_docente(entorno, colegio(entorno, "Lejano", "4.º", "5.º"), colegio(entorno, "Andino", "3.º"))

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["usuario"]["colegios_asignados"] == ["Colegio Andino", "Colegio Lejano"]
    assert filas(db, entorno, id_docente_de(db)) == {
        "Andino": de(entorno, *((pa, "3.º", False) for pa in (2, 3, 4))),
        "Lejano": de(entorno, *((pa, g, False) for pa in (2, 3, 4) for g in ("4.º", "5.º"))),
    }


def test_d67_conflicto_en_el_segundo_colegio_no_crea_nada(client, db, entorno, sup):
    antes = (len(db.exec(select(Usuario)).all()), len(db.exec(select(DocenteColegioGrado)).all()))
    # 1.º de Andino ya es de Rosa en el PA2.
    datos = nuevo_docente(entorno, colegio(entorno, "Lejano", "3.º"), colegio(entorno, "Andino", "1.º"))

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "grado_asignado")
    assert respuesta.json()["detail"] == (
        "No se puede guardar la asignación. El colegio Colegio Andino, grado 1.º, ya tiene un "
        f"Docente asignado en PA2 del año escolar {entorno.anio.nombre}."
    )
    db.expire_all()
    despues = len(db.exec(select(Usuario)).all()), len(db.exec(select(DocenteColegioGrado)).all())
    assert despues == antes


def test_d67_el_colegio_en_receso_exige_su_inicio(client, entorno, sup, lejano_en_receso):
    datos = nuevo_docente(entorno, colegio(entorno, "Andino", "3.º"), colegio(entorno, "Lejano", "3.º"))

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "inicio_asignacion_requerido")
    cuerpo = respuesta.json()
    assert (cuerpo["id_colegio"], cuerpo["periodo"]) == (entorno.colegios["Lejano"].id_colegio, "PR2")
    assert [e["campo"] for e in cuerpo["errores"]] == ["asignacion.colegios.1.inicio_asignacion"]


def test_d67_retroactivo_solo_en_el_colegio_en_receso(client, db, entorno, sup, lejano_en_receso):
    datos = nuevo_docente(
        entorno,
        # En Andino (PA2) la elección no aplica y se ignora.
        colegio(entorno, "Andino", "3.º", inicio_asignacion="proximo"),
        colegio(entorno, "Lejano", "3.º", inicio_asignacion="retroactivo"),
    )

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert respuesta.status_code == 201, respuesta.text
    assert filas(db, entorno, id_docente_de(db)) == {
        "Andino": de(entorno, (2, "3.º", False), (3, "3.º", False), (4, "3.º", False)),
        "Lejano": de(entorno, (2, "3.º", True), (3, "3.º", False), (4, "3.º", False)),
    }
    # El detalle trae un bloque por colegio, cada uno con su vigente y su retroactiva.
    detalle = client.get(f"/usuarios/{respuesta.json()['usuario']['id']}", headers=sup).json()["asignacion"]
    assert detalle["id_anio_escolar"] == entorno.anio.id_anio_escolar
    assert [(c["colegio"], [(p["numero"], p["vigente"], p["retroactiva"]) for p in c["periodos"]])
            for c in detalle["colegios"]] == [
        ("Colegio Andino", [(2, True, False), (3, False, False), (4, False, False)]),
        ("Colegio Lejano", [(2, False, True), (3, False, False), (4, False, False)]),
    ]
    assert [g["nombre"] for g in detalle["colegios"][1]["periodos"][0]["grados"]] == ["3.º"]


# ── Edición ─────────────────────────────────────────────────────────────────────────

def _vigente_de_andino(db, entorno) -> dict[int, object]:
    db.expire_all()
    return {
        f.id_grado: f.creado_en
        for f in db.exec(select(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == entorno.id_docente,
            DocenteColegioGrado.id_colegio == entorno.colegios["Andino"].id_colegio,
            DocenteColegioGrado.id_periodo_academico == entorno.periodos["vigente"].id_periodo_academico,
        ))
    }


def test_d67_agregar_un_colegio_conserva_el_otro(client, db, entorno, sup):
    antes = _vigente_de_andino(db, entorno)
    # Sin grados: en Andino conserva los que tiene; en Lejano (nuevo para él), todos.
    cambio = asignacion(entorno, colegio(entorno, "Andino"), colegio(entorno, "Lejano", "3.º"))

    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}", json={"asignacion": cambio}, headers=sup)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["colegios_asignados"] == ["Colegio Andino", "Colegio Lejano"]
    assert _vigente_de_andino(db, entorno) == antes
    actuales = filas(db, entorno, entorno.id_docente)
    assert actuales["Andino"] == de(entorno, *((pa, g, False) for pa in (1, 2, 3, 4) for g in ("1.º", "2.º")))
    assert actuales["Lejano"] == de(entorno, (2, "3.º", False), (3, "3.º", False), (4, "3.º", False))
    auditoria = db.exec(select(Auditoria).where(Auditoria.campo == "asignacion")).one()
    anio = entorno.anio.nombre
    assert auditoria.valor_anterior == f"Colegio Andino [1.º, 2.º] desde P2 {anio}"
    assert auditoria.valor_nuevo == (
        f"Colegio Andino [1.º, 2.º] desde P2 {anio}; Colegio Lejano [3.º] desde P2 {anio}"
    )


def test_d67_quitar_un_colegio_libera_solo_sus_pa_no_terminados(client, db, entorno, sup, lejano_en_receso):
    for clave in ("pasado", "vigente", "futuro"):
        asignar(db, entorno.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos[clave], 1)
    db.commit()
    andino = filas(db, entorno, entorno.id_docente)["Andino"]

    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}",
        json={"asignacion": asignacion(entorno, colegio(entorno, "Andino", "1.º", "2.º"))},
        headers=sup,
    )

    assert respuesta.status_code == 200, respuesta.text
    actuales = filas(db, entorno, entorno.id_docente)
    # El PA2 de Lejano ya culminó en su calendario (hoy está en el PR2): es historial.
    assert actuales["Lejano"] == de(entorno, (1, "3.º", False), (2, "3.º", False))
    # Andino gana el PA3 y el PA4 (que no tenía), sin perder lo que tenía.
    assert set(andino) <= set(actuales["Andino"])
    auditoria = db.exec(select(Auditoria).where(Auditoria.campo == "asignacion")).one()
    assert auditoria.valor_anterior == (
        f"Colegio Andino [1.º, 2.º] desde P2 {entorno.anio.nombre}; Colegio Lejano [3.º] desde P3 {entorno.anio.nombre}"
    )


def test_d67_cambiar_los_grados_de_un_colegio_no_toca_el_otro(client, db, entorno, sup):
    for clave in ("vigente", "futuro", "cuarto"):
        asignar(db, entorno.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos[clave], 1)
    db.commit()
    lejano_antes = {(f.id, f.creado_en) for f in db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_colegio == entorno.colegios["Lejano"].id_colegio))}

    cambio = asignacion(entorno, colegio(entorno, "Andino", "1.º"), colegio(entorno, "Lejano"))
    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}", json={"asignacion": cambio}, headers=sup)

    assert respuesta.status_code == 200, respuesta.text
    db.expire_all()
    lejano_despues = {(f.id, f.creado_en) for f in db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_colegio == entorno.colegios["Lejano"].id_colegio))}
    assert lejano_despues == lejano_antes
    assert set(_vigente_de_andino(db, entorno)) == {entorno.grados["1.º"]}


def test_d67_conflicto_en_la_edicion_no_escribe_el_primer_colegio(client, db, entorno, sup):
    otro = client.post(
        "/usuarios", json=nuevo_docente(entorno, colegio(entorno, "Lejano", "3.º")), headers=sup
    ).json()["usuario"]
    antes = filas(db, entorno, entorno.id_docente)

    cambio = asignacion(entorno, colegio(entorno, "Andino", "4.º"), colegio(entorno, "Lejano", "3.º"))
    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}", json={"asignacion": cambio}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "grado_asignado")
    assert filas(db, entorno, entorno.id_docente) == antes
    assert otro["colegios_asignados"] == ["Colegio Lejano"]


# ── Lo que ve el Docente ────────────────────────────────────────────────────────────

def test_d67_el_docente_ve_sus_dos_colegios(client, db, entorno, sup):
    cambio = asignacion(entorno, colegio(entorno, "Andino"), colegio(entorno, "Lejano", "3.º"))
    client.patch(f"/usuarios/{entorno.docente.id_usuario}", json={"asignacion": cambio}, headers=sup)
    cabecera = cabecera_de(db, releer(db, Usuario, entorno.docente.id_usuario))

    listado = client.get("/usuarios", params={"q": "Rosa"}, headers=sup).json()["items"]
    mias = client.get("/me/asignaciones", headers=cabecera).json()
    habilitaciones = client.get("/me/habilitaciones", headers=cabecera).json()["asignaciones"]

    assert [u["colegios_asignados"] for u in listado] == [["Colegio Andino", "Colegio Lejano"]]
    assert [(a["colegio"], [g["nombre"] for g in a["grados"]]) for a in mias] == [
        ("Colegio Andino", ["1.º", "2.º"]), ("Colegio Lejano", ["3.º"]),
    ]
    assert sorted({(a["id_colegio"], a["id_grado"]) for a in habilitaciones}) == [
        (entorno.colegios["Andino"].id_colegio, entorno.grados["1.º"]),
        (entorno.colegios["Andino"].id_colegio, entorno.grados["2.º"]),
        (entorno.colegios["Lejano"].id_colegio, entorno.grados["3.º"]),
    ]
