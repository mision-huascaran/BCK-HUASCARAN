"""Gestión de cuentas de usuario (CU016). Solo el Supervisor, para los tres roles."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.politica_contrasena import cumple_politica_contrasena
from app.core.tiempo import ahora_utc
from app.models.organizacion import AnioEscolar, Docente, DocenteColegioGrado, PeriodoAcademico, Usuario
from app.models.seguridad import Sesion
from app.models.trazabilidad import Auditoria
from tests.conftest import CORREOS, cabecera_de, crear_colegio, crear_docente, motivo, releer


def nuevo(id_rol, n=1, **extra):
    return {
        "nombres": f"Nuevo{n}",
        "apellidos": "Usuario",
        "dni": f"5000000{n}",
        "correo": f"nuevo{n}@sicedu.test",
        "id_rol": id_rol,
        **extra,
    }


def asignacion(entorno, colegio="Lejano", grados=None, **extra):
    datos = {"id_colegio": entorno.colegios[colegio].id_colegio, "id_anio_escolar": entorno.anio.id_anio_escolar, **extra}
    if grados is not None:
        datos["grados"] = grados
    return datos


def asignaciones_de(db, id_docente):
    db.expire_all()
    return db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == id_docente)).all()


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


# ── Alta ────────────────────────────────────────────────────────────────────────────

def test_cu016_crear_docente_con_asignacion_desde_el_periodo_vigente(client, db, entorno, sup, correos):
    datos = nuevo(entorno.roles["Docente"], asignacion=asignacion(entorno, grados=[entorno.grados["3.º"]]))

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["correo_enviado"] is True
    assert cuerpo["contraseña_temporal"] is None
    assert cuerpo["usuario"]["rol"] == "Docente"
    assert cuerpo["usuario"]["colegios_asignados"] == ["Colegio Lejano"]
    usuario = db.exec(select(Usuario).where(Usuario.correo == "nuevo1@sicedu.test")).one()
    periodos = {fila.id_periodo_academico for fila in asignaciones_de(db, usuario.id_docente)}
    assert periodos == {entorno.periodos["vigente"].id_periodo_academico, entorno.periodos["futuro"].id_periodo_academico}
    temporal = correos.temporal("nuevo1@sicedu.test")
    assert cumple_politica_contrasena(temporal)
    assert client.post("/login", json={"correo": "nuevo1@sicedu.test", "password": temporal}).status_code == 200


def test_cu016_sin_grados_se_asignan_todos_los_del_colegio(client, db, entorno, sup):
    client.post("/usuarios", json=nuevo(entorno.roles["Docente"], asignacion=asignacion(entorno)), headers=sup)

    usuario = db.exec(select(Usuario).where(Usuario.correo == "nuevo1@sicedu.test")).one()
    grados = {f.id_grado for f in asignaciones_de(db, usuario.id_docente)}
    assert grados == set(entorno.grados.values())


@pytest.mark.parametrize("rol", ["Supervisor", "Directivo"])
def test_cu016_crear_supervisor_o_directivo(client, entorno, sup, rol):
    respuesta = client.post("/usuarios", json=nuevo(entorno.roles[rol]), headers=sup)

    assert respuesta.status_code == 201
    assert respuesta.json()["usuario"]["colegios_asignados"] == ["Global"]


def test_cu016_si_el_correo_falla_la_temporal_vuelve_en_la_respuesta(client, entorno, sup, correos):
    correos.fallar = True

    cuerpo = client.post("/usuarios", json=nuevo(entorno.roles["Directivo"]), headers=sup).json()

    assert cuerpo["correo_enviado"] is False
    assert cumple_politica_contrasena(cuerpo["contraseña_temporal"])


@pytest.mark.parametrize(
    "cambio, codigo, esperado",
    [
        ({"correo": CORREOS["Docente"]}, 409, "correo_duplicado"),
        ({"correo": CORREOS["Docente"].upper()}, 409, "correo_duplicado"),
        ({"dni": "40000003"}, 409, "dni_duplicado"),
        ({"dni": "123"}, 422, "validacion"),
        ({"correo": "sin-arroba"}, 422, "validacion"),
        ({"id_rol": 999}, 422, "rol_inexistente"),
        ({"nombres": "   "}, 422, "validacion"),
    ],
)
def test_cu016_alta_con_datos_invalidos(client, entorno, sup, cambio, codigo, esperado):
    respuesta = client.post("/usuarios", json={**nuevo(entorno.roles["Supervisor"]), **cambio}, headers=sup)

    assert respuesta.status_code == codigo
    assert motivo(respuesta) == esperado


def test_cu016_docente_sin_asignacion_y_otro_rol_con_asignacion(client, entorno, sup):
    sin = client.post("/usuarios", json=nuevo(entorno.roles["Docente"]), headers=sup)
    con = client.post("/usuarios", json=nuevo(entorno.roles["Directivo"], asignacion=asignacion(entorno)), headers=sup)

    assert motivo(sin) == "asignacion_requerida"
    assert motivo(con) == "asignacion_no_permitida"


def test_cu016_un_aula_solo_tiene_un_docente(client, entorno, sup):
    datos = nuevo(entorno.roles["Docente"], asignacion=asignacion(entorno, "Andino", [entorno.grados["1.º"]]))

    respuesta = client.post("/usuarios", json=datos, headers=sup)

    assert respuesta.status_code == 409
    assert motivo(respuesta) == "grado_asignado"
    assert "Rosa Quispe" in respuesta.json()["detail"]


@pytest.mark.parametrize(
    "preparar, esperado",
    [
        ("inactivo", "colegio_inactivo"),
        ("inexistente", "colegio_inexistente"),
        ("sin_grados", "colegio_sin_grados"),
        ("grado_no_ofrecido", "grado_no_ofrecido"),
        ("anio_inexistente", "anio_inexistente"),
        ("anio_sin_periodos", "anio_sin_periodos"),
        ("anio_terminado", "sin_periodos_asignables"),
    ],
)
def test_cu016_asignacion_invalida(client, db, entorno, sup, preparar, esperado):
    datos = asignacion(entorno)
    colegio = entorno.colegios["Lejano"]
    if preparar == "inactivo":
        colegio.activo = False
        db.add(colegio)
    elif preparar == "inexistente":
        datos["id_colegio"] = 999
    elif preparar == "sin_grados":
        datos["id_colegio"] = crear_colegio(db, "Colegio Vacío", [], [], entorno.supervisor.id_usuario).id_colegio
    elif preparar == "grado_no_ofrecido":
        datos["id_colegio"] = crear_colegio(
            db, "Colegio Chico", [entorno.grados["1.º"]], [], entorno.supervisor.id_usuario
        ).id_colegio
        datos["grados"] = [entorno.grados["6.º"]]
    elif preparar == "anio_inexistente":
        datos["id_anio_escolar"] = 999
    else:
        hoy = entorno.hoy
        inicio, fin = (hoy + timedelta(days=300), hoy + timedelta(days=500))
        if preparar == "anio_terminado":
            inicio, fin = hoy - timedelta(days=900), hoy - timedelta(days=600)
        anio = AnioEscolar(nombre="Otro", fecha_inicio=inicio, fecha_fin=fin, creado_por=1, creado_en=ahora_utc())
        db.add(anio)
        db.flush()
        if preparar == "anio_terminado":
            db.add(PeriodoAcademico(id_anio_escolar=anio.id_anio_escolar, numero=1, fecha_inicio=inicio,
                                    fecha_fin=fin, creado_por=1, creado_en=ahora_utc()))
        datos["id_anio_escolar"] = anio.id_anio_escolar
    db.commit()

    respuesta = client.post("/usuarios", json=nuevo(entorno.roles["Docente"], asignacion=datos), headers=sup)

    assert respuesta.status_code == 422
    assert motivo(respuesta) == esperado


@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_cu016_solo_el_supervisor_gestiona_usuarios(client, entorno, cabeceras, rol):
    cabecera = cabeceras[rol]
    respuestas = [
        client.get("/usuarios", headers=cabecera),
        client.post("/usuarios", json=nuevo(entorno.roles["Directivo"]), headers=cabecera),
        client.patch(f"/usuarios/{entorno.supervisor.id_usuario}", json={"nombres": "X"}, headers=cabecera),
    ]

    assert {r.status_code for r in respuestas} == {403}
    assert respuestas[0].json() == {"detail": "No tienes permisos para realizar esta acción", "motivo": "sin_permiso"}


# ── Listado y detalle ───────────────────────────────────────────────────────────────

def test_cu016_listado_ordenado_por_rol_y_nombre(client, entorno, sup):
    cuerpo = client.get("/usuarios", headers=sup).json()

    assert [u["rol"] for u in cuerpo["items"]] == ["Docente", "Supervisor", "Supervisor", "Directivo"]
    assert cuerpo["items"][0]["colegios_asignados"] == ["Colegio Andino"]
    assert [u["es_supervisor_original"] for u in cuerpo["items"]].count(True) == 1
    assert (cuerpo["total"], cuerpo["page"], cuerpo["page_size"], cuerpo["total_pages"]) == (4, 1, 10, 1)


@pytest.mark.parametrize(
    "params, correos",
    [
        ({"rol": "Directivo"}, [CORREOS["Directivo"]]),
        ({"q": "rosa quis"}, [CORREOS["Docente"]]),
        ({"q": "SUPERV"}, [CORREOS["Supervisor"]]),
        ({"activo": "false"}, []),
    ],
)
def test_cu016_filtros_del_listado(client, entorno, sup, params, correos):
    cuerpo = client.get("/usuarios", params=params, headers=sup).json()

    assert [u["correo"] for u in cuerpo["items"]] == correos


def test_cu016_paginacion_de_10_en_10(client, db, entorno, sup):
    for n in range(10):
        crear_docente(db, entorno, f"Extra{n:02d}", f"extra{n}@sicedu.test", f"6000000{n}")
    db.commit()

    segunda = client.get("/usuarios", params={"page": 2}, headers=sup).json()

    assert (segunda["total"], segunda["total_pages"], len(segunda["items"])) == (14, 2, 4)
    assert client.get("/usuarios", params={"page": 0}, headers=sup).status_code == 422


def test_cu016_detalle_de_un_docente_trae_su_asignacion(client, entorno, sup):
    cuerpo = client.get(f"/usuarios/{entorno.docente.id_usuario}", headers=sup).json()

    periodos = cuerpo["asignacion"]["periodos"]
    assert cuerpo["asignacion"]["id_anio_escolar"] == entorno.anio.id_anio_escolar
    assert [p["vigente"] for p in periodos] == [False, True]
    assert [g["nombre"] for g in periodos[1]["grados"]] == ["1.º", "2.º"]


def test_cu016_detalle_de_otros_roles_y_404(client, entorno, sup):
    assert client.get(f"/usuarios/{entorno.supervisor.id_usuario}", headers=sup).json()["asignacion"] is None
    respuesta = client.get("/usuarios/999", headers=sup)
    assert respuesta.status_code == 404
    assert motivo(respuesta) == "usuario_no_encontrado"


# ── Edición ─────────────────────────────────────────────────────────────────────────

def test_cu016_editar_nombres_actualiza_la_ficha_y_audita(client, db, entorno, sup):
    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}", json={"nombres": "Rosa María", "dni": "40000099"}, headers=sup
    )

    assert respuesta.status_code == 200
    assert releer(db, Docente, entorno.id_docente).nombres == "Rosa María"
    campos = db.exec(select(Auditoria.campo).where(Auditoria.tabla == "usuario", Auditoria.accion == "editar")).all()
    assert set(campos) == {"nombres", "dni"}


@pytest.mark.parametrize(
    "cambio, codigo, esperado",
    [
        ({"correo": CORREOS["Directivo"]}, 409, "correo_duplicado"),
        ({"dni": "40000002"}, 409, "dni_duplicado"),
        ({"nombres": None}, 422, "validacion"),
        ({"id_rol": 999}, 422, "rol_inexistente"),
    ],
)
def test_cu016_edicion_invalida(client, entorno, sup, cambio, codigo, esperado):
    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}", json=cambio, headers=sup)

    assert respuesta.status_code == codigo
    assert motivo(respuesta) == esperado


def test_cu016_mismo_correo_no_es_conflicto_y_no_audita(client, db, entorno, sup):
    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}", json={"correo": CORREOS["Docente"]}, headers=sup)

    assert respuesta.status_code == 200
    assert db.exec(select(Auditoria).where(Auditoria.accion == "editar")).all() == []
    assert client.patch("/usuarios/999", json={"nombres": "X"}, headers=sup).status_code == 404


def test_cu016_nadie_cambia_su_propio_rol_ni_el_del_original(client, entorno, sup):
    propio = client.patch(f"/usuarios/{entorno.supervisor.id_usuario}", json={"id_rol": entorno.roles["Directivo"]}, headers=sup)
    original = client.patch(
        f"/usuarios/{entorno.usuarios['Original'].id_usuario}", json={"id_rol": entorno.roles["Directivo"]}, headers=sup
    )

    assert (propio.status_code, motivo(propio)) == (409, "auto_cambio_rol")
    assert (original.status_code, motivo(original)) == (403, "supervisor_original_protegido")


def test_cu016_el_original_si_puede_editar_sus_datos(client, entorno, sup):
    respuesta = client.patch(f"/usuarios/{entorno.usuarios['Original'].id_usuario}", json={"nombres": "Olga M."}, headers=sup)

    assert respuesta.json()["nombres"] == "Olga M."


def test_cu016_docente_a_supervisor_libera_sus_aulas(client, db, entorno, sup):
    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}", json={"id_rol": entorno.roles["Supervisor"]}, headers=sup
    )

    assert respuesta.json()["rol"] == "Supervisor"
    restantes = asignaciones_de(db, entorno.id_docente)
    assert {f.id_periodo_academico for f in restantes} == {entorno.periodos["pasado"].id_periodo_academico}
    assert releer(db, Docente, entorno.id_docente).activo is False


def test_cu016_pasar_a_docente_exige_asignacion(client, db, entorno, sup):
    id_directivo = entorno.usuarios["Directivo"].id_usuario
    sin = client.patch(f"/usuarios/{id_directivo}", json={"id_rol": entorno.roles["Docente"]}, headers=sup)
    con = client.patch(
        f"/usuarios/{id_directivo}",
        json={"id_rol": entorno.roles["Docente"], "asignacion": asignacion(entorno)},
        headers=sup,
    )

    assert motivo(sin) == "asignacion_requerida"
    assert con.status_code == 200
    assert con.json()["colegios_asignados"] == ["Colegio Lejano"]
    assert releer(db, Usuario, id_directivo).id_docente is not None


def test_cu016_volver_a_docente_reactiva_su_ficha(client, db, entorno, sup):
    id_usuario = entorno.docente.id_usuario
    client.patch(f"/usuarios/{id_usuario}", json={"id_rol": entorno.roles["Directivo"]}, headers=sup)

    respuesta = client.patch(
        f"/usuarios/{id_usuario}", json={"id_rol": entorno.roles["Docente"], "asignacion": asignacion(entorno)}, headers=sup
    )

    assert respuesta.status_code == 200
    assert releer(db, Docente, entorno.id_docente).activo is True


def test_cu016_asignacion_en_un_rol_que_no_la_admite(client, entorno, sup):
    respuesta = client.patch(
        f"/usuarios/{entorno.usuarios['Directivo'].id_usuario}", json={"asignacion": asignacion(entorno)}, headers=sup
    )

    assert motivo(respuesta) == "asignacion_no_permitida"


def test_cu016_rotar_de_colegio_desde_el_periodo_vigente(client, db, entorno, sup):
    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}",
        json={"asignacion": {"id_colegio": entorno.colegios["Lejano"].id_colegio}},
        headers=sup,
    )

    assert respuesta.json()["colegios_asignados"] == ["Colegio Lejano"]
    por_periodo = {}
    for fila in asignaciones_de(db, entorno.id_docente):
        por_periodo.setdefault(fila.id_periodo_academico, set()).add(fila.id_colegio)
    assert por_periodo[entorno.periodos["pasado"].id_periodo_academico] == {entorno.colegios["Andino"].id_colegio}
    assert por_periodo[entorno.periodos["vigente"].id_periodo_academico] == {entorno.colegios["Lejano"].id_colegio}
    auditoria = db.exec(select(Auditoria).where(Auditoria.campo == "asignacion")).one()
    assert auditoria.valor_anterior.startswith("Colegio Andino [1.º, 2.º]")
    assert auditoria.valor_nuevo.startswith("Colegio Lejano [")


def test_cu016_cambiar_grados_conserva_las_filas_que_no_cambian(client, db, entorno, sup):
    vigente = entorno.periodos["vigente"].id_periodo_academico
    antes = {f.id_grado: f.creado_en for f in asignaciones_de(db, entorno.id_docente) if f.id_periodo_academico == vigente}

    client.patch(
        f"/usuarios/{entorno.docente.id_usuario}",
        json={"asignacion": {"grados": [entorno.grados["1.º"], entorno.grados["3.º"]]}},
        headers=sup,
    )

    despues = {f.id_grado: f.creado_en for f in asignaciones_de(db, entorno.id_docente) if f.id_periodo_academico == vigente}
    assert set(despues) == {entorno.grados["1.º"], entorno.grados["3.º"]}
    assert despues[entorno.grados["1.º"]] == antes[entorno.grados["1.º"]]


def test_cu016_renovar_para_otro_anio(client, db, entorno, sup):
    hoy = entorno.hoy
    otro = AnioEscolar(nombre="Siguiente", fecha_inicio=hoy + timedelta(days=200),
                       fecha_fin=hoy + timedelta(days=400), creado_por=1, creado_en=ahora_utc())
    db.add(otro)
    db.flush()
    periodo = PeriodoAcademico(id_anio_escolar=otro.id_anio_escolar, numero=1, fecha_inicio=otro.fecha_inicio,
                               fecha_fin=otro.fecha_fin, creado_por=1, creado_en=ahora_utc())
    db.add(periodo)
    db.commit()

    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}",
        json={"asignacion": {"id_anio_escolar": otro.id_anio_escolar}},
        headers=sup,
    )

    assert respuesta.status_code == 200
    nuevas = [f for f in asignaciones_de(db, entorno.id_docente) if f.id_periodo_academico == periodo.id_periodo_academico]
    assert {f.id_grado for f in nuevas} == {entorno.grados["1.º"], entorno.grados["2.º"]}


@pytest.mark.parametrize("periodo, esperado", [("pasado", "periodo_terminado"), ("otro_anio", "periodo_fuera_del_anio")])
def test_cu016_desde_periodo_invalido(client, db, entorno, sup, periodo, esperado):
    if periodo == "otro_anio":
        otro = AnioEscolar(nombre="X", fecha_inicio=entorno.hoy, fecha_fin=entorno.hoy, creado_por=1, creado_en=ahora_utc())
        db.add(otro)
        db.flush()
        fila = PeriodoAcademico(id_anio_escolar=otro.id_anio_escolar, numero=9, fecha_inicio=entorno.hoy,
                                fecha_fin=entorno.hoy, creado_por=1, creado_en=ahora_utc())
        db.add(fila)
        db.commit()
        id_periodo = fila.id_periodo_academico
    else:
        id_periodo = entorno.periodos[periodo].id_periodo_academico

    respuesta = client.patch(
        f"/usuarios/{entorno.docente.id_usuario}", json={"asignacion": {"desde_periodo": id_periodo}}, headers=sup
    )

    assert motivo(respuesta) == esperado


def test_cu016_docente_sin_asignaciones_necesita_colegio_y_anio(client, db, entorno, sup):
    otro = crear_docente(db, entorno, "Sin", "sin@sicedu.test", "70000000")
    db.commit()

    incompleta = client.patch(f"/usuarios/{otro.id_usuario}", json={"asignacion": {"grados": [1]}}, headers=sup)
    completa = client.patch(
        f"/usuarios/{otro.id_usuario}",
        json={"asignacion": {**asignacion(entorno), "desde_periodo": entorno.periodos["futuro"].id_periodo_academico}},
        headers=sup,
    )

    assert motivo(incompleta) == "asignacion_incompleta"
    assert completa.status_code == 200
    assert {f.id_periodo_academico for f in asignaciones_de(db, otro.id_docente)} == {
        entorno.periodos["futuro"].id_periodo_academico
    }


# ── Activar / desactivar ────────────────────────────────────────────────────────────

def test_cu016_desactivar_docente_cierra_sesiones_y_libera_aulas(client, db, entorno, sup):
    cabecera = cabecera_de(db, entorno.docente)

    respuesta = client.patch(f"/usuarios/{entorno.docente.id_usuario}/desactivar", headers=sup)

    assert respuesta.json()["activo"] is False
    assert motivo(client.get("/me", headers=cabecera)) == "sesion_invalida"
    assert set(db.exec(select(Sesion.tipo_cierre).where(Sesion.id_usuario == entorno.docente.id_usuario)).all()) == {
        "Invalidada por desactivación"
    }
    assert {f.id_periodo_academico for f in asignaciones_de(db, entorno.id_docente)} == {
        entorno.periodos["pasado"].id_periodo_academico
    }
    assert releer(db, Docente, entorno.id_docente).activo is False


def test_cu016_reactivar_no_restaura_las_asignaciones(client, db, entorno, sup):
    url = f"/usuarios/{entorno.docente.id_usuario}"
    client.patch(f"{url}/desactivar", headers=sup)

    respuesta = client.patch(f"{url}/activar", headers=sup)

    assert respuesta.json()["activo"] is True
    assert respuesta.json()["colegios_asignados"] == []
    assert client.patch(f"{url}/activar", headers=sup).status_code == 200


@pytest.mark.parametrize(
    "quien, codigo, esperado",
    [("propio", 409, "auto_desactivacion"), ("Original", 409, "supervisor_original_protegido"), ("nadie", 404, "usuario_no_encontrado")],
)
def test_cu016_desactivaciones_no_permitidas(client, entorno, sup, quien, codigo, esperado):
    id_usuario = {"propio": entorno.supervisor.id_usuario, "nadie": 999}.get(quien) or entorno.usuarios[quien].id_usuario

    respuesta = client.patch(f"/usuarios/{id_usuario}/desactivar", headers=sup)

    assert respuesta.status_code == codigo
    assert motivo(respuesta) == esperado


def test_cu016_desactivar_un_directivo_lo_deja_en_el_listado_de_inactivos(client, entorno, sup):
    client.patch(f"/usuarios/{entorno.usuarios['Directivo'].id_usuario}/desactivar", headers=sup)

    inactivos = client.get("/usuarios", params={"activo": False}, headers=sup).json()["items"]

    assert [u["correo"] for u in inactivos] == [CORREOS["Directivo"]]
