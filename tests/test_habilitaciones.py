"""Habilitaciones (T4): asignaciones en receso (CU016), servicio central de habilitación,
inicio de actividad (CU009) e Inicio del Docente (CU010)."""
import uuid
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core import tiempo
from app.models.organizacion import AnioEscolar, DocenteColegioGrado, PeriodoAcademico, Usuario
from app.models.trazabilidad import Auditoria
from app.services.habilitacion import (
    MENSAJE_CUENTA_INACTIVA,
    MENSAJE_SIN_HABILITACION,
    ORIGEN_ASIGNACION,
    ORIGEN_PERMISO,
    ORIGEN_RETROACTIVA,
    habilitacion_escritura,
    habilitaciones_vigentes,
)
from tests.conftest import (
    asignar,
    cabecera_de,
    crear_docente,
    crear_particular,
    crear_permiso,
    motivo,
    rangos_en_receso,
)

MENSAJE_SIN_PROXIMO = (
    "Este año escolar ya no tiene otro periodo académico. Para asignar al Docente al "
    "siguiente año, selecciona ese año escolar."
)
MENSAJE_NO_INICIA = (
    "No puedes iniciar una actividad porque no tienes asignaciones habilitadas ni permisos "
    "excepcionales vigentes."
)


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


@pytest.fixture
def lejano_en_receso(db, entorno):
    """Lejano (calendario particular) terminó su PA2 ayer y hoy está en el PR2; Andino
    (general) sigue en el PA2."""
    return crear_particular(db, entorno, rangos_en_receso(entorno.hoy))


def nuevo_docente(entorno, n=1, colegio="Lejano", grado="3.º", **asignacion) -> dict:
    return {
        "nombres": f"Nuevo{n}", "apellidos": "Docente", "dni": f"6000000{n}", "correo": f"nuevo{n}@sicedu.test",
        "id_rol": entorno.roles["Docente"],
        "asignacion": {
            "id_colegio": entorno.colegios[colegio].id_colegio, "id_anio_escolar": entorno.anio.id_anio_escolar,
            "grados": [entorno.grados[grado]], **asignacion,
        },
    }


def filas_de(db, correo) -> list[tuple[int, bool]]:
    """(número de PA, retroactiva) de las asignaciones del docente de ese correo."""
    db.expire_all()
    usuario = db.exec(select(Usuario).where(Usuario.correo == correo)).one()
    filas = db.exec(
        select(PeriodoAcademico.numero, DocenteColegioGrado.retroactiva)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(DocenteColegioGrado.id_docente == usuario.id_docente)
        .order_by(PeriodoAcademico.numero)
    ).all()
    return [tuple(f) for f in filas]


def rangos_en_pr4(hoy):
    """Calendario con los cuatro PA terminados y hoy en el PR4."""
    dia = timedelta(days=1)
    return {
        "PA1": (hoy - 200 * dia, hoy - 150 * dia), "PR1": (hoy - 149 * dia, hoy - 149 * dia),
        "PA2": (hoy - 148 * dia, hoy - 100 * dia), "PR2": (hoy - 99 * dia, hoy - 99 * dia),
        "PA3": (hoy - 98 * dia, hoy - 50 * dia), "PR3": (hoy - 49 * dia, hoy - 49 * dia),
        "PA4": (hoy - 48 * dia, hoy - dia), "PR4": (hoy, hoy + 30 * dia),
    }


# ── Asignaciones en receso (CU016) ──────────────────────────────────────────────────

def test_cu016_colegio_en_pa_no_pide_inicio(client, db, entorno, sup, lejano_en_receso):
    datos = nuevo_docente(entorno, colegio="Andino", grado="3.º", inicio_asignacion="retroactivo")

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    # Andino está en el PA2: la elección de receso no aplica y se ignora.
    assert respuesta.status_code == 201
    assert filas_de(db, "nuevo1@sicedu.test") == [(2, False), (3, False), (4, False)]


def test_cu016_colegio_en_receso_exige_elegir(client, entorno, sup, lejano_en_receso):
    respuesta = client.post("/usuarios", json=nuevo_docente(entorno), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "inicio_asignacion_requerido")
    cuerpo = respuesta.json()
    assert (cuerpo["colegio"], cuerpo["periodo"]) == ("Colegio Lejano", "PR2")
    assert cuerpo["id_colegio"] == entorno.colegios["Lejano"].id_colegio


