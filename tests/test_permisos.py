"""Permisos excepcionales de corrección (CU028), T4."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.organizacion import AnioEscolar, PeriodoAcademico, PermisoCorreccion
from app.models.trazabilidad import Auditoria
from tests.conftest import a_las, crear_docente, crear_permiso, iso, motivo


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def url(entorno):
    return f"/usuarios/{entorno.usuarios['Docente'].id_usuario}/permisos"


def pedido(entorno, **cambios) -> dict:
    """Permiso válido: Lectura de Andino 1.º en el PA1 (su PR1 ya terminó), desde ahora y
    por un día."""
    return {
        "id_anio_escolar": entorno.anio.id_anio_escolar,
        "id_colegio": entorno.colegios["Andino"].id_colegio,
        "id_grado": entorno.grados["1.º"],
        "id_periodo_academico": entorno.periodos["pasado"].id_periodo_academico,
        "modulo": "lectura",
        "desde_ahora": True,
        "fin": iso(tiempo.ahora_utc() + timedelta(days=1)),
        "motivo": "Faltó registrar la lectura de marzo.",
        **cambios,
    }


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


# ── Otorgar ─────────────────────────────────────────────────────────────────────────

def test_cu028_otorgar_desde_ahora(client, db, entorno, sup, url):
    respuesta = client.post(url, json=pedido(entorno), headers=sup)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert (cuerpo["estado"], cuerpo["modulo"], cuerpo["periodo"]["nombre"]) == ("vigente", "lectura", "PA1")
    assert (cuerpo["otorgado_por"]["nombre"], cuerpo["motivo"]) == ("Sara Supervisora", "Faltó registrar la lectura de marzo.")
    auditoria = db.exec(select(Auditoria).where(Auditoria.tabla == "permiso_correccion")).one()
    assert (auditoria.accion, auditoria.id_permiso, auditoria.id_registro) == ("crear", cuerpo["id"], str(cuerpo["id"]))


def test_cu028_otorgar_programado(client, entorno, sup, url):
    inicio = tiempo.ahora_utc() + timedelta(hours=3)

    respuesta = client.post(url, json=pedido(entorno, desde_ahora=False, inicio=iso(inicio)), headers=sup)

    assert (respuesta.status_code, respuesta.json()["estado"]) == (201, "programado")


@pytest.mark.parametrize(
    "cambios, campo, mensaje",
    [
        ({"motivo": "   "}, "motivo", "Ingresa el motivo de la autorización."),
        ({"desde_ahora": False, "inicio": None}, "inicio",
         "Selecciona una fecha y hora futura o utiliza la opción ‘Desde ahora’."),
        ({"desde_ahora": False, "inicio": "2020-01-01T10:00:00Z"}, "inicio",
         "Selecciona una fecha y hora futura o utiliza la opción ‘Desde ahora’."),
        ({"fin": "2020-01-01T10:00:00Z"}, "fin", "El fin de vigencia debe ser posterior al inicio."),
        ({"id_grado": 999}, "id_grado", "El colegio no ofrece ese grado."),
        ({"id_anio_escolar": 999}, "id_anio_escolar", "No existe el año escolar."),
        ({"id_colegio": 999}, "id_colegio", "No existe el colegio."),
    ],
)
def test_cu028_validaciones_de_otorgar(client, entorno, sup, url, cambios, campo, mensaje):
    respuesta = client.post(url, json=pedido(entorno, **cambios), headers=sup)

    assert (respuesta.status_code, respuesta.json()["detail"]) == (422, mensaje)
    assert errores(respuesta) == [(campo, mensaje)]


def test_cu028_pa_de_otro_anio_es_422(client, db, entorno, sup, url):
    otro = AnioEscolar(nombre="1999", fecha_inicio=entorno.hoy.replace(year=1999), fecha_fin=entorno.hoy.replace(year=1999),
                       creado_por=1, creado_en=tiempo.ahora_utc())
    db.add(otro)
    db.flush()
    ajeno = PeriodoAcademico(id_anio_escolar=otro.id_anio_escolar, numero=1, creado_por=1, creado_en=tiempo.ahora_utc())
    db.add(ajeno)
    db.commit()

    respuesta = client.post(url, json=pedido(entorno, id_periodo_academico=ajeno.id_periodo_academico), headers=sup)

    assert errores(respuesta) == [("id_periodo_academico", "El periodo no pertenece al año escolar.")]


def test_cu028_pa_con_plazo_ordinario_no_requiere_permiso(client, entorno, sup, url):
    vigente = entorno.periodos["vigente"].id_periodo_academico

    respuesta = client.post(url, json=pedido(entorno, id_periodo_academico=vigente), headers=sup)

    assert errores(respuesta) == [(
        "id_periodo_academico",
        "Este periodo todavía admite regularización ordinaria y no requiere un permiso excepcional.",
    )]


def test_cu028_docente_inactivo(client, db, entorno, sup, url):
    rosa = entorno.usuarios["Docente"]
    rosa.activo = False
    db.add(rosa)
    db.commit()

    respuesta = client.post(url, json=pedido(entorno), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "docente_inactivo")
    assert respuesta.json()["detail"] == "La cuenta del Docente está inactiva. No se puede otorgar el permiso."


def test_cu028_superposicion_del_mismo_alcance(client, db, entorno, sup, url):
    existente = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º")

    superpuesto = client.post(url, json=pedido(entorno), headers=sup)
    otro_modulo = client.post(url, json=pedido(entorno, modulo="rubrica"), headers=sup)

    assert (superpuesto.status_code, motivo(superpuesto)) == (409, "permiso_superpuesto")
    assert superpuesto.json()["detail"] == (
        "Ya existe un permiso para este mismo alcance con una vigencia que coincide total o "
        "parcialmente con la propuesta."
    )
    assert superpuesto.json()["permiso_existente"]["id"] == existente.id_permiso
    assert otro_modulo.status_code == 201


def test_cu028_un_permiso_revocado_no_impide_otro(client, db, entorno, sup, url):
    crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º", revocado_por=1, revocado_en=tiempo.ahora_utc())

    assert client.post(url, json=pedido(entorno), headers=sup).status_code == 201


def test_cu028_solo_para_docentes(client, entorno, sup):
    directivo = entorno.usuarios["Directivo"].id_usuario

    respuesta = client.post(f"/usuarios/{directivo}/permisos", json=pedido(entorno), headers=sup)
    listado = client.get(f"/usuarios/{directivo}/permisos", headers=sup)

    assert (respuesta.status_code, listado.status_code) == (404, 404)


@pytest.mark.parametrize("rol, codigo", [(None, 401), ("Docente", 403), ("Directivo", 403)])
def test_cu028_solo_el_supervisor(client, entorno, cabeceras, url, rol, codigo):
    cabecera = cabeceras[rol] if rol else {}
    permiso = f"{url}/1"

    respuestas = [
        client.post(url, json=pedido(entorno), headers=cabecera),
        client.get(url, headers=cabecera),
        client.get(permiso, headers=cabecera),
        client.post(f"{permiso}/revocar", json={"motivo": "x"}, headers=cabecera),
        client.get("/permisos/opciones", params={"id_colegio": 1, "id_anio_escolar": 1}, headers=cabecera),
    ]

    assert [r.status_code for r in respuestas] == [codigo] * 5


# ── Estado, listado y detalle ───────────────────────────────────────────────────────

def test_cu028_estado_calculado_en_el_tiempo(client, db, entorno, sup, url, reloj):
    reloj.fijar(a_las(entorno.hoy, 10))
    inicio = tiempo.ahora_utc() + timedelta(hours=1)
    creado = client.post(
        url, json=pedido(entorno, desde_ahora=False, inicio=iso(inicio), fin=iso(inicio + timedelta(hours=2))),
        headers=sup,
    ).json()
    detalle = f"{url}/{creado['id']}"

    estados = [creado["estado"]]
    for horas in (1, 2):
        reloj.avanzar(hours=horas)
        estados.append(client.get(detalle, headers=sup).json()["estado"])

    assert estados == ["programado", "vigente", "vencido"]


def test_cu028_listado_del_mas_reciente_y_filtros(client, db, entorno, sup, url):
    ahora = tiempo.ahora_utc()
    viejo = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º", modulo="rubrica",
                          inicio=ahora - timedelta(days=3), fin=ahora - timedelta(days=2))
    nuevo = crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="lectura")
    viejo.otorgado_en = ahora - timedelta(days=4)
    db.add(viejo)
    db.commit()

    todos = client.get(url, headers=sup).json()
    vencidos = client.get(url, params={"estado": "vencido"}, headers=sup).json()
    de_lectura = client.get(url, params={"modulo": "lectura"}, headers=sup).json()

    assert [p["id"] for p in todos["items"]] == [nuevo.id_permiso, viejo.id_permiso]
    assert [p["estado"] for p in todos["items"]] == ["vigente", "vencido"]
    assert [p["id"] for p in vencidos["items"]] == [viejo.id_permiso]
    assert [p["id"] for p in de_lectura["items"]] == [nuevo.id_permiso]


def test_cu028_detalle_de_otro_docente_es_404(client, db, entorno, sup, url):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    ajeno = crear_permiso(db, entorno, otro.id_docente, "Andino", "1.º")

    assert client.get(f"{url}/{ajeno.id_permiso}", headers=sup).status_code == 404


# ── Revocar ─────────────────────────────────────────────────────────────────────────

def test_cu028_revocar(client, db, entorno, sup, url):
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º")

    respuesta = client.post(f"{url}/{permiso.id_permiso}/revocar", json={"motivo": " Error de alcance "}, headers=sup)

    cuerpo = respuesta.json()
    assert (cuerpo["estado"], cuerpo["revocado_por"]["nombre"], cuerpo["motivo_revocacion"]) == (
        "revocado", "Sara Supervisora", "Error de alcance"
    )
    fila = db.exec(select(Auditoria).where(Auditoria.tabla == "permiso_correccion", Auditoria.campo == "activo")).one()
    assert (fila.accion, fila.id_permiso) == ("inactivar", permiso.id_permiso)


def test_cu028_revocar_sin_motivo(client, db, entorno, sup, url):
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º")

    respuesta = client.post(f"{url}/{permiso.id_permiso}/revocar", json={"motivo": "  "}, headers=sup)

    assert errores(respuesta) == [("motivo", "Ingresa el motivo de la revocación.")]


def test_cu028_revocar_dos_veces(client, db, entorno, sup, url):
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º")
    revocar = f"{url}/{permiso.id_permiso}/revocar"
    client.post(revocar, json={"motivo": "Primera"}, headers=sup)

    respuesta = client.post(revocar, json={"motivo": "Segunda"}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["detail"]) == (
        409, "El permiso ya fue revocado. Se actualizó su información."
    )
    assert respuesta.json()["permiso"]["motivo_revocacion"] == "Primera"
    db.expire_all()
    assert len(db.exec(select(Auditoria).where(Auditoria.tabla == "permiso_correccion")).all()) == 2


def test_cu028_revocar_un_vencido(client, db, entorno, sup, url):
    ahora = tiempo.ahora_utc()
    permiso = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º",
                            inicio=ahora - timedelta(days=2), fin=ahora - timedelta(days=1))

    respuesta = client.post(f"{url}/{permiso.id_permiso}/revocar", json={"motivo": "Tarde"}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["detail"]) == (409, "El permiso ya venció y no requiere revocación.")
    assert db.get(PermisoCorreccion, permiso.id_permiso).revocado_en is None


# ── Opciones del modal ──────────────────────────────────────────────────────────────

def test_cu028_opciones_del_modal(client, entorno, sup):
    params = {"id_colegio": entorno.colegios["Lejano"].id_colegio, "id_anio_escolar": entorno.anio.id_anio_escolar}

    cuerpo = client.get("/permisos/opciones", params=params, headers=sup).json()

    # Solo el PA1 terminó su PR; no se limita a las asignaciones de ningún docente.
    assert [p["nombre"] for p in cuerpo["periodos"]] == ["PA1"]
    assert len(cuerpo["grados"]) == 6


def test_cu028_opciones_de_colegio_inexistente(client, entorno, sup):
    respuesta = client.get("/permisos/opciones", params={"id_colegio": 999, "id_anio_escolar": 1}, headers=sup)

    assert errores(respuesta) == [("id_colegio", "No existe el colegio.")]
