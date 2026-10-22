"""Seed de datos E2E (app/cli/seed_e2e.py): idempotencia, candado de entorno y garantías
que usan las pruebas de Playwright."""
from datetime import timedelta

import pytest
from pydantic import TypeAdapter
from sqlmodel import Session, col, func, select

from app.cli import __main__ as cli
from app.cli import inicializar as orquestador
from app.cli.catalogos import cargar_catalogos
from app.cli.seed_e2e import COLEGIO, CUENTAS, sembrar_e2e
from app.cli.supervisor import inicializar_supervisor_original
from app.core import tiempo
from app.core.correo import CorreoValido, normalizar_correo
from app.core.security import verify_password
from app.models.organizacion import (
    AnioEscolar,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Rol,
    Usuario,
)
from app.models.seguridad import ControlAccesoCorreo
from tests.conftest import asignar, crear_calendario, crear_colegio
from tests.test_cli import datos_supervisor

CONTRASENAS = {
    "E2E_DOCENTE_PASS": "Docente-E2e-1",
    "E2E_SUPERVISOR_PASS": "Supervisor-E2e-1",
    "E2E_DIRECTIVO_PASS": "Directivo-E2e-1",
    "E2E_INACTIVO_PASS": "Inactivo-E2e-1",
}
VARIABLES = {"SEED_E2E": "true", "ENTORNO": "qa", **CONTRASENAS}
CORREOS = {c.variable: c.correo for c in CUENTAS}


@pytest.fixture
def base(db):
    """Catálogos, Supervisor original y un año con PA1–PA4: dos terminados, PA3 vigente
    y PA4 futuro."""
    cargar_catalogos(db)
    inicializar_supervisor_original(db, datos_supervisor())
    hoy = tiempo.hoy_lima()
    id_original = db.exec(select(Usuario.id_usuario).where(col(Usuario.es_supervisor_original))).one()
    auditoria = {"creado_por": id_original, "creado_en": tiempo.ahora_utc()}
    anio = AnioEscolar(nombre=str(hoy.year), fecha_inicio=hoy - timedelta(days=200),
                       fecha_fin=hoy + timedelta(days=150), **auditoria)
    db.add(anio)
    db.flush()
    dias = {"PA1": (-180, -120), "PR1": (-119, -111), "PA2": (-110, -40), "PR2": (-39, -31),
            "PA3": (-30, 30), "PR3": (31, 31), "PA4": (32, 120), "PR4": (121, 150)}
    rangos = {nombre: (hoy + timedelta(days=d), hoy + timedelta(days=h)) for nombre, (d, h) in dias.items()}
    crear_calendario(db, anio.id_anio_escolar, rangos, id_original)
    db.commit()
    return id_original


def periodos(db) -> dict[int, PeriodoAcademico]:
    return {p.numero: p for p in db.exec(select(PeriodoAcademico)).all()}


def grados(db) -> dict[str, int]:
    return {g.nombre: g.id_grado for g in db.exec(select(Grado)).all()}


def usuario(db, variable) -> Usuario:
    db.expire_all()
    return db.exec(select(Usuario).where(Usuario.correo == CORREOS[variable])).first()


def colegio_e2e(db) -> Colegio:
    return db.exec(select(Colegio).where(Colegio.nombre == COLEGIO["nombre"])).one()


def contar(db, modelo) -> int:
    return db.exec(select(func.count()).select_from(modelo)).one()


def otro_docente(db, id_autor, nombres="Otro") -> int:
    ficha = Docente(nombres=nombres, apellidos="Docente", activo=True, creado_por=id_autor,
                    creado_en=tiempo.ahora_utc())
    db.add(ficha)
    db.commit()
    return ficha.id_docente