def test_cu016_retroactivo_agrega_el_pa_culminado(client, db, entorno, sup, lejano_en_receso):
    respuesta = client.post("/usuarios", json=nuevo_docente(entorno, inicio_asignacion="retroactivo"), headers=sup)

    assert respuesta.status_code == 201
    assert filas_de(db, "nuevo1@sicedu.test") == [(2, True), (3, False), (4, False)]


def test_cu016_proximo_empieza_en_el_pa_siguiente(client, db, entorno, sup, lejano_en_receso):
    respuesta = client.post("/usuarios", json=nuevo_docente(entorno, inicio_asignacion="proximo"), headers=sup)

    assert respuesta.status_code == 201
    assert filas_de(db, "nuevo1@sicedu.test") == [(3, False), (4, False)]


def test_cu016_dos_colegios_con_calendarios_distintos_el_mismo_dia(client, db, entorno, sup, lejano_en_receso):
    en_pa = client.post("/usuarios", json=nuevo_docente(entorno, 1, "Andino", "4.º"), headers=sup)
    en_pr = client.post("/usuarios", json=nuevo_docente(entorno, 2, inicio_asignacion="proximo"), headers=sup)

    assert (en_pa.status_code, en_pr.status_code) == (201, 201)
    assert filas_de(db, "nuevo1@sicedu.test") == [(2, False), (3, False), (4, False)]
    assert filas_de(db, "nuevo2@sicedu.test") == [(3, False), (4, False)]


def test_cu016_proximo_en_el_pr4_es_422(client, db, entorno, sup):
    crear_particular(db, entorno, rangos_en_pr4(entorno.hoy))

    proximo = client.post("/usuarios", json=nuevo_docente(entorno, inicio_asignacion="proximo"), headers=sup)
    retroactivo = client.post(
        "/usuarios", json=nuevo_docente(entorno, 2, inicio_asignacion="retroactivo"), headers=sup
    )

    assert (proximo.status_code, proximo.json()["detail"]) == (422, MENSAJE_SIN_PROXIMO)
    assert proximo.json()["errores"] == [{"campo": "asignacion.inicio_asignacion", "mensaje": MENSAJE_SIN_PROXIMO}]
    assert retroactivo.status_code == 201
    assert filas_de(db, "nuevo2@sicedu.test") == [(4, True)]


