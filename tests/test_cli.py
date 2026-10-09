"""Comandos de carga de datos maestros (`python -m app.cli`) y datos de prueba (seed)."""
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app import seed_data
from app.cli import __main__ as cli
from app.cli import inicializar as orquestador
from app.cli.calendario import cargar_calendario, leer_calendario, semanas_deseadas
from app.cli.catalogos import cargar_catalogos
from app.cli.colegios import cargar_colegios_iniciales
from app.cli.comun import Resumen, ajustar_secuencia, leer_json
from app.cli.feriados import cargar_feriados, feriados_entre_semana
from app.cli.recovery_keys import SalidaNoPermitida, contenido_archivo, generar_recovery_keys, validar_salida
from app.cli.supervisor import DatosSupervisor, errores_de_validacion, inicializar_supervisor_original
from app.core.tiempo import ahora_utc
from app.models.evaluacion import AsistenciaSemanal, EvaluacionDiagnostica, SemanaReporte
from app.models.organizacion import (
    AnioEscolar,
    Colegio,
    ColegioGrado,
    DiaNoLaborable,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Usuario,
)
from tests.conftest import HASHES_LLAVES, PASSWORD, crear_alumno, crear_colegio, crear_docente

LUNES = date(2030, 3, 4)


def datos_supervisor(**cambios) -> DatosSupervisor:
    base = {
        "correo": "Original@Sicedu.Test ", "password": PASSWORD, "nombres": " Olga ", "apellidos": "Original",
        "dni": "40000000", "recovery_hashes": ",".join(HASHES_LLAVES),
    }
    return DatosSupervisor(**{**base, **cambios})


@pytest.fixture
def con_original(db):
    """BD con catálogos y Supervisor original, sin calendario ni colegios."""
    cargar_catalogos(db)
    inicializar_supervisor_original(db, datos_supervisor())
    return db


def calendario(periodos=None, **anio) -> dict:
    """Año de 2030 con dos periodos de lunes a viernes."""
    if periodos is None:
        periodos = [
            {"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-06"},
            {"numero": 2, "fecha_inicio": "2030-03-25", "fecha_fin": "2030-04-05", "corte_diagnostico": "2030-03-27"},
        ]
    return {"anio": {"nombre": "2030", "fecha_inicio": "2030-03-01", "fecha_fin": "2030-12-20", **anio}, "periodos": periodos}


def escribir(tmp_path: Path, datos, nombre="calendario.json") -> Path:
    ruta = tmp_path / nombre
    ruta.write_text(json.dumps(datos) if not isinstance(datos, str) else datos, encoding="utf-8")
    return ruta


# ── cargar-catalogos ────────────────────────────────────────────────────────────────

def test_cargar_catalogos_es_idempotente(db):
    primera = cargar_catalogos(db)
    segunda = cargar_catalogos(db)

    assert primera.creados["grado"] == 6
    assert primera.creados["rol"] == 3
    assert sum(segunda.creados.values()) == 0
    assert segunda.advertencias == []


def test_cargar_catalogos_avisa_diferencias_sin_sobrescribir(db):
    cargar_catalogos(db)
    datos = json.loads(Path("app/datos/catalogos.json").read_text(encoding="utf-8"))
    datos["grados"][0]["ciclo"] = "V"
    datos["niveles_razkids"][0]["letra"] = "ZZ"
    datos["niveles_esperados_por_grado"][0]["letra"] = "B"
    datos["niveles_esperados_por_grado"].append({"grado": "7.º", "letra": "A"})

    resumen = cargar_catalogos(db, datos)

    assert len(resumen.advertencias) == 3
    assert resumen.errores == ["nivel_esperado_por_grado: no existe el grado 7.º o el nivel A. Se omite."]
    assert db.exec(select(Grado).where(Grado.nombre == "1.º")).one().id_ciclo == 1


def test_cargar_catalogos_sin_archivo(db, monkeypatch):
    monkeypatch.setattr("app.cli.catalogos.leer_json", lambda _ruta: None)

    assert cargar_catalogos(db).errores


# ── inicializar-supervisor-original ─────────────────────────────────────────────────