def asignaciones_e2e(db) -> set[tuple[int, str, int]]:
    """(id_docente, grado, número de periodo) de las asignaciones del colegio E2E."""
    filas = db.exec(
        select(DocenteColegioGrado.id_docente, Grado.nombre, PeriodoAcademico.numero)
        .join(Grado, Grado.id_grado == DocenteColegioGrado.id_grado)
        .join(PeriodoAcademico,
              PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(DocenteColegioGrado.id_colegio == colegio_e2e(db).id_colegio)
    ).all()
    return set(filas)


# ── Idempotencia y datos sembrados ──────────────────────────────────────────────────

def test_dos_corridas_no_duplican_nada(db, base):
    primera = sembrar_e2e(db, VARIABLES)
    totales = {m: contar(db, m) for m in (Colegio, Usuario, Docente, DocenteColegioGrado, ColegioGrado,
                                          ColegioPrograma)}
    segunda = sembrar_e2e(db, VARIABLES)

    assert not primera.errores
    assert not primera.advertencias
    assert primera.creados["usuario E2E"] == 4
    assert primera.creados["colegio E2E"] == 1
    assert primera.creados["asignación E2E"] == 12
    assert not segunda.errores
    assert not segunda.creados
    assert not segunda.actualizados
    assert {m: contar(db, m) for m in totales} == totales
    assert totales[Usuario] == 5  # las 4 cuentas E2E más el Supervisor original


def test_colegio_y_cuentas_con_los_datos_pedidos(db, base):
    sembrar_e2e(db, VARIABLES)

    colegio = colegio_e2e(db)
    assert (colegio.departamento, colegio.provincia, colegio.nivel_educativo, colegio.seccion, colegio.activo) == (
        "Áncash", "Huaraz", "Primaria", "Única", True
    )
    oferta = db.exec(select(Grado.nombre).join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
                     .where(ColegioGrado.id_colegio == colegio.id_colegio)).all()
    assert sorted(oferta) == ["1.º", "2.º", "3.º", "4.º", "5.º", "6.º"]
    assert contar(db, ColegioPrograma) == 2

    roles = {r.id_rol: r.nombre for r in db.exec(select(Rol)).all()}
    esperado = {
        "E2E_DOCENTE_PASS": ("Docente", "E2E Docente", "90000001", True),
        "E2E_SUPERVISOR_PASS": ("Supervisor", "E2E Supervisora", "90000002", True),
        "E2E_DIRECTIVO_PASS": ("Directivo", "E2E Directivo", "90000003", True),
        "E2E_INACTIVO_PASS": ("Directivo", "E2E Inactivo", "90000004", False),
    }
    for variable, (rol, nombres, dni, activo) in esperado.items():
        cuenta = usuario(db, variable)
        assert (roles[cuenta.id_rol], cuenta.nombres, cuenta.apellidos, cuenta.dni, cuenta.activo) == (
            rol, nombres, "Playwright", dni, activo
        )
        assert cuenta.password_hash.startswith("$2")
        assert verify_password(CONTRASENAS[variable], cuenta.password_hash)
        assert not cuenta.es_supervisor_original


def test_dias_de_clase_del_colegio_e2e(db, base):
    """Cada grado con su día (para cubrir los vencidos de CU011); un colegio E2E ya
    cargado sin días (o con otros) los recibe en la siguiente corrida."""
    sembrar_e2e(db, VARIABLES)
    oferta = db.exec(select(Grado.nombre, ColegioGrado.dia_clase).join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
                     .where(ColegioGrado.id_colegio == colegio_e2e(db).id_colegio)).all()
    assert sorted(oferta) == [("1.º", 1), ("2.º", 2), ("3.º", 3), ("4.º", 4), ("5.º", 5), ("6.º", 1)]
    for fila in db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == colegio_e2e(db).id_colegio)).all():
        fila.dia_clase = None
        db.add(fila)
    db.commit()

    resumen = sembrar_e2e(db, VARIABLES)

    assert resumen.actualizados["colegio E2E"] == 1
    dias = db.exec(select(ColegioGrado.dia_clase).where(ColegioGrado.id_colegio == colegio_e2e(db).id_colegio)).all()
    assert None not in dias


def test_la_cuenta_inactiva_queda_inactiva_aunque_la_activen(db, base):
    sembrar_e2e(db, VARIABLES)
    inactiva = usuario(db, "E2E_INACTIVO_PASS")
    inactiva.activo = True
    db.add(inactiva)
    db.commit()

    sembrar_e2e(db, VARIABLES)

    assert usuario(db, "E2E_INACTIVO_PASS").activo is False


