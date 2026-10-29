"""Permisos de corrección en lote, todo o nada (CU028, Tanda I)."""
from datetime import timedelta, timezone

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.organizacion import PermisoCorreccion
from app.models.trazabilidad import Auditoria
from tests.conftest import crear_permiso, iso, motivo

SUPERPOSICION = (
    "Ya existe un permiso para este mismo alcance con una vigencia que coincide total o "
    "parcialmente con la propuesta."
)


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def url(entorno):
    return f"/usuarios/{entorno.usuarios['Docente'].id_usuario}/permisos/lote"


def lote(entorno, combinaciones=None, **cambios) -> dict:
    """Lote válido en Andino, PA1 (su PR1 ya terminó), desde ahora y por un día: por
    defecto, Asistencias y Lectura de 1.º y Rúbrica de 2.º."""
    return {
        "id_anio_escolar": entorno.anio.id_anio_escolar,
        "id_colegio": entorno.colegios["Andino"].id_colegio,
        "id_periodo_academico": entorno.periodos["pasado"].id_periodo_academico,
        "desde_ahora": True,
        "fin": iso(tiempo.ahora_utc() + timedelta(days=1)),
        "motivo": "Regularizar PA1 por reemplazo",
        "combinaciones": combinaciones or [
            {"id_grado": entorno.grados["1.º"], "modulo": "asistencias"},
            {"id_grado": entorno.grados["1.º"], "modulo": "lectura"},
            {"id_grado": entorno.grados["2.º"], "modulo": "rubrica"},
        ],
        **cambios,
    }


def permisos_en_bd(db) -> list[PermisoCorreccion]:
    db.expire_all()
    return list(db.exec(select(PermisoCorreccion)).all())


def errores(respuesta) -> list[tuple[str, str]]:
    return [(e["campo"], e["mensaje"]) for e in respuesta.json().get("errores", [])]


def test_lote_valido_crea_todos_en_orden_con_su_auditoria(client, db, entorno, sup, url):
    pedido = lote(entorno)

    respuesta = client.post(url, json=pedido, headers=sup)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert [(p["grado"]["id"], p["modulo"]) for p in cuerpo] == [
        (c["id_grado"], c["modulo"]) for c in pedido["combinaciones"]
    ]
    assert {(p["estado"], p["motivo"], p["periodo"]["nombre"]) for p in cuerpo} == {
        ("vigente", "Regularizar PA1 por reemplazo", "PA1")
    }
    assert len(permisos_en_bd(db)) == 3
    auditorias = db.exec(select(Auditoria).where(Auditoria.tabla == "permiso_correccion")).all()
    assert sorted((a.accion, a.id_permiso) for a in auditorias) == sorted(("crear", p["id"]) for p in cuerpo)


def test_lote_desde_ahora_comparte_el_inicio(client, entorno, sup, url):
    cuerpo = client.post(url, json=lote(entorno), headers=sup).json()

    assert len({p["inicio"] for p in cuerpo}) == 1
    assert len({p["fin"] for p in cuerpo}) == 1


def test_lote_con_superposicion_no_escribe_nada(client, db, entorno, sup, url):
    existente = crear_permiso(db, entorno, entorno.id_docente, "Andino", "1.º", modulo="lectura")

    respuesta = client.post(url, json=lote(entorno), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "permiso_superpuesto")
    assert respuesta.json()["detail"] == SUPERPOSICION
    [rechazada] = respuesta.json()["combinaciones_rechazadas"]
    assert rechazada == {
        "indice": 1, "id_grado": entorno.grados["1.º"], "modulo": "lectura", "motivo": "permiso_superpuesto",
        "detail": SUPERPOSICION, "permiso_existente": rechazada["permiso_existente"],
    }
    assert rechazada["permiso_existente"]["id"] == existente.id_permiso
    assert [p.id_permiso for p in permisos_en_bd(db)] == [existente.id_permiso]