def test_supervisor_original_se_crea_una_sola_vez(db):
    cargar_catalogos(db)

    creado = inicializar_supervisor_original(db, datos_supervisor())
    repetido = inicializar_supervisor_original(db, datos_supervisor())

    usuario = db.exec(select(Usuario)).one()
    assert (usuario.correo, usuario.nombres, usuario.creado_por) == ("original@sicedu.test", "Olga", usuario.id_usuario)
    assert creado.creados["recovery_key"] == 10
    assert repetido.omitidos == ["el Supervisor original ya existe"]


@pytest.mark.parametrize(
    "cambios, texto",
    [
        ({"password": "corta"}, "PASSWORD no cumple"),
        ({"dni": "123"}, "DNI debe tener"),
        ({"correo": "sin-arroba"}, "CORREO no parece"),
        ({"recovery_hashes": HASHES_LLAVES[0]}, "exactamente 10 hashes"),
        ({"recovery_hashes": ",".join(["x"] * 10)}, "sin formato bcrypt"),
        ({"recovery_hashes": ",".join([HASHES_LLAVES[0]] * 10)}, "repetidos"),
    ],
)
def test_supervisor_original_valida_sus_datos(cambios, texto):
    errores = errores_de_validacion(datos_supervisor(**cambios))

    assert any(texto in e for e in errores)


def test_supervisor_original_con_variables_faltantes(db):
    resumen = inicializar_supervisor_original(db, datos_supervisor(dni=" ", correo=""))

    assert "SUPERVISOR_ORIGINAL_CORREO, SUPERVISOR_ORIGINAL_DNI" in resumen.advertencias[0]


def test_supervisor_original_desde_el_entorno_vacio(db):
    resumen = inicializar_supervisor_original(db)

    assert resumen.advertencias
    assert db.exec(select(Usuario)).all() == []


def test_supervisor_original_con_correo_o_dni_en_uso_y_sin_rol(db, entorno):
    otro = Session(db.get_bind())
    for usuario in otro.exec(select(Usuario).where(Usuario.es_supervisor_original)).all():
        usuario.es_supervisor_original = False
        otro.add(usuario)
    otro.commit()

    resumen = inicializar_supervisor_original(db, datos_supervisor(correo="supervisor@sicedu.test", dni="40000001"))

    assert len(resumen.errores) == 2


def test_supervisor_original_sin_rol_supervisor(db):
    resumen = inicializar_supervisor_original(db, datos_supervisor())

    assert "no existe el rol Supervisor" in resumen.errores[0]


# ── cargar-calendario ───────────────────────────────────────────────────────────────

def test_cargar_calendario_crea_anio_periodos_cortes_y_semanas(con_original, tmp_path):
    db = con_original

    resumen = cargar_calendario(db, escribir(tmp_path, calendario()))

    assert resumen.errores == []
    assert (resumen.creados["año_escolar"], resumen.creados["periodo_academico"]) == (1, 2)
    assert resumen.creados["evaluacion_diagnostica"] == 2
    semanas = db.exec(select(SemanaReporte).order_by(SemanaReporte.numero_semana)).all()
    assert [(s.numero_semana, s.fecha_inicio) for s in semanas] == [
        (1, LUNES), (2, LUNES + timedelta(days=7)), (3, date(2030, 3, 25)), (4, date(2030, 4, 1))
    ]
    assert sum(cargar_calendario(db, escribir(tmp_path, calendario())).creados.values()) == 0


def test_cargar_calendario_actualiza_fechas_y_elimina_semanas_sobrantes(con_original, tmp_path):
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario()))
    cambiado = calendario(
        [
            {"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-08", "corte_diagnostico": "2030-03-07"},
            {"numero": 3, "fecha_inicio": "2030-04-01", "fecha_fin": "2030-04-12", "corte_diagnostico": "2030-04-03"},
        ],
        fecha_fin="2030-12-13",
    )
    db.add(PeriodoAcademico(id_anio_escolar=db.exec(select(AnioEscolar)).one().id_anio_escolar, numero=9,
                            fecha_inicio=date(2030, 9, 2), fecha_fin=date(2030, 9, 6), creado_por=1, creado_en=ahora_utc()))
    db.commit()

    resumen = cargar_calendario(db, escribir(tmp_path, cambiado))

    assert resumen.errores == []
    assert resumen.actualizados["año_escolar"] == 1
    assert resumen.actualizados["periodo_academico"] == 1
    assert resumen.actualizados["evaluacion_diagnostica"] == 1
    assert resumen.eliminados["semana_reporte"] == 2
    assert any("el periodo 2 existe en la BD" in a for a in resumen.advertencias)
    assert [s.numero_semana for s in db.exec(select(SemanaReporte).order_by(SemanaReporte.fecha_inicio)).all()] == [1, 2, 3]