def test_el_seed_reactiva_las_cuentas_activas_y_la_ficha_del_docente(db, base):
    sembrar_e2e(db, VARIABLES)
    docente = usuario(db, "E2E_DOCENTE_PASS")
    docente.activo = False
    db.get(Docente, docente.id_docente).activo = False
    db.commit()

    resumen = sembrar_e2e(db, VARIABLES)

    docente = usuario(db, "E2E_DOCENTE_PASS")
    assert docente.activo
    assert db.get(Docente, docente.id_docente).activo
    assert resumen.actualizados["usuario E2E"] == 1


def test_docente_asignado_en_1_a_3_en_los_cuatro_periodos_y_4_a_6_libres(db, base):
    sembrar_e2e(db, VARIABLES)

    id_docente = usuario(db, "E2E_DOCENTE_PASS").id_docente
    assert asignaciones_e2e(db) == {
        (id_docente, grado, numero) for grado in ("1.º", "2.º", "3.º") for numero in (1, 2, 3, 4)
    }


def test_reasigna_1_a_3_si_los_tiene_otro_docente(db, base):
    sembrar_e2e(db, VARIABLES)
    intruso = otro_docente(db, base)
    fila = db.exec(select(DocenteColegioGrado).where(
        DocenteColegioGrado.id_grado == grados(db)["2.º"],
        DocenteColegioGrado.id_periodo_academico == periodos(db)[3].id_periodo_academico,
    )).one()
    fila.id_docente = intruso
    db.commit()

    resumen = sembrar_e2e(db, VARIABLES)

    assert resumen.actualizados["asignación E2E (reasignada)"] == 1
    assert all(id_docente != intruso for id_docente, _, _ in asignaciones_e2e(db))


def test_libera_4_a_6_desde_el_periodo_vigente_y_conserva_el_historial(db, base):
    sembrar_e2e(db, VARIABLES)
    colegio = colegio_e2e(db)
    temporal = otro_docente(db, base, "Temporal")
    por_numero = periodos(db)
    for numero in (1, 2, 3, 4):
        asignar(db, temporal, colegio, [grados(db)["4.º"], grados(db)["6.º"]], por_numero[numero], base)
    db.commit()

    resumen = sembrar_e2e(db, VARIABLES)

    assert resumen.eliminados["asignación E2E en 4.º–6.º"] == 4
    restantes = {(g, n) for d, g, n in asignaciones_e2e(db) if d == temporal}
    assert restantes == {("4.º", 1), ("6.º", 1), ("4.º", 2), ("6.º", 2)}


def test_no_toca_asignaciones_de_otros_colegios(db, base):
    otro = crear_colegio(db, "Colegio Real", list(grados(db).values()), [], base)
    temporal = otro_docente(db, base)
    asignar(db, temporal, otro, [grados(db)["4.º"]], periodos(db)[3], base)
    db.commit()

    sembrar_e2e(db, VARIABLES)

    assert db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == temporal)).one()


# ── Candado de entorno y variables ──────────────────────────────────────────────────

@pytest.mark.parametrize("valor", [None, "", "false", "1", "yes"])
def test_desactivado_si_seed_e2e_no_es_true(db, base, valor):
    variables = {**VARIABLES}
    variables.pop("SEED_E2E")
    if valor is not None:
        variables["SEED_E2E"] = valor

    resumen = sembrar_e2e(db, variables)

    assert resumen.omitidos
    assert not resumen.errores
    assert contar(db, Colegio) == 0
    assert contar(db, Usuario) == 1


@pytest.mark.parametrize("entorno", ["uat", "UAT", "main", "prod", "produccion", "piloto", "", "  ", None])
def test_nunca_corre_fuera_de_qa_o_dev_aunque_seed_e2e_sea_true(db, base, entorno):
    variables = {**VARIABLES}
    variables.pop("ENTORNO")
    if entorno is not None:
        variables["ENTORNO"] = entorno

    resumen = sembrar_e2e(db, variables)

    assert resumen.errores
    assert "no está permitido" in resumen.errores[0]
    assert contar(db, Colegio) == 0
    assert contar(db, Usuario) == 1