def test_lote_informa_todas_las_combinaciones_rechazadas(client, db, entorno, sup, url):
    crear_permiso(db, entorno, entorno.id_docente, "Andino", "2.º", modulo="rubrica")
    combinaciones = [
        {"id_grado": entorno.grados["1.º"], "modulo": "asistencias"},
        {"id_grado": 999, "modulo": "lectura"},
        {"id_grado": entorno.grados["2.º"], "modulo": "rubrica"},
    ]

    respuesta = client.post(url, json=lote(entorno, combinaciones), headers=sup)

    # Manda la primera que falló: el grado que el colegio no ofrece.
    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert errores(respuesta) == [("combinaciones.1.id_grado", "El colegio no ofrece ese grado.")]
    rechazadas = respuesta.json()["combinaciones_rechazadas"]
    assert [(r["indice"], r["id_grado"], r["modulo"], r["motivo"]) for r in rechazadas] == [
        (1, 999, "lectura", "validacion"), (2, entorno.grados["2.º"], "rubrica", "permiso_superpuesto"),
    ]
    assert "permiso_existente" not in rechazadas[0]
    assert len(permisos_en_bd(db)) == 1


def test_lote_combinacion_repetida_es_422(client, entorno, sup, url):
    repetida = {"id_grado": entorno.grados["1.º"], "modulo": "lectura"}

    respuesta = client.post(url, json=lote(entorno, [repetida, dict(repetida)]), headers=sup)

    assert respuesta.status_code == 422
    assert errores(respuesta) == [("combinaciones", "Hay combinaciones de grado y módulo repetidas.")]


@pytest.mark.parametrize("cantidad, mensaje", [
    (0, "Debe tener al menos un elemento."), (25, "Debe tener como máximo 24 elementos."),
])
def test_lote_cantidad_de_combinaciones(client, db, entorno, sup, url, cantidad, mensaje):
    modulos = ("asistencias", "rubrica", "registro_vuelo", "lectura")
    combinaciones = [{"id_grado": g, "modulo": m} for g in range(1, 8) for m in modulos][:cantidad]
    pedido = lote(entorno)
    pedido["combinaciones"] = combinaciones

    respuesta = client.post(url, json=pedido, headers=sup)

    assert respuesta.status_code == 422
    assert [campo for campo, _ in errores(respuesta)] == ["combinaciones"]
    assert permisos_en_bd(db) == []


def test_lote_errores_comunes_son_por_campo(client, entorno, sup, url):
    sin_motivo = client.post(url, json=lote(entorno, motivo="  "), headers=sup)
    sin_colegio = client.post(url, json=lote(entorno, id_colegio=999), headers=sup)

    assert errores(sin_motivo) == [("motivo", "Ingresa el motivo de la autorización.")]
    assert errores(sin_colegio) == [("id_colegio", "No existe el colegio.")]
    assert "combinaciones_rechazadas" not in sin_colegio.json()


def test_lote_docente_inactivo(client, db, entorno, sup, url):
    rosa = entorno.usuarios["Docente"]
    rosa.activo = False
    db.add(rosa)
    db.commit()

    respuesta = client.post(url, json=lote(entorno), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "docente_inactivo")


@pytest.mark.parametrize("rol, codigo", [(None, 401), ("Docente", 403), ("Directivo", 403)])
def test_lote_solo_el_supervisor(client, entorno, cabeceras, url, rol, codigo):
    cabecera = cabeceras[rol] if rol else {}

    assert client.post(url, json=lote(entorno), headers=cabecera).status_code == codigo


def test_lote_fechas_sin_zona_son_hora_de_lima(client, db, entorno, sup, url):
    inicio = (tiempo.ahora_utc() + timedelta(hours=3)).astimezone(tiempo.ZONA_LIMA).replace(second=0, microsecond=0)
    fin = inicio + timedelta(days=1)
    sin_zona = {"inicio": inicio.replace(tzinfo=None).isoformat(), "fin": fin.replace(tzinfo=None).isoformat()}

    respuesta = client.post(url, json=lote(entorno, desde_ahora=False, **sin_zona), headers=sup)

    assert respuesta.status_code == 201
    esperado = (inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc))
    assert {(p.inicio, p.fin) for p in permisos_en_bd(db)} == {esperado}