def test_cargar_calendario_no_mueve_semanas_con_registros(con_original, tmp_path):
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario()))
    supervisor = db.exec(select(Usuario)).one()
    colegio = crear_colegio(db, "C", [1], [1], supervisor.id_usuario)
    alumno = crear_alumno(db, "A", "B", colegio, 1, 1, supervisor.id_usuario)
    ficha = Docente(nombres="D", apellidos="D", creado_por=1, creado_en=ahora_utc())
    db.add(ficha)
    db.flush()
    semana = db.exec(select(SemanaReporte).order_by(SemanaReporte.fecha_inicio)).first()
    db.add(AsistenciaSemanal(id_alumno=alumno.id_alumno, id_semana=semana.id_semana, asistio=True,
                             id_docente=ficha.id_docente, fecha_registro=ahora_utc(), creado_por=1, creado_en=ahora_utc()))
    db.commit()
    corrido = calendario(
        [{"numero": 1, "fecha_inicio": "2030-03-11", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-12"}]
    )

    resumen = cargar_calendario(db, escribir(tmp_path, corrido))

    assert "tiene registros" in resumen.errores[0]
    assert db.exec(select(PeriodoAcademico).where(PeriodoAcademico.numero == 1)).one().fecha_inicio == LUNES


@pytest.mark.parametrize(
    "periodos, anio, texto",
    [
        ([{"numero": 1, "fecha_inicio": "2030-03-05", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-06"}], {}, "no es lunes"),
        ([{"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-14", "corte_diagnostico": "2030-03-06"}], {}, "no es viernes"),
        ([{"numero": 1, "fecha_inicio": "2030-03-15", "fecha_fin": "2030-03-04", "corte_diagnostico": "2030-03-06"}], {}, "después de terminar"),
        ([{"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-20"}], {}, "corte"),
        ([{"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-06"}],
         {"fecha_inicio": "2030-03-10"}, "sale del año"),
        ([], {}, "no tiene periodos"),
        ([], {"fecha_inicio": "2031-01-01"}, "empieza (2031-01-01)"),
        ([{"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15", "corte_diagnostico": "2030-03-06"},
          {"numero": 1, "fecha_inicio": "2030-03-11", "fecha_fin": "2030-03-22", "corte_diagnostico": "2030-03-12"}],
         {}, "se solapan"),
    ],
)
def test_validar_calendario(periodos, anio, texto):
    calendario_leido, errores = leer_calendario(calendario(periodos, **anio))

    assert calendario_leido is not None
    assert any(texto in e for e in errores), errores


def test_leer_calendario_con_formato_invalido():
    assert leer_calendario({"anio": {}})[1][0].startswith("formato inválido")
    cal, _ = leer_calendario(calendario())
    assert len(semanas_deseadas(cal)) == 4


def test_cargar_calendario_archivo_invalido_o_sin_supervisor(db, con_original, tmp_path):
    assert cargar_calendario(db, tmp_path / "no-existe.json").errores
    assert cargar_calendario(db, escribir(tmp_path, "{no es json")).errores
    assert cargar_calendario(db, escribir(tmp_path, calendario([]))).errores


def test_cargar_calendario_sin_supervisor_original(db, tmp_path):
    resumen = cargar_calendario(db, escribir(tmp_path, calendario()))

    assert resumen.advertencias == ["No se carga el calendario: todavía no existe el Supervisor original"]


def test_cargar_calendario_real_del_repositorio(con_original):
    assert cargar_calendario(con_original).errores == []
    assert len(con_original.exec(select(EvaluacionDiagnostica)).all()) == 4


# ── cargar-feriados ─────────────────────────────────────────────────────────────────

def test_feriados_de_lunes_a_viernes_en_espanol():
    feriados = feriados_entre_semana({2030})

    assert feriados[date(2030, 1, 1)] == "Feriado nacional: Año Nuevo"
    assert all(dia.weekday() <= 4 for dia in feriados)
    assert feriados_entre_semana(set()) == {}


def test_cargar_feriados(con_original, tmp_path):
    db = con_original
    assert cargar_feriados(db).omitidos == ["feriados: no hay ningún año escolar en la BD"]
    cargar_calendario(db, escribir(tmp_path, calendario()))
    db.add(DiaNoLaborable(fecha=date(2030, 1, 1), motivo="Otro nombre", creado_por=1, creado_en=ahora_utc()))
    db.commit()

    resumen = cargar_feriados(db)

    assert resumen.creados["dia_no_laborable"] > 5
    assert resumen.advertencias == [
        "dia_no_laborable 2030-01-01: ya existe con motivo 'Otro nombre' (la librería dice 'Feriado nacional: Año Nuevo'). "
        "No se modifica."
    ]
    assert sum(cargar_feriados(db).creados.values()) == 0


def test_cargar_feriados_sin_supervisor(db):
    assert cargar_feriados(db).advertencias


# ── cargar-colegios-iniciales ───────────────────────────────────────────────────────

def test_colegios_iniciales_deshabilitados_por_defecto(con_original):
    assert cargar_colegios_iniciales(con_original).omitidos == ["colegios iniciales: CARGAR_COLEGIOS_INICIALES no es true"]


def test_colegios_iniciales_crea_y_completa(con_original):
    db = con_original
    supervisor = db.exec(select(Usuario)).one()
    crear_colegio(db, "AMASHCÁ ", [1], [], supervisor.id_usuario)
    db.commit()

    primera = cargar_colegios_iniciales(db, habilitado=True)
    segunda = cargar_colegios_iniciales(db, habilitado=True)

    assert primera.creados["colegio"] == 8
    assert primera.actualizados["colegio"] == 1
    assert len(segunda.omitidos) == 9
    amashca = db.exec(select(Colegio).where(Colegio.nombre == "AMASHCÁ ")).one()
    assert len(db.exec(select(ColegioGrado).where(ColegioGrado.id_colegio == amashca.id_colegio)).all()) == 6


def test_colegios_iniciales_con_errores(db, con_original, tmp_path):
    assert cargar_colegios_iniciales(db, tmp_path / "nada.json", habilitado=True).errores


def test_colegios_iniciales_sin_supervisor(db):
    assert cargar_colegios_iniciales(db, habilitado=True).advertencias


# ── generar-recovery-keys ───────────────────────────────────────────────────────────

def test_generar_recovery_keys(tmp_path):
    salida = tmp_path / "llaves.txt"

    linea = generar_recovery_keys(salida)

    assert linea.startswith("SUPERVISOR_ORIGINAL_RECOVERY_HASHES='$2")
    assert len(linea.split(",")) == 10
    contenido = salida.read_text(encoding="utf-8")
    assert contenido.count("\n 1. ") == 1
    assert "10. " in contenido


def test_recovery_keys_no_sobrescribe_ni_escribe_en_el_repo(tmp_path):
    existente = tmp_path / "ya.txt"
    existente.write_text("x", encoding="utf-8")

    with pytest.raises(SalidaNoPermitida, match="ya existe"):
        validar_salida(existente)
    with pytest.raises(SalidaNoPermitida, match="dentro del repositorio"):
        validar_salida(Path("llaves.txt"))
    with pytest.raises(SalidaNoPermitida, match="no existe"):
        validar_salida(tmp_path / "falta" / "llaves.txt")
    assert "1. ABCD" in contenido_archivo(["ABCD"])


# ── Piezas comunes, orquestador y punto de entrada ──────────────────────────────────

def test_resumen_y_leer_json(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sicedu.cli")
    resumen = Resumen()
    resumen.creados["x"] += 1
    resumen.advertir("aviso")
    resumen.error("falla")
    otro = Resumen()
    otro.absorber(resumen)

    otro.registrar_en_log("prueba")

    assert "WARNING: aviso" in caplog.text
    assert "ERROR: falla" in caplog.text
    assert leer_json(escribir(tmp_path, "[1]", "ok.json")) == [1]


def test_ajustar_secuencia_no_aplica_en_sqlite(db):
    ajustar_secuencia(db, Grado.id_grado)


@pytest.fixture
def motor_de_la_app(motor, monkeypatch):
    """Los comandos y el seed abren sus propias sesiones con el motor de la app."""
    monkeypatch.setattr(orquestador, "engine", motor)
    monkeypatch.setattr(seed_data, "engine", motor)
    return motor


def test_inicializar_ejecuta_todos_los_pasos(motor_de_la_app, db):
    total = orquestador.inicializar()

    assert total.creados["grado"] == 6
    assert any("Supervisor original" in a for a in total.advertencias)


def test_ejecutar_paso_absorbe_errores_inesperados(motor_de_la_app):
    def falla(_db):
        raise ValueError("dato raro")

    assert orquestador.ejecutar_paso("x", falla).errores == ["x: fallo inesperado (ValueError), ver traceback en el log"]


def test_ejecutar_paso_sin_bd_es_error_de_infraestructura(motor_de_la_app):
    def sin_bd(_db):
        raise OperationalError("SELECT 1", {}, Exception("sin conexión"))

    with pytest.raises(orquestador.ErrorInfraestructura):
        orquestador.ejecutar_paso("x", sin_bd)


@pytest.mark.parametrize(
    "comando",
    ["cargar-catalogos", "inicializar-supervisor-original", "cargar-calendario", "cargar-feriados",
     "cargar-colegios-iniciales", "inicializar"],
)
def test_main_ejecuta_cada_comando(motor_de_la_app, comando):
    assert cli.main([comando]) == 0


def test_main_genera_recovery_keys(tmp_path, capsys):
    assert cli.main(["generar-recovery-keys", "--salida", str(tmp_path / "k.txt")]) == 0
    assert capsys.readouterr().out.startswith("SUPERVISOR_ORIGINAL_RECOVERY_HASHES=")
    assert cli.main(["generar-recovery-keys", "--salida", str(tmp_path / "k.txt")]) == 1


def test_main_sin_bd_sale_con_1(monkeypatch):
    def sin_bd():
        raise orquestador.ErrorInfraestructura("sin BD")

    monkeypatch.setattr(cli, "inicializar", sin_bd)

    assert cli.main(["inicializar"]) == 1


def test_main_comando_invalido_sale_con_2():
    with pytest.raises(SystemExit) as salida:
        cli.main(["no-existe"])

    assert salida.value.code == 2


# ── Seed de datos de prueba ─────────────────────────────────────────────────────────

def test_seed_crea_cuentas_y_es_idempotente(motor_de_la_app, db, capsys):
    seed_data.seed()
    seed_data.seed()

    correos = set(db.exec(select(Usuario.correo)).all())
    assert {seed_data.JEFA_CORREO, seed_data.PROFESOR_CORREO, seed_data.DIRECTIVO_CORREO} <= correos
    assert len(db.exec(select(Colegio)).all()) == 3
    assert "Sin calendario cargado" in capsys.readouterr().out


def test_seed_asigna_al_docente_en_el_periodo_vigente(motor_de_la_app, db, entorno):
    seed_data.seed()

    profesor = db.exec(select(Usuario).where(Usuario.correo == seed_data.PROFESOR_CORREO)).one()
    filas = db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == profesor.id_docente)).all()
    assert [f.id_periodo_academico for f in filas] == [entorno.periodos["vigente"].id_periodo_academico]


def test_seed_completa_datos_de_cuentas_antiguas(motor_de_la_app, db, entorno):
    """Sin periodo vigente, el seed cuelga la asignación del último periodo cargado."""
    vigente = entorno.periodos["vigente"]
    vigente.fecha_fin = entorno.hoy - timedelta(days=1)
    db.add(vigente)
    crear_docente(db, entorno, "Docente", seed_data.PROFESOR_CORREO, "00000002").dni = None
    db.commit()

    seed_data.seed()

    db.expire_all()
    profesor = db.exec(select(Usuario).where(Usuario.correo == seed_data.PROFESOR_CORREO)).one()
    assert profesor.dni == seed_data.PROFESOR_DNI
    assert seed_data.periodo_para_asignaciones(db).id_periodo_academico == entorno.periodos["futuro"].id_periodo_academico