@pytest.mark.parametrize("entorno", ["qa", "dev", " QA "])
def test_corre_en_qa_y_dev(db, base, entorno):
    resumen = sembrar_e2e(db, {**VARIABLES, "ENTORNO": entorno})

    assert not resumen.errores
    assert resumen.creados["usuario E2E"] == 4


def test_lee_las_variables_del_entorno_del_proceso(db, base, monkeypatch):
    for nombre, valor in VARIABLES.items():
        monkeypatch.setenv(nombre, valor)

    assert sembrar_e2e(db).creados["usuario E2E"] == 4


def test_sin_supervisor_original_no_siembra(db):
    cargar_catalogos(db)

    resumen = sembrar_e2e(db, VARIABLES)

    assert resumen.advertencias
    assert contar(db, Colegio) == 0


def test_contrasena_faltante_omite_solo_esa_cuenta(db, base):
    variables = {**VARIABLES, "E2E_SUPERVISOR_PASS": "  "}
    variables.pop("E2E_DIRECTIVO_PASS")

    resumen = sembrar_e2e(db, variables)

    assert usuario(db, "E2E_SUPERVISOR_PASS") is None
    assert usuario(db, "E2E_DIRECTIVO_PASS") is None
    assert usuario(db, "E2E_DOCENTE_PASS") is not None
    assert usuario(db, "E2E_INACTIVO_PASS") is not None
    assert len(resumen.advertencias) == 2
    assert not resumen.errores
    assert all("E2E_" in a for a in resumen.advertencias)
    assert all("Directivo-E2e-1" not in a for a in resumen.advertencias)


def test_contrasena_que_no_cumple_cu006_no_crea_la_cuenta(db, base):
    resumen = sembrar_e2e(db, {**VARIABLES, "E2E_DIRECTIVO_PASS": "debil"})

    assert usuario(db, "E2E_DIRECTIVO_PASS") is None
    assert any("CU006" in e for e in resumen.errores)
    assert not any("debil" in e for e in resumen.errores)


def test_sin_cuenta_docente_no_hay_asignaciones(db, base):
    variables = {**VARIABLES}
    variables.pop("E2E_DOCENTE_PASS")

    sembrar_e2e(db, variables)

    assert asignaciones_e2e(db) == set()


# ── Contraseñas, PIN y bloqueos ─────────────────────────────────────────────────────

def test_contrasena_igual_a_la_variable_en_cada_corrida(db, base):
    sembrar_e2e(db, VARIABLES)
    hash_inicial = usuario(db, "E2E_DOCENTE_PASS").password_hash

    sembrar_e2e(db, VARIABLES)
    assert usuario(db, "E2E_DOCENTE_PASS").password_hash == hash_inicial  # no se rehashea sin motivo

    sembrar_e2e(db, {**VARIABLES, "E2E_DOCENTE_PASS": "Otra-Clave-2"})
    cuenta = usuario(db, "E2E_DOCENTE_PASS")
    assert verify_password("Otra-Clave-2", cuenta.password_hash)
    assert not verify_password(CONTRASENAS["E2E_DOCENTE_PASS"], cuenta.password_hash)


def test_limpia_bloqueos_y_pin_solo_de_los_correos_e2e(db, base):
    sembrar_e2e(db, VARIABLES)
    correo_e2e = CORREOS["E2E_SUPERVISOR_PASS"]
    bloqueo = tiempo.ahora_utc() + timedelta(minutes=15)
    db.add(ControlAccesoCorreo(correo=correo_e2e, intentos_fallidos=5, bloqueado_hasta=bloqueo, solicitudes_pin=5))
    db.add(ControlAccesoCorreo(correo="ajeno@sicedu.test", intentos_fallidos=5, bloqueado_hasta=bloqueo))
    cuenta = usuario(db, "E2E_SUPERVISOR_PASS")
    cuenta.codigo_verificacion = "a" * 64
    cuenta.codigo_verificacion_expira = bloqueo
    cuenta.codigo_verificacion_intentos = 2
    db.commit()

    sembrar_e2e(db, VARIABLES)

    db.expire_all()
    assert db.get(ControlAccesoCorreo, correo_e2e) is None
    assert db.get(ControlAccesoCorreo, "ajeno@sicedu.test") is not None
    cuenta = usuario(db, "E2E_SUPERVISOR_PASS")
    assert (cuenta.codigo_verificacion, cuenta.codigo_verificacion_expira, cuenta.codigo_verificacion_intentos) == (
        None, None, 0
    )


