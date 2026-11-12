"""CU014 (semana 14): alumno con pertenencia pendiente, sin ningún intervalo."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.tiempo import ahora_utc
from app.models.organizacion import Alumno, AlumnoPertenencia, ColegioGrado, Grado
from app.models.trazabilidad import Auditoria
from app.services import asignacion_service, colegio_service, inicio_service, pertenencia
from app.services.dashboard import cobertura
from tests.conftest import a_las, agregar_seccion, crear_alumno, crear_colegio, hoy_iso, iniciar_actividad, motivo, seccion_de

COMPLETA = "Completa el grado, el subprograma y la sección, o deja vacíos el subprograma y la sección."
CONFIRMA = "Confirma desde qué fecha el alumno forma parte de este grupo."
USA_PERTENECE = "Este alumno todavía no tiene un inicio de pertenencia: usa 'Pertenece al grupo desde'."
ACTIVA_ANTES = "Activa al alumno antes de confirmar desde qué fecha forma parte de su grupo."
COMPRENSION = "Comprensión Lectora"
HOY_MIERCOLES = date(2026, 6, 17)
LUNES = date(2026, 6, 15)


def pendiente(db, entorno, nombres="Paca", colegio="Andino", grado=None, activo=True) -> Alumno:
    """La ficha que deja la importación de una nómina: sin subprograma, sección ni intervalos."""
    alumno = Alumno(
        nombres=nombres, apellidos="Sin Grupo", id_colegio=entorno.colegios[colegio].id_colegio,
        id_grado=entorno.grados[grado] if grado else None, fecha_registro=entorno.hoy, activo=activo,
        creado_por=entorno.supervisor.id_usuario, creado_en=ahora_utc(),
    )
    db.add(alumno)
    db.commit()
    db.refresh(alumno)
    return alumno


def antiguo(db, entorno, nombres="Rita", grado="2.º", activo=True) -> Alumno:
    """Alumno anterior a los intervalos: grupo completo en la ficha y ningún intervalo."""
    alumno = crear_alumno(
        db, nombres, "Antigua", entorno.colegios["Andino"], entorno.grados[grado], entorno.programas[COMPRENSION],
        entorno.supervisor.id_usuario, activo=False,
    )
    alumno.activo = activo
    db.add(alumno)
    db.commit()
    db.refresh(alumno)
    return alumno


def intervalos(db, alumno) -> list[AlumnoPertenencia]:
    db.expire_all()
    return list(db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno == alumno.id_alumno)).all())


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


def faltantes(*campos) -> list[tuple[str, str]]:
    return [(campo, COMPLETA) for campo in campos]


def nombres(respuesta) -> list[str]:
    return [a["nombres"] for a in respuesta.json()["items"]]


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def doc(cabeceras):
    return cabeceras["Docente"]


# ── 1. Listado ──────────────────────────────────────────────────────────────────────

def test_cu014_listado_marca_al_pendiente_con_sus_datos_en_null(client, db, entorno, sup):
    alumno = pendiente(db, entorno)

    item = next(a for a in client.get("/alumnos", headers=sup).json()["items"] if a["id"] == alumno.id_alumno)

    assert (item["grado"], item["programa"], item["ciclo"], item["seccion"], item["dni"]) == (None, None, None, None, None)
    assert (item["pertenencia_pendiente"], item["activo"], item["colegio"]["nombre"]) == (True, True, "Colegio Andino")


@pytest.mark.parametrize(
    "filtro, esperado",
    [
        ({"pertenencia": "pendiente"}, ["Paca"]),
        ({"pertenencia": "definida"}, ["Mario", "Luis", "Ana"]),
        ({}, ["Mario", "Luis", "Paca", "Ana"]),
    ],
    ids=["pendiente", "definida", "sin-filtro"],
)
def test_cu014_filtro_de_pertenencia(client, db, entorno, sup, filtro, esperado):
    pendiente(db, entorno)

    respuesta = client.get("/alumnos", params=filtro, headers=sup)

    assert nombres(respuesta) == esperado
    assert {a["pertenencia_pendiente"] for a in respuesta.json()["items"]} == {a == "Paca" for a in esperado}


def test_cu014_filtro_de_pertenencia_invalido(client, entorno, sup):
    assert client.get("/alumnos", params={"pertenencia": "otra"}, headers=sup).status_code == 422


@pytest.mark.parametrize(
    "filtro",
    [
        lambda e, a: {"id_seccion": seccion_de_entorno(e)},
        lambda e, a: {"id_programa": e.programas[COMPRENSION]},
        lambda e, a: {"ciclo": "III"},
        lambda e, a: {"id_grado": e.grados["3.º"]},
    ],
    ids=["seccion", "subprograma", "ciclo", "otro-grado"],
)
def test_cu014_un_pendiente_sin_el_dato_no_coincide_con_el_filtro(client, db, entorno, sup, filtro):
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.get("/alumnos", params=filtro(entorno, alumno), headers=sup)

    assert "Paca" not in nombres(respuesta)


def seccion_de_entorno(entorno) -> int:
    return entorno.alumnos["ana"].id_seccion


def test_cu014_filtro_por_grado_encuentra_al_pendiente_que_lo_tiene(client, db, entorno, sup):
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.get("/alumnos", params={"id_grado": alumno.id_grado, "pertenencia": "pendiente"}, headers=sup)

    assert nombres(respuesta) == ["Paca"]


def test_cu015_detalle_de_un_pendiente(client, db, entorno, sup):
    alumno = pendiente(db, entorno)
    url = f"/alumnos/{alumno.id_alumno}"

    detalle = client.get(url, headers=sup).json()

    assert (detalle["pertenencia_pendiente"], detalle["pertenece_desde"], detalle["grado"]) == (True, None, None)
    for pestana in ("resumen", "registro-vuelo", "rubrica", "lectura", "historial"):
        assert client.get(f"{url}/{pestana}", headers=sup).status_code == 200
    filas = client.get(f"{url}/registro-vuelo", headers=sup).json()
    assert {(f["entrada"], f["nivel_programa"]) for f in filas} <= {(None, None)}


# ── 2. Edición que sigue pendiente ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cuerpo",
    [
        lambda e: {"nombres": "Francisca", "apellidos": "Díaz"},
        lambda e: {"dni": "87654321"},
        lambda e: {"id_grado": e.grados["2.º"]},
    ],
    ids=["nombres", "dni", "grado"],
)
def test_cu014_editar_sin_grupo_sigue_pendiente(client, db, entorno, sup, cuerpo):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json=cuerpo(entorno), headers=sup)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["pertenencia_pendiente"] is True
    assert intervalos(db, alumno) == []


def test_cu014_el_grado_de_un_pendiente_debe_ofrecerlo_su_colegio(client, db, entorno, sup):
    solo_primero = crear_colegio(db, "Solo 1.º", [entorno.grados["1.º"]], [], entorno.supervisor.id_usuario)
    entorno.colegios["Solo"] = solo_primero
    db.commit()
    alumno = pendiente(db, entorno, colegio="Solo")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["2.º"]}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "grado_no_ofrecido")


def test_cu014_cambiar_de_colegio_vacia_el_grado_que_el_nuevo_no_ofrece(client, db, entorno, sup):
    solo_primero = crear_colegio(db, "Solo 1.º", [entorno.grados["1.º"]], [], entorno.supervisor.id_usuario)
    db.commit()
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_colegio": solo_primero.id_colegio}, headers=sup)

    assert respuesta.status_code == 200, respuesta.text
    assert (respuesta.json()["colegio"]["id"], respuesta.json()["grado"]) == (solo_primero.id_colegio, None)


def test_cu014_cambiar_de_colegio_conserva_el_grado_que_el_nuevo_ofrece(client, db, entorno, sup):
    lejano = entorno.colegios["Lejano"]
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_colegio": lejano.id_colegio}, headers=sup)

    assert (respuesta.json()["colegio"]["id"], respuesta.json()["grado"]["id"]) == (lejano.id_colegio, entorno.grados["2.º"])


def test_cu014_mover_a_un_pendiente_exige_un_colegio_activo(client, db, entorno, sup):
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_colegio": lejano.id_colegio}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "colegio_inactivo")


def test_cu014_la_edicion_parcial_queda_en_el_historial(client, db, entorno, sup):
    alumno = pendiente(db, entorno)

    client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["2.º"]}, headers=sup)

    [evento] = client.get(f"/alumnos/{alumno.id_alumno}/historial", headers=sup).json()["items"]
    assert (evento["accion"], evento["cambios"]) == ("editar", [{"campo": "Grado", "anterior": None, "nuevo": "2.º"}])


# ── 3. Grupo incompleto ─────────────────────────────────────────────────────────────

def test_cu014_subprograma_sin_seccion_con_varias_secciones(client, db, entorno, sup):
    andino = entorno.colegios["Andino"]
    agregar_seccion(db, andino, entorno.grados["2.º"], "B", entorno.supervisor.id_usuario)
    db.commit()
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}",
        json={"id_programa": entorno.programas[COMPRENSION], "pertenece_desde": hoy_iso()}, headers=sup,
    )

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert errores(respuesta) == faltantes("id_seccion")
    assert respuesta.json()["detail"] == COMPLETA


def test_cu014_seccion_inexistente_en_su_grado(client, db, entorno, sup):
    alumno = pendiente(db, entorno, grado="2.º")
    ajena = seccion_de(db, entorno.colegios["Lejano"], entorno.grados["2.º"])

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}",
        json={"id_programa": entorno.programas[COMPRENSION], "id_seccion": ajena.id_seccion, "pertenece_desde": hoy_iso()},
        headers=sup,
    )

    assert (respuesta.status_code, errores(respuesta)) == (422, faltantes("id_seccion"))


@pytest.mark.parametrize(
    "cuerpo, campos",
    [
        (lambda e, s: {"id_seccion": s, "id_programa": e.programas[COMPRENSION]}, ("id_grado",)),
        (lambda e, s: {"id_seccion": s}, ("id_grado", "id_programa")),
        (lambda e, s: {"id_programa": e.programas[COMPRENSION]}, ("id_grado", "id_seccion")),
    ],
    ids=["seccion-y-subprograma", "solo-seccion", "solo-subprograma"],
)
def test_cu014_grupo_incompleto_sin_grado(client, db, entorno, sup, cuerpo, campos):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}", json=cuerpo(entorno, seccion_de_entorno(entorno)), headers=sup
    )

    assert (respuesta.status_code, errores(respuesta)) == (422, faltantes(*campos))
    db.refresh(alumno)
    assert (alumno.id_grado, alumno.id_seccion, alumno.id_programa_actual) == (None, None, None)


def test_cu014_seccion_sin_subprograma(client, db, entorno, sup):
    alumno = pendiente(db, entorno, grado="2.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_seccion": seccion_de_entorno(entorno)}, headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (422, faltantes("id_programa"))


# ── 4. Completar el grupo ───────────────────────────────────────────────────────────

def completo(entorno, **cambios) -> dict:
    return {"id_grado": entorno.grados["2.º"], "id_programa": entorno.programas[COMPRENSION], **cambios}


def test_cu014_completar_el_grupo_sin_fecha(client, db, entorno, sup):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json=completo(entorno), headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (422, [("pertenece_desde", CONFIRMA)])
    assert intervalos(db, alumno) == []
    assert alumno.id_grado is None


def test_cu014_completar_el_grupo_con_fecha_abre_el_intervalo(client, db, entorno, sup):
    alumno = pendiente(db, entorno)
    hace_tres = entorno.hoy - timedelta(days=3)

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}", json=completo(entorno, pertenece_desde=hace_tres.isoformat()), headers=sup
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["pertenencia_pendiente"], cuerpo["ciclo"], cuerpo["seccion"]["nombre"]) == (False, "III", "Única")
    [intervalo] = intervalos(db, alumno)
    assert (intervalo.desde, intervalo.hasta, intervalo.id_grado) == (hace_tres, None, entorno.grados["2.º"])
    assert (intervalo.id_programa, intervalo.id_seccion) == (entorno.programas[COMPRENSION], seccion_de_entorno(entorno))
    fila = db.exec(
        select(Auditoria).where(Auditoria.id_registro == str(alumno.id_alumno), Auditoria.campo == "pertenencia")
    ).one()
    assert (fila.accion, fila.valor_nuevo) == ("editar", hace_tres.isoformat())


def test_cu014_completar_valida_la_regla_de_primer_grado(client, db, entorno, sup):
    alumno = pendiente(db, entorno, grado="1.º")

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}",
        json={"id_programa": entorno.programas[COMPRENSION], "pertenece_desde": hoy_iso()}, headers=sup,
    )

    assert (respuesta.status_code, motivo(respuesta)) == (422, "primer_grado_solo_alfabetizacion")


def test_cu014_cambio_grupo_desde_en_un_pendiente(client, db, entorno, sup):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["2.º"], "cambio_grupo_desde": hoy_iso()}, headers=sup
    )

    assert (respuesta.status_code, errores(respuesta)) == (422, [("cambio_grupo_desde", USA_PERTENECE)])


# ── 5. pertenece_desde con el grupo incompleto ──────────────────────────────────────

@pytest.mark.parametrize(
    "grado, campos",
    [(None, ("id_grado", "id_programa", "id_seccion")), ("2.º", ("id_programa", "id_seccion"))],
    ids=["sin-grado", "con-grado"],
)
def test_cu014_pertenece_desde_con_el_grupo_incompleto(client, db, entorno, sup, grado, campos):
    alumno = pendiente(db, entorno, grado=grado)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"pertenece_desde": hoy_iso()}, headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (422, faltantes(*campos))
    assert intervalos(db, alumno) == []


# ── 6. Alumno antiguo: grupo completo y sin intervalos ──────────────────────────────

def test_cu014_antiguo_editar_solo_los_nombres(client, db, entorno, sup):
    alumno = antiguo(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Rita María"}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["pertenencia_pendiente"]) == (200, True)
    assert intervalos(db, alumno) == []


def test_cu014_antiguo_pertenece_desde_abre_el_intervalo(client, db, entorno, sup):
    alumno = antiguo(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"pertenece_desde": hoy_iso()}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["pertenencia_pendiente"]) == (200, False)
    assert [i.id_grado for i in intervalos(db, alumno)] == [entorno.grados["2.º"]]


def test_cu014_antiguo_en_colegio_inactivo_confirma_su_grupo_como_antes(client, db, entorno, sup):
    alumno = antiguo(db, entorno)
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"pertenece_desde": hoy_iso()}, headers=sup)

    assert respuesta.status_code == 200, respuesta.text
    assert len(intervalos(db, alumno)) == 1


def test_cu014_antiguo_cambiar_de_grado_sin_fecha_vacia_seccion_y_subprograma(client, db, entorno, sup):
    alumno = antiguo(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["3.º"]}, headers=sup)

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200, respuesta.text
    assert (cuerpo["grado"]["nombre"], cuerpo["seccion"], cuerpo["programa"]) == ("3.º", None, None)
    assert cuerpo["pertenencia_pendiente"] is True


def test_cu014_no_se_abre_un_intervalo_con_el_grupo_incompleto(db, entorno):
    alumno = pendiente(db, entorno)
    movimiento = pertenencia.Movimiento(entorno.supervisor.id_usuario, ahora_utc())

    with pytest.raises(ValueError, match="grupo incompleto"):
        pertenencia.abrir(db, alumno, entorno.hoy, "pertenece_desde", movimiento)
    db.rollback()


# ── 7. Activar o inactivar ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("hacer", [pendiente, antiguo], ids=["sin-grupo", "grupo-completo"])
@pytest.mark.parametrize("cuerpo", [{}, {"aplicar_desde": "2020-01-01"}], ids=["sin-fecha", "fecha-ignorada"])
def test_cu014_activar_e_inactivar_a_un_pendiente_solo_cambia_el_estado(client, db, entorno, sup, hacer, cuerpo):
    alumno = hacer(db, entorno)
    url = f"/alumnos/{alumno.id_alumno}"

    inactivado = client.patch(f"{url}/desactivar", json=cuerpo, headers=sup)
    activado = client.patch(f"{url}/activar", json=cuerpo, headers=sup)

    assert (inactivado.status_code, inactivado.json()["activo"]) == (200, False)
    assert (activado.status_code, activado.json()["activo"], activado.json()["pertenencia_pendiente"]) == (200, True, True)
    assert intervalos(db, alumno) == []
    filas = db.exec(select(Auditoria).where(Auditoria.id_registro == str(alumno.id_alumno))).all()
    assert sorted((f.accion, f.campo) for f in filas) == [("activar", "activo"), ("inactivar", "activo")]


@pytest.mark.parametrize("ruta", ["activar", "desactivar"])
def test_cu014_con_pertenencia_aplicar_desde_es_obligatorio(client, entorno, sup, ruta):
    respuesta = client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}/{ruta}", json={}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert respuesta.json()["detail"] == "Los datos enviados no son válidos."
    assert errores(respuesta) == [("aplicar_desde", "Este campo es obligatorio.")]


def test_cu014_activar_a_un_pendiente_exige_un_colegio_activo(client, db, entorno, sup):
    alumno = pendiente(db, entorno, activo=False)
    andino = entorno.colegios["Andino"]
    andino.activo = False
    db.add(andino)
    db.commit()

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}/activar", json={}, headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "colegio_inactivo")


def test_cu014_el_docente_sigue_necesitando_una_actividad(client, db, entorno, doc):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}/desactivar", json={}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "actividad_requerida")


# ── 8. DP-5: alumno inactivo con pertenencia pendiente ──────────────────────────────

def test_dp5_inactivo_con_pertenece_desde(client, db, entorno, sup):
    alumno = pendiente(db, entorno, grado="2.º", activo=False)

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}", json=completo(entorno, pertenece_desde=hoy_iso()), headers=sup
    )

    assert (respuesta.status_code, errores(respuesta)) == (422, [("pertenece_desde", ACTIVA_ANTES)])


def test_dp5_inactivo_completa_su_ficha_y_confirma_despues_de_activarlo(client, db, entorno, sup):
    alumno = pendiente(db, entorno, activo=False)
    url = f"/alumnos/{alumno.id_alumno}"

    completado = client.patch(url, json=completo(entorno, nombres="Paula"), headers=sup)

    assert (completado.status_code, completado.json()["pertenencia_pendiente"]) == (200, True)
    assert completado.json()["programa"]["nombre"] == COMPRENSION
    assert intervalos(db, alumno) == []

    client.patch(f"{url}/activar", json={}, headers=sup)
    assert intervalos(db, alumno) == []

    confirmado = client.patch(url, json={"pertenece_desde": hoy_iso()}, headers=sup)
    assert (confirmado.status_code, confirmado.json()["pertenencia_pendiente"]) == (200, False)
    assert len(intervalos(db, alumno)) == 1


def test_dp5_inactivo_con_el_grupo_incompleto(client, db, entorno, sup):
    alumno = pendiente(db, entorno, activo=False)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_programa": entorno.programas[COMPRENSION]}, headers=sup)

    assert (respuesta.status_code, errores(respuesta)) == (422, faltantes("id_grado", "id_seccion"))


# ── 9. DP-4: alcance del Docente ────────────────────────────────────────────────────

@pytest.mark.parametrize("grado", [None, "3.º"], ids=["sin-grado", "grado-ajeno"])
def test_dp4_docente_ve_al_pendiente_de_su_colegio(client, db, entorno, doc, grado):
    alumno = pendiente(db, entorno, grado=grado)

    listado = client.get("/alumnos", headers=doc)
    detalle = client.get(f"/alumnos/{alumno.id_alumno}", headers=doc)

    assert "Paca" in nombres(listado)
    assert detalle.status_code == 200


def test_dp4_docente_no_ve_al_pendiente_de_otro_colegio(client, db, entorno, doc):
    alumno = pendiente(db, entorno, colegio="Lejano", grado="1.º")

    listado = client.get("/alumnos", headers=doc)
    detalle = client.get(f"/alumnos/{alumno.id_alumno}", headers=doc)
    pestana = client.get(f"/alumnos/{alumno.id_alumno}/historial", headers=doc)

    assert "Paca" not in nombres(listado)
    assert (detalle.status_code, motivo(detalle)) == (403, "alumno_fuera_de_alcance")
    assert pestana.status_code == 403


def test_dp4_docente_no_ve_por_colegio_a_un_alumno_con_pertenencia_de_grado_ajeno(client, db, entorno, doc):
    otro = crear_alumno(db, "Tito", "Tercero", entorno.colegios["Andino"], entorno.grados["3.º"],
                        entorno.programas[COMPRENSION], entorno.supervisor.id_usuario)
    db.commit()

    assert "Tito" not in nombres(client.get("/alumnos", headers=doc))
    assert client.get(f"/alumnos/{otro.id_alumno}", headers=doc).status_code == 403


def test_dp4_docente_corrige_el_nombre_de_un_pendiente_de_grado_ajeno(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno, grado="3.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Paquita", "dni": "11112222"}, headers=doc)

    assert (respuesta.status_code, respuesta.json()["nombres"]) == (200, "Paquita")


def test_dp4_docente_no_escribe_al_pendiente_de_otro_colegio(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno, colegio="Lejano", grado="1.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Otra"}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "alumno_fuera_de_alcance")


def test_dp4_docente_no_puede_poner_un_grado_ajeno(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["3.º"]}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "alumno_fuera_de_alcance")
    db.refresh(alumno)
    assert alumno.id_grado is None


def test_dp4_docente_pone_un_grado_de_su_alcance(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_grado": entorno.grados["2.º"]}, headers=doc)

    assert (respuesta.status_code, respuesta.json()["grado"]["nombre"]) == (200, "2.º")


def test_dp4_docente_completa_el_grupo_en_su_grado(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json=completo(entorno, pertenece_desde=hoy_iso()), headers=doc)

    assert (respuesta.status_code, respuesta.json()["pertenencia_pendiente"]) == (200, False)
    [intervalo] = intervalos(db, alumno)
    assert str(intervalo.id_actividad) == actividad["id"]


def test_dp4_docente_no_completa_el_grupo_en_un_grado_ajeno(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno, grado="3.º")

    respuesta = client.patch(
        f"/alumnos/{alumno.id_alumno}",
        json={"id_programa": entorno.programas[COMPRENSION], "pertenece_desde": hoy_iso()}, headers=doc,
    )

    assert (respuesta.status_code, motivo(respuesta)) == (403, "alumno_fuera_de_alcance")
    assert intervalos(db, alumno) == []


def test_dp4_docente_no_confirma_a_un_antiguo_de_grado_ajeno(client, db, entorno, doc, actividad):
    alumno = antiguo(db, entorno, grado="3.º")

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"pertenece_desde": hoy_iso()}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "alumno_fuera_de_alcance")


def test_dp4_docente_activa_a_un_pendiente_de_su_colegio(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno, grado="3.º", activo=False)

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}/activar", json={}, headers=doc)

    assert (respuesta.status_code, respuesta.json()["activo"]) == (200, True)


def test_dp4_docente_no_cambia_el_colegio_de_un_pendiente(client, db, entorno, doc, actividad):
    alumno = pendiente(db, entorno)
    lejano = entorno.colegios["Lejano"].id_colegio

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"id_colegio": lejano}, headers=doc)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "cambio_colegio_no_permitido")


# ── 10. Los demás módulos ───────────────────────────────────────────────────────────

@pytest.fixture
def miercoles(reloj):
    reloj.fijar(a_las(HOY_MIERCOLES, 9))
    return reloj


@pytest.fixture
def con_clase_los_lunes(miercoles, db, entorno):
    fila = db.get(ColegioGrado, (entorno.colegios["Andino"].id_colegio, entorno.grados["2.º"]))
    fila.dia_clase = 1
    db.add(fila)
    db.commit()


def test_cu014_el_pendiente_no_aparece_en_la_lista_de_asistencias(con_clase_los_lunes, client, db, entorno, cabeceras):
    ana = entorno.alumnos["ana"]
    colado = antiguo(db, entorno)

    respuesta = client.get(
        "/asistencias",
        params={"id_colegio": ana.id_colegio, "id_grado": ana.id_grado, "id_seccion": ana.id_seccion, "fecha": LUNES.isoformat()},
        headers=cabeceras["Docente"],
    )

    assert respuesta.status_code == 200, respuesta.text
    ids = {a["id_alumno"] for a in respuesta.json()["alumnos"]}
    assert ana.id_alumno in ids
    assert colado.id_alumno not in ids


def test_cu014_el_pendiente_no_esta_en_las_lecturas_del_grupo(db, entorno):
    """Asistencias y Lectura leen `lista_del_grupo`; Rúbrica y Registro de Vuelo, `alumnos_del_grupo_en_rango`."""
    ana = entorno.alumnos["ana"]
    colado = antiguo(db, entorno)
    grupo = (ana.id_colegio, ana.id_grado, ana.id_seccion)
    desde, hasta = entorno.anio.fecha_inicio, entorno.hoy

    assert colado.id_alumno not in {a.id_alumno for a, _ in pertenencia.lista_del_grupo(db, *grupo, hasta)}
    assert colado.id_alumno not in {a.id_alumno for a in pertenencia.alumnos_del_grupo(db, *grupo, hasta)}
    en_rango = {a.id_alumno for a in pertenencia.alumnos_del_grupo_en_rango(db, *grupo, desde, hasta)}
    assert ana.id_alumno in en_rango
    assert colado.id_alumno not in en_rango


def test_cu014_el_pendiente_no_sale_en_rubrica_ni_en_registro_de_vuelo(miercoles, client, db, entorno, cabeceras):
    ana = entorno.alumnos["ana"]
    grupo = {"id_colegio": ana.id_colegio, "id_grado": ana.id_grado, "id_seccion": ana.id_seccion,
             "id_programa": ana.id_programa_actual, "id_anio_escolar": entorno.anio.id_anio_escolar}
    antiguo(db, entorno)
    doc = cabeceras["Docente"]

    rubrica = client.get("/rubricas", params={**grupo, "mes": HOY_MIERCOLES.month}, headers=doc)
    vuelo = client.get("/registro-vuelo", params=grupo, headers=doc)

    assert (rubrica.status_code, vuelo.status_code) == (200, 200)
    assert "Torres" in rubrica.text
    assert "Torres" in vuelo.text
    assert "Antigua" not in rubrica.text
    assert "Antigua" not in vuelo.text


def test_cu014_el_pendiente_no_cuenta_como_atendido_en_el_dashboard(db, entorno):
    colado = antiguo(db, entorno)

    filas = cobertura._pertenencias(db, [colado.id_colegio], entorno.anio.fecha_inicio, entorno.hoy)

    assert entorno.alumnos["ana"].id_alumno in {f.id_alumno for f in filas}
    assert colado.id_alumno not in {f.id_alumno for f in filas}


def test_cu014_el_pendiente_no_cuenta_en_lo_que_el_docente_tiene_a_cargo(client, db, entorno, cabeceras):
    base = client.get("/me/asignaciones", headers=cabeceras["Docente"]).json()
    antiguo(db, entorno)
    pendiente(db, entorno, grado="2.º")

    assert client.get("/me/asignaciones", headers=cabeceras["Docente"]).json() == base
    _, totales = asignacion_service.a_cargo(db, entorno.docente)
    assert totales.cantidad_alumnos == 2


def test_cu014_el_pendiente_no_cuenta_como_beneficiario(db, entorno):
    antes = inicio_service.beneficiarios_activos(db)

    antiguo(db, entorno)
    pendiente(db, entorno)

    assert inicio_service.beneficiarios_activos(db) == antes


def test_cu014_el_pendiente_con_grado_bloquea_retirarlo_del_colegio(db, entorno):
    andino = entorno.colegios["Andino"]
    cuarto = db.get(Grado, entorno.grados["4.º"])
    assert colegio_service._uso_de_grado(db, andino.id_colegio, cuarto) is None

    pendiente(db, entorno, grado="4.º")

    assert "1 alumno activo" in colegio_service._uso_de_grado(db, andino.id_colegio, cuarto)


def test_cu014_el_pendiente_inactivo_con_grado_tambien_lo_bloquea(db, entorno):
    andino = entorno.colegios["Andino"]
    cuarto = db.get(Grado, entorno.grados["4.º"])

    pendiente(db, entorno, grado="4.º", activo=False)

    assert colegio_service._uso_de_grado(db, andino.id_colegio, cuarto) is not None


def test_cu019_el_cambio_de_un_pendiente_no_desaparece_de_la_actividad(client, db, entorno, cabeceras):
    doc = cabeceras["Docente"]
    actividad = iniciar_actividad(client, doc)
    alumno = pendiente(db, entorno)

    client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Paquita"}, headers=doc)

    detalle = client.get(f"/actividades/{actividad['id']}", headers=doc).json()
    assert detalle["cambios_por_modulo"][0]["total"] == 1
    [asignacion] = detalle["asignaciones"]
    assert asignacion["colegio"]["nombre"] == "Colegio Andino"
    assert (asignacion["grado"], asignacion["ciclo"], asignacion["seccion"]) == (None, None, None)


def test_cu019_asignaciones_mezcla_pendientes_y_alumnos_con_grupo(client, db, entorno, cabeceras):
    doc = cabeceras["Docente"]
    actividad = iniciar_actividad(client, doc)
    alumno = pendiente(db, entorno)

    client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Paquita"}, headers=doc)
    client.patch(f"/alumnos/{entorno.alumnos['ana'].id_alumno}", json={"nombres": "Anita"}, headers=doc)

    asignaciones = client.get(f"/actividades/{actividad['id']}", headers=doc).json()["asignaciones"]
    assert [a["grado"]["nombre"] if a["grado"] else None for a in asignaciones] == [None, "2.º"]


def test_cu021_el_registro_de_un_pendiente_sale_en_la_lista_de_la_actividad(client, db, entorno, cabeceras):
    doc = cabeceras["Docente"]
    actividad = iniciar_actividad(client, doc)
    alumno = pendiente(db, entorno)
    client.patch(f"/alumnos/{alumno.id_alumno}", json={"nombres": "Paquita"}, headers=doc)

    respuesta = client.get(
        f"/seguimiento/docentes/{entorno.id_docente}/actividades/{actividad['id']}/registros",
        params={"modulo": "alumnos"}, headers=cabeceras["Supervisor"],
    )

    assert respuesta.status_code == 200, respuesta.text
    [item] = respuesta.json()["items"]
    assert (item["id"], item["grado"], item["seccion"]) == (alumno.id_alumno, None, None)
    assert item["alumno"]["nombre"] == "Paquita Sin Grupo"