def test_cu016_retroactivo_no_sustituye_al_titular_historico(client, db, entorno, sup, lejano_en_receso):
    titular = crear_docente(db, entorno, "Titular", "titular@sicedu.test", "70000009")
    asignar(db, titular.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    antes = len(db.exec(select(Usuario)).all())

    respuesta = client.post("/usuarios", json=nuevo_docente(entorno, inicio_asignacion="retroactivo"), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "grado_asignado")
    assert respuesta.json()["detail"] == (
        "No se puede guardar la asignación. El colegio Colegio Lejano, grado 3.º, ya tiene un "
        f"Docente asignado en PA2 del año escolar {entorno.anio.nombre}."
    )
    # Todo o nada: no se creó la cuenta ni ninguna asignación.
    db.expire_all()
    assert len(db.exec(select(Usuario)).all()) == antes


def test_cu016_editar_en_receso_tambien_pide_inicio(client, db, entorno, sup, lejano_en_receso):
    creado = client.post("/usuarios", json=nuevo_docente(entorno, inicio_asignacion="proximo"), headers=sup).json()
    url = f"/usuarios/{creado['usuario']['id']}"
    cambio = {"grados": [entorno.grados["3.º"], entorno.grados["4.º"]]}

    sin_inicio = client.patch(url, json={"asignacion": cambio}, headers=sup)
    retroactivo = client.patch(url, json={"asignacion": {**cambio, "inicio_asignacion": "retroactivo"}}, headers=sup)

    assert motivo(sin_inicio) == "inicio_asignacion_requerido"
    assert retroactivo.status_code == 200
    assert filas_de(db, "nuevo1@sicedu.test") == [(2, True), (2, True), (3, False), (3, False), (4, False), (4, False)]


def _titular_de_lejano(db, entorno) -> Usuario:
    """Docente titular de Lejano 3.º en el PA2, que Lejano acaba de culminar."""
    titular = crear_docente(db, entorno, "Titular", "titular@sicedu.test", "70000009")
    asignar(db, titular.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    db.commit()
    return titular


def test_cu016_retroactivo_del_mismo_titular_habilita_su_pa_culminado(client, db, entorno, sup, lejano_en_receso):
    titular = _titular_de_lejano(db, entorno)
    cambio = {"grados": [entorno.grados["3.º"]], "inicio_asignacion": "retroactivo"}

    respuesta = client.patch(f"/usuarios/{titular.id_usuario}", json={"asignacion": cambio}, headers=sup)

    assert respuesta.status_code == 200
    # Se conserva su fila del PA2 (sin duplicarla) y queda marcada retroactiva.
    assert filas_de(db, titular.correo) == [(2, True), (3, False), (4, False)]
    habilitacion = _habilitacion(db, entorno, titular, "Lejano", "3.º", modulo="rubrica")
    assert (habilitacion.permitido, habilitacion.origen) == (True, ORIGEN_RETROACTIVA)
    auditoria = db.exec(select(Auditoria).where(Auditoria.campo == "asignacion")).one()
    assert auditoria.valor_nuevo.endswith("(P2 retroactivo)")
    assert not auditoria.valor_anterior.endswith("retroactivo)")


def test_cu016_proximo_no_habilita_al_titular_en_el_pa_culminado(client, db, entorno, sup, lejano_en_receso):
    titular = _titular_de_lejano(db, entorno)
    cambio = {"grados": [entorno.grados["3.º"]], "inicio_asignacion": "proximo"}

    respuesta = client.patch(f"/usuarios/{titular.id_usuario}", json={"asignacion": cambio}, headers=sup)

    assert respuesta.status_code == 200
    assert filas_de(db, titular.correo) == [(2, False), (3, False), (4, False)]
    assert _habilitacion(db, entorno, titular, "Lejano", "3.º", modulo="rubrica").permitido is False


def test_cu016_inactivar_en_pa_libera_ese_pa_y_los_siguientes(client, db, entorno, sup):
    docente = entorno.usuarios["Docente"]
    asignar(db, entorno.id_docente, entorno.colegios["Andino"], [entorno.grados["1.º"]], entorno.periodos["futuro"], 1)
    db.commit()

    client.patch(f"/usuarios/{docente.id_usuario}/desactivar", headers=sup)

    # Quedan solo las del PA1 (terminado): historial.
    assert set(filas_de(db, docente.correo)) == {(1, False)}


def test_cu016_inactivar_en_pr_conserva_el_pa_culminado_y_libera_los_futuros(client, db, entorno, sup, lejano_en_receso):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    lejano, tercero = entorno.colegios["Lejano"], [entorno.grados["3.º"]]
    for periodo in ("vigente", "futuro"):
        asignar(db, otro.id_docente, lejano, tercero, entorno.periodos[periodo], 1)
    siguiente = AnioEscolar(
        nombre=str(entorno.hoy.year + 1), fecha_inicio=entorno.hoy + timedelta(days=200),
        fecha_fin=entorno.hoy + timedelta(days=400), creado_por=1, creado_en=tiempo.ahora_utc(),
    )
    db.add(siguiente)
    db.flush()
    pa1_siguiente = PeriodoAcademico(id_anio_escolar=siguiente.id_anio_escolar, numero=1, creado_por=1,
                                     creado_en=tiempo.ahora_utc())
    db.add(pa1_siguiente)
    db.flush()
    asignar(db, otro.id_docente, lejano, tercero, pa1_siguiente, 1)
    db.commit()

    client.patch(f"/usuarios/{otro.id_usuario}/desactivar", headers=sup)

    db.expire_all()
    quedan = db.exec(
        select(DocenteColegioGrado.id_periodo_academico).where(DocenteColegioGrado.id_docente == otro.id_docente)
    ).all()
    assert quedan == [entorno.periodos["vigente"].id_periodo_academico]


def test_cu016_estado_calendario_por_colegio(client, entorno, sup, lejano_en_receso, cabeceras):
    respuesta = client.get("/colegios/estado-calendario", headers=sup)
    docente = client.get("/colegios/estado-calendario", headers=cabeceras["Docente"])

    estados = {c["nombre"]: c["estado_calendario"] for c in respuesta.json()}
    assert (estados["Colegio Andino"]["tipo"], estados["Colegio Andino"]["numero"]) == ("PA", 2)
    assert (estados["Colegio Lejano"]["tipo"], estados["Colegio Lejano"]["numero"], estados["Colegio Lejano"]["es_pr4"]) == (
        "PR", 2, False
    )
    assert docente.status_code == 403


# ── Servicio central de habilitación ────────────────────────────────────────────────

def _habilitacion(db, entorno, usuario, colegio="Andino", grado="1.º", periodo="vigente", modulo="asistencias", en=None):
    return habilitacion_escritura(
        db, usuario, entorno.colegios[colegio].id_colegio, entorno.grados[grado],
        entorno.periodos[periodo].id_periodo_academico, modulo, en,
    )


def test_habilitacion_por_asignacion_del_pa_vigente(db, entorno):
    rosa = entorno.usuarios["Docente"]

    vigente = _habilitacion(db, entorno, rosa)
    pasado = _habilitacion(db, entorno, rosa, periodo="pasado")
    otro_grado = _habilitacion(db, entorno, rosa, grado="3.º")

    assert (vigente.permitido, vigente.origen, vigente.id_permiso) == (True, ORIGEN_ASIGNACION, None)
    assert (pasado.permitido, pasado.motivo) == (False, MENSAJE_SIN_HABILITACION)
    assert otro_grado.permitido is False


def test_habilitacion_retroactiva_solo_durante_su_pr(db, entorno, lejano_en_receso):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    asignar(db, otro.id_docente, entorno.colegios["Lejano"], [entorno.grados["3.º"]], entorno.periodos["vigente"], 1)
    fila = db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == otro.id_docente)).one()
    sin_marca = _habilitacion(db, entorno, otro, "Lejano", "3.º")
    fila.retroactiva = True
    db.add(fila)
    db.commit()
    despues_del_pr2 = tiempo.ahora_utc() + timedelta(days=40)

    en_el_pr = _habilitacion(db, entorno, otro, "Lejano", "3.º", modulo="rubrica")
    fuera_del_pr = _habilitacion(db, entorno, otro, "Lejano", "3.º", en=despues_del_pr2)

    assert sin_marca.permitido is False
    assert (en_el_pr.permitido, en_el_pr.origen) == (True, ORIGEN_RETROACTIVA)
    assert fuera_del_pr.permitido is False