# ── Datos ajenos ────────────────────────────────────────────────────────────────────

def fila_actual(db, id_usuario) -> dict:
    """Columnas de la fila tal como están en la BD (no las de un objeto expirado)."""
    fila = db.get(Usuario, id_usuario)
    db.refresh(fila)
    return fila.model_dump()


def test_no_toca_al_supervisor_original(db, base):
    antes = fila_actual(db, base)

    sembrar_e2e(db, VARIABLES)
    sembrar_e2e(db, VARIABLES)

    assert antes["es_supervisor_original"]
    assert fila_actual(db, base) == antes


def test_dni_en_uso_por_otra_cuenta_no_la_pisa(db, base):
    ajena = Usuario(id_rol=db.exec(select(Rol.id_rol).where(Rol.nombre == "Directivo")).one(),
                    correo="ajena@sicedu.test", password_hash="x", dni="90000002", nombres="Ana",
                    apellidos="Ajena", activo=True, creado_por=base, creado_en=tiempo.ahora_utc())
    db.add(ajena)
    db.commit()
    antes = fila_actual(db, ajena.id_usuario)

    resumen = sembrar_e2e(db, VARIABLES)

    assert usuario(db, "E2E_SUPERVISOR_PASS") is None
    assert any("90000002" in e for e in resumen.errores)
    assert antes["dni"] == "90000002"
    assert fila_actual(db, ajena.id_usuario) == antes
    assert resumen.creados["usuario E2E"] == 3


def test_correo_del_supervisor_original_no_se_usa(db):
    cargar_catalogos(db)
    inicializar_supervisor_original(db, datos_supervisor(correo=CORREOS["E2E_SUPERVISOR_PASS"], dni="40000000"))

    resumen = sembrar_e2e(db, VARIABLES)

    assert any("Supervisor original" in e for e in resumen.errores)
    assert usuario(db, "E2E_SUPERVISOR_PASS").es_supervisor_original


# ── Correos con "+" ─────────────────────────────────────────────────────────────────

def test_los_correos_e2e_pasan_la_validacion_y_ya_estan_normalizados():
    validar = TypeAdapter(CorreoValido).validate_python
    for correo in CORREOS.values():
        assert validar(correo) == correo == normalizar_correo(correo)
    assert validar(" SoloParaUnUso123+E2E.Docente@Gmail.com ") == CORREOS["E2E_DOCENTE_PASS"]


def test_login_con_el_correo_e2e_con_mas_y_mayusculas(db, base, client):
    sembrar_e2e(db, VARIABLES)

    ok = client.post("/login", json={"correo": CORREOS["E2E_DOCENTE_PASS"].upper(),
                                     "password": CONTRASENAS["E2E_DOCENTE_PASS"]})
    inactiva = client.post("/login", json={"correo": CORREOS["E2E_INACTIVO_PASS"],
                                           "password": CONTRASENAS["E2E_INACTIVO_PASS"]})

    assert ok.status_code == 200, ok.text
    assert ok.json()["usuario"]["rol"] == "Docente"
    assert inactiva.status_code == 403


# ── Punto de entrada ────────────────────────────────────────────────────────────────

def test_comando_y_paso_de_inicializar(motor, monkeypatch):
    monkeypatch.setattr(orquestador, "engine", motor)
    with Session(motor) as db:
        cargar_catalogos(db)
        inicializar_supervisor_original(db, datos_supervisor())
    for nombre, valor in VARIABLES.items():
        monkeypatch.setenv(nombre, valor)

    assert cli.main(["sembrar-e2e"]) == 0
    assert orquestador.inicializar().creados["usuario E2E"] == 0  # ya sembrado: solo verifica

    with Session(motor) as db:
        assert contar(db, Colegio) == 1