def test_habilitacion_por_permiso_de_su_modulo_y_vigencia(db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    permiso = crear_permiso(db, entorno, otro.id_docente, "Lejano", "3.º", modulo="lectura")
    ahora = tiempo.ahora_utc()

    def con(**extra):
        return _habilitacion(db, entorno, otro, "Lejano", "3.º", periodo="pasado", **extra)

    vigente = con(modulo="lectura")
    assert (vigente.permitido, vigente.origen, vigente.id_permiso) == (True, ORIGEN_PERMISO, permiso.id_permiso)
    assert con(modulo="rubrica").permitido is False
    assert con(modulo="lectura", en=ahora + timedelta(hours=2)).permitido is False
    assert con(modulo="lectura", en=ahora - timedelta(hours=2)).permitido is False
    assert con(modulo="lectura", en=ahora + timedelta(minutes=30)).permitido is True


def test_habilitacion_permiso_revocado_no_habilita(db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    crear_permiso(db, entorno, otro.id_docente, "Lejano", "3.º", revocado_por=1, revocado_en=tiempo.ahora_utc())

    assert _habilitacion(db, entorno, otro, "Lejano", "3.º", "pasado", "lectura").permitido is False


def test_habilitacion_cuenta_inactiva_nunca(db, entorno):
    rosa = entorno.usuarios["Docente"]
    crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º")
    rosa.activo = False
    db.add(rosa)
    db.commit()

    resultado = _habilitacion(db, entorno, rosa)

    assert (resultado.permitido, resultado.motivo) == (False, MENSAJE_CUENTA_INACTIVA)
    assert habilitaciones_vigentes(db, rosa) == []


def test_habilitacion_modulo_desconocido_es_un_error_de_programacion(db, entorno):
    with pytest.raises(ValueError):
        _habilitacion(db, entorno, entorno.usuarios["Docente"], modulo="alumnos")


def test_habilitaciones_vigentes_en_orden_de_evaluacion(db, entorno):
    crear_permiso(db, entorno, entorno.id_docente, "Lejano", "3.º", modulo="rubrica")

    vigentes = habilitaciones_vigentes(db, entorno.usuarios["Docente"])

    assert [(h.origen, h.modulo) for h in vigentes] == [
        (ORIGEN_ASIGNACION, None), (ORIGEN_ASIGNACION, None), (ORIGEN_PERMISO, "rubrica"),
    ]


# ── Iniciar actividad e Inicio del Docente (CU009, CU010) ───────────────────────────

@pytest.fixture
def solo_con_permiso(db, entorno):
    """Docente sin asignaciones, con un permiso vigente de Lectura en Lejano 3.º (PA1)."""
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    permiso = crear_permiso(db, entorno, otro.id_docente, "Lejano", "3.º")
    return otro, permiso


def test_cu009_inicia_actividad_solo_con_un_permiso(client, db, solo_con_permiso):
    otro, _ = solo_con_permiso

    respuesta = client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabecera_de(db, otro))

    assert respuesta.status_code == 201


def test_cu009_sin_habilitaciones_no_inicia_con_el_mensaje_exacto(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    crear_permiso(db, entorno, otro.id_docente, "Lejano", "3.º",
                  inicio=tiempo.ahora_utc() + timedelta(hours=1), fin=tiempo.ahora_utc() + timedelta(hours=2))

    respuesta = client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabecera_de(db, otro))

    assert (respuesta.status_code, respuesta.json()["detail"]) == (422, MENSAJE_NO_INICIA)


def test_cu010_inicio_con_permisos_vigentes(client, db, entorno, solo_con_permiso):
    otro, permiso = solo_con_permiso

    cuerpo = client.get("/inicio/docente", headers=cabecera_de(db, otro)).json()

    assert (cuerpo["puede_iniciar_actividad"], cuerpo["motivo_no_puede"]) == (True, None)
    # El grupo al que solo llega por un permiso no suma a lo asignado.
    assert (cuerpo["asignaciones"], cuerpo["totales"]["cantidad_alumnos"]) == ([], 0)
    [vigente] = cuerpo["permisos_vigentes"]
    assert vigente["id_permiso"] == permiso.id_permiso
    assert (vigente["colegio"]["nombre"], vigente["grado"]["nombre"], vigente["periodo"], vigente["modulo"]) == (
        "Colegio Lejano", "3.º", "PA1", "lectura"
    )
    assert vigente["anio_escolar"]["nombre"] == entorno.anio.nombre


def test_cu010_inicio_sin_habilitaciones_explica_el_motivo(client, db, entorno):
    otro = crear_docente(db, entorno, "Otro", "otro@sicedu.test", "70000001")
    db.commit()

    cuerpo = client.get("/inicio/docente", headers=cabecera_de(db, otro)).json()

    assert (cuerpo["puede_iniciar_actividad"], cuerpo["motivo_no_puede_iniciar"]) == (False, "sin_asignaciones")
    assert (cuerpo["motivo_no_puede"], cuerpo["permisos_vigentes"]) == (MENSAJE_NO_INICIA, [])


def test_cu028_el_permiso_amplia_la_lectura_del_docente(client, db, entorno, solo_con_permiso):
    otro, _ = solo_con_permiso
    cabecera = cabecera_de(db, otro)
    lejano = entorno.colegios["Lejano"].id_colegio

    colegios = client.get("/colegios", headers=cabecera).json()["items"]
    alumnos = client.get("/alumnos", headers=cabecera).json()["items"]
    calendario = client.get("/calendarios", params={"id_colegio": lejano}, headers=cabecera)

    assert [c["nombre"] for c in colegios] == ["Colegio Lejano"]
    assert [a["nombres"] for a in alumnos] == ["Mario"]
    assert calendario.status_code == 200


def test_cu028_el_permiso_no_habilita_escribir_alumnos(client, db, entorno, solo_con_permiso):
    """Iniciar no amplía el alcance: el permiso es de un módulo, no de Alumnos."""
    otro, _ = solo_con_permiso
    cabecera = cabecera_de(db, otro)
    client.post("/actividades", json={"id_actividad": str(uuid.uuid4())}, headers=cabecera)

    respuesta = client.patch(f"/alumnos/{entorno.alumnos['mario'].id_alumno}", json={"nombres": "M"}, headers=cabecera)

    assert (respuesta.status_code, motivo(respuesta)) == (403, "alumno_fuera_de_alcance")
