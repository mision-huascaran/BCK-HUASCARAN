"""Comandos de carga de datos maestros (`python -m app.cli`) y datos de prueba (seed)."""
import json
import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app import seed_data
from app.cli import __main__ as cli
from app.cli import inicializar as orquestador
from app.cli.calendario import cargar_calendario, leer_calendario, semanas_deseadas
from app.cli.catalogos import cargar_catalogos
from app.cli.colegios import cargar_colegios_iniciales
from app.cli.comun import Resumen, ajustar_secuencia, leer_json
from app.cli.feriados import cargar_feriados, feriados_entre_semana
from app.cli.supervisor import DatosSupervisor, errores_de_validacion, inicializar_supervisor_original
from app.core.config import Settings
from app.core.security import verify_password
from app.core.tiempo import ahora_utc
from app.models.evaluacion import SemanaReporte
from app.models.organizacion import (
    AnioEscolar,
    Calendario,
    Colegio,
    ColegioGrado,
    DiaNoLectivo,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Usuario,
)
from app.services.calendario import cargar_calendarios
from tests.conftest import (
    PASSWORD,
    agregar_no_lectivo,
    crear_alumno,
    crear_colegio,
    crear_docente,
    fijar_periodo,
    registrar_asistencia,
    releer,
    tabla_lectura,
    terminar_pa_vigente,
)

LUNES = date(2030, 3, 4)

# Un .env como el de Jenkins, que aún trae los hashes de las Recovery Keys eliminadas.
ENV_CON_VARIABLE_OBSOLETA = "\n".join([
    "DATABASE_URL=sqlite://",
    "JWT_SECRET_KEY=x",
    "SUPERVISOR_ORIGINAL_RECOVERY_HASHES='$2b$12$abc,$2b$12$def'",
])


def datos_supervisor(**cambios) -> DatosSupervisor:
    base = {
        "correo": "Original@Sicedu.Test ", "password": PASSWORD, "nombres": " Olga ", "apellidos": "Original",
        "dni": "40000000",
    }
    return DatosSupervisor(**{**base, **cambios})


@pytest.fixture
def con_original(db):
    """BD con catálogos y Supervisor original, sin calendario ni colegios."""
    cargar_catalogos(db)
    inicializar_supervisor_original(db, datos_supervisor())
    return db


# PA1 a PA4 de 2030, de lunes a viernes: 2, 2, 1 y 1 semanas.
PAS_2030 = (
    {"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15"},
    {"numero": 2, "fecha_inicio": "2030-03-25", "fecha_fin": "2030-04-05"},
    {"numero": 3, "fecha_inicio": "2030-05-06", "fecha_fin": "2030-05-10"},
    {"numero": 4, "fecha_inicio": "2030-06-03", "fecha_fin": "2030-06-07"},
)


def calendario(periodos=None, recesos=None, **anio) -> dict:
    """Año 2030 con sus PA (por defecto PAS_2030) y, si se pasan, sus recesos."""
    datos = {
        "anio": {"nombre": "2030", "fecha_inicio": "2030-03-01", "fecha_fin": "2030-12-20", **anio},
        "periodos": [dict(p) for p in (PAS_2030 if periodos is None else periodos)],
    }
    if recesos is not None:
        datos["recesos"] = recesos
    return datos


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
    assert primera.creados["escala_nivel_descriptivo"] == 24
    assert sum(segunda.creados.values()) == 0
    assert segunda.advertencias == []


def test_cargar_catalogos_avisa_diferencias_sin_sobrescribir(db):
    cargar_catalogos(db)
    datos = json.loads(Path("app/datos/catalogos.json").read_text(encoding="utf-8"))
    datos["grados"][0]["ciclo"] = "V"
    datos["niveles_razkids"][0]["letra"] = "ZZ"
    datos["niveles_esperados_por_grado"][0]["letra"] = "B"
    datos["niveles_esperados_por_grado"].append({"grado": "7.º", "letra": "A"})
    datos["escala_nivel_descriptivo"][0]["desde"]["Logrado"] = "B"
    datos["escala_nivel_descriptivo"][1]["desde"]["Avanzado"] = "Ñ"
    datos["escala_nivel_descriptivo"].append({"grado": "7.º", "desde": {"Inicio": "aa"}})

    resumen = cargar_catalogos(db, datos)

    assert len(resumen.advertencias) == 4
    assert resumen.advertencias[-1].startswith("escala_nivel_descriptivo 1.º Logrado: la BD tiene id_nivel_rk_desde=4")
    assert resumen.errores == [
        "nivel_esperado_por_grado: no existe el grado 7.º o el nivel A. Se omite.",
        "escala_nivel_descriptivo 2.º Avanzado: no existe el nivel Ñ. Se omite.",
        "escala_nivel_descriptivo: no existe el grado 7.º. Se omite.",
    ]
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
    assert usuario.debe_cambiar_contrasena is True
    assert creado.creados == {"usuario (Supervisor original)": 1}
    assert repetido.omitidos == ["el Supervisor original ya existe"]


def test_supervisor_original_se_crea_sin_hashes(db, monkeypatch):
    """CU001 ya no usa Recovery Keys: bastan correo, contraseña, nombres, apellidos y DNI,
    leídos del entorno, y la variable obsoleta se ignora aunque traiga basura."""
    cargar_catalogos(db)
    for campo, valor in {
        "CORREO": "original@sicedu.test", "PASSWORD": PASSWORD, "NOMBRES": "Olga",
        "APELLIDOS": "Original", "DNI": "40000000", "RECOVERY_HASHES": "no-es-un-hash",
    }.items():
        monkeypatch.setenv(f"SUPERVISOR_ORIGINAL_{campo}", valor)
    monkeypatch.setattr("app.cli.supervisor.settings", Settings())

    resumen = inicializar_supervisor_original(db)

    assert resumen.errores == []
    assert resumen.creados == {"usuario (Supervisor original)": 1}
    usuario = db.exec(select(Usuario)).one()
    assert usuario.es_supervisor_original
    assert verify_password(PASSWORD, usuario.password_hash)


def test_settings_acepta_la_variable_obsoleta_en_el_env(tmp_path):
    """Settings rechaza las claves desconocidas del .env: la de las Recovery Keys sigue
    declarada para que un .env (o un secreto de Jenkins) que aún la tenga no tumbe la app."""
    env = escribir(
        tmp_path,
        ENV_CON_VARIABLE_OBSOLETA,
        ".env",
    )

    assert Settings(_env_file=env).SUPERVISOR_ORIGINAL_RECOVERY_HASHES is not None


def test_la_app_arranca_con_la_variable_obsoleta_en_el_env(tmp_path):
    """Como en Jenkins: un proceso nuevo importa la app con un .env que aún la trae."""
    escribir(
        tmp_path,
        ENV_CON_VARIABLE_OBSOLETA,
        ".env",
    )
    entorno = {
        clave: valor for clave, valor in os.environ.items()
        if clave not in ("DATABASE_URL", "JWT_SECRET_KEY") and not clave.startswith("SUPERVISOR_ORIGINAL_")
    }
    entorno["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    resultado = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(len(app.routes))"],
        cwd=tmp_path, env=entorno, capture_output=True, text=True, timeout=60,
    )

    assert resultado.returncode == 0, resultado.stderr


@pytest.mark.parametrize(
    "cambios, texto",
    [
        ({"password": "corta"}, "PASSWORD no cumple"),
        ({"dni": "123"}, "DNI debe tener"),
        ({"correo": "sin-arroba"}, "CORREO no parece"),
    ],
)
def test_supervisor_original_valida_sus_datos(cambios, texto):
    errores = errores_de_validacion(datos_supervisor(**cambios))

    assert any(texto in e for e in errores)


def test_supervisor_original_rechazado_por_la_bd_no_queda_a_medias(db, monkeypatch):
    cargar_catalogos(db)

    def commit_que_choca(_sesion):
        raise IntegrityError("INSERT INTO usuario", {}, Exception("unique"))

    monkeypatch.setattr(Session, "commit", commit_que_choca)
    resumen = inicializar_supervisor_original(db, datos_supervisor())
    monkeypatch.undo()

    assert resumen.errores == ["No se crea el Supervisor original: la BD rechazó la fila (Exception)"]
    assert resumen.creados == {}
    assert db.exec(select(Usuario)).all() == []


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

def general_2030(db):
    return cargar_calendarios(db).generales[db.exec(select(AnioEscolar)).one().id_anio_escolar]


def test_cargar_calendario_crea_anio_periodos_calendario_y_semanas(con_original, tmp_path):
    db = con_original

    resumen = cargar_calendario(db, escribir(tmp_path, calendario()))

    assert resumen.errores == []
    assert (resumen.creados["año_escolar"], resumen.creados["periodo_academico"], resumen.creados["calendario"]) == (1, 4, 1)
    # Sin `recesos` en el archivo, cada PR cubre el hueco hasta el PA siguiente y el PR4
    # llega al fin del año.
    assert [(p.nombre, p.fecha_inicio, p.fecha_fin) for p in general_2030(db).periodos] == [
        ("PA1", date(2030, 3, 4), date(2030, 3, 15)), ("PR1", date(2030, 3, 16), date(2030, 3, 24)),
        ("PA2", date(2030, 3, 25), date(2030, 4, 5)), ("PR2", date(2030, 4, 6), date(2030, 5, 5)),
        ("PA3", date(2030, 5, 6), date(2030, 5, 10)), ("PR3", date(2030, 5, 11), date(2030, 6, 2)),
        ("PA4", date(2030, 6, 3), date(2030, 6, 7)), ("PR4", date(2030, 6, 8), date(2030, 12, 20)),
    ]
    # Todo el año escolar (01/03 viernes a 20/12), de lunes a viernes, sin depender de los PA:
    # la primera semana es la del viernes 1 de marzo.
    semanas = db.exec(select(SemanaReporte).order_by(SemanaReporte.numero_semana)).all()
    assert (semanas[0].fecha_inicio, semanas[0].fecha_fin, semanas[-1].fecha_inicio) == (
        date(2030, 2, 25), date(2030, 3, 1), date(2030, 12, 16),
    )
    assert [s.numero_semana for s in semanas] == list(range(1, len(semanas) + 1))
    assert all(s.fecha_inicio == semanas[0].fecha_inicio + timedelta(weeks=i) for i, s in enumerate(semanas))
    otra = cargar_calendario(db, escribir(tmp_path, calendario()))
    assert (sum(otra.creados.values()), otra.advertencias) == (0, [])


def test_cargar_calendario_usa_los_recesos_del_archivo(con_original, tmp_path):
    db = con_original
    recesos = [
        {"numero": 1, "fecha_inicio": "2030-03-16", "fecha_fin": "2030-03-17"},
        {"numero": 2, "fecha_inicio": "2030-04-06", "fecha_fin": "2030-04-07"},
        {"numero": 3, "fecha_inicio": "2030-05-11", "fecha_fin": "2030-05-12"},
        {"numero": 4, "fecha_inicio": "2030-06-08", "fecha_fin": "2031-02-28"},
    ]

    assert cargar_calendario(db, escribir(tmp_path, calendario(recesos=recesos))).errores == []

    periodos = {p.nombre: (p.fecha_inicio, p.fecha_fin) for p in general_2030(db).periodos}
    assert (periodos["PR1"], periodos["PR4"]) == ((date(2030, 3, 16), date(2030, 3, 17)), (date(2030, 6, 8), date(2031, 2, 28)))


def test_cargar_calendario_no_pisa_el_calendario_de_la_api(con_original, tmp_path):
    """Desde CU027 el general lo gestiona el Supervisor: un archivo distinto no lo cambia
    (WARNING). El año sí lo manda el archivo."""
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario()))
    cambiado = calendario(fecha_fin="2030-12-13")
    cambiado["periodos"][0] = {"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-08",
                               }
    db.add(PeriodoAcademico(id_anio_escolar=db.exec(select(AnioEscolar)).one().id_anio_escolar, numero=9,
                            creado_por=1, creado_en=ahora_utc()))
    db.commit()

    resumen = cargar_calendario(db, escribir(tmp_path, cambiado))

    assert resumen.errores == []
    assert resumen.actualizados["año_escolar"] == 1
    assert any("calendario general 2030: difiere del archivo y no se modifica" in a for a in resumen.advertencias)
    assert any("el periodo 9 existe en la BD" in a for a in resumen.advertencias)
    assert general_2030(db).pa(1).fecha_fin == date(2030, 3, 15)
    # El año termina una semana antes: su última semana sobra.
    assert resumen.eliminados["semana_reporte"] == 1


def test_cargar_calendario_las_semanas_no_dependen_de_los_pa(con_original, tmp_path):
    """Si el Supervisor acortó el PA2, las semanas no cambian: cubren todo el año."""
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario()))
    antes = [s.fecha_inicio for s in db.exec(select(SemanaReporte).order_by(SemanaReporte.fecha_inicio)).all()]
    general = db.exec(select(Calendario)).one()
    fijar_periodo(db, general, "PA2", fin=date(2030, 3, 29))
    fijar_periodo(db, general, "PR2", inicio=date(2030, 3, 30))

    resumen = cargar_calendario(db, escribir(tmp_path, calendario()))

    assert (resumen.eliminados["semana_reporte"], resumen.actualizados["semana_reporte"]) == (0, 0)
    assert [s.fecha_inicio for s in db.exec(select(SemanaReporte).order_by(SemanaReporte.fecha_inicio)).all()] == antes


def test_cargar_calendario_no_mueve_semanas_con_registros(con_original, tmp_path):
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario()))
    supervisor = db.exec(select(Usuario)).one()
    colegio = crear_colegio(db, "C", [1], [1], supervisor.id_usuario)
    alumno = crear_alumno(db, "A", "B", colegio, 1, 1, supervisor.id_usuario)
    db.add(Docente(nombres="D", apellidos="D", creado_por=1, creado_en=ahora_utc()))
    db.flush()
    # Una tabla de lectura en la última semana del año (16 a 20 de diciembre).
    tabla = tabla_lectura(db, alumno, date(2030, 12, 17))

    resumen = cargar_calendario(db, escribir(tmp_path, calendario(fecha_fin="2030-12-13")))

    assert "tiene registros" in resumen.errores[0]
    assert db.exec(select(AnioEscolar)).one().fecha_fin == date(2030, 12, 20)
    assert db.get(SemanaReporte, tabla.id_semana) is not None


def con_pa1(**cambios) -> list[dict]:
    """Los PA de `calendario()` con el PA1 cambiado."""
    periodos = calendario()["periodos"]
    periodos[0] = {**periodos[0], **cambios}
    return periodos


@pytest.mark.parametrize(
    "periodos, anio, texto",
    [
        (con_pa1(fecha_inicio="2030-03-05"), {}, "no es lunes"),
        (con_pa1(fecha_fin="2030-03-14"), {}, "no es viernes"),
        (con_pa1(fecha_inicio="2030-03-15", fecha_fin="2030-03-04"), {}, "después de terminar"),
        (con_pa1(), {"fecha_inicio": "2030-03-10"}, "sale del año"),
        ([], {}, "debe tener los periodos 1 a 4"),
        (calendario()["periodos"][:3], {}, "debe tener los periodos 1 a 4"),
        ([], {"fecha_inicio": "2031-01-01"}, "empieza (2031-01-01)"),
        (con_pa1(fecha_fin="2030-03-29"), {}, "se solapan"),
        (con_pa1(fecha_fin="2030-03-24"), {}, "se solapan o no dejan días para el receso"),
    ],
)
def test_validar_calendario(periodos, anio, texto):
    calendario_leido, errores = leer_calendario(calendario(periodos, **anio))

    assert calendario_leido is not None
    assert any(texto in e for e in errores), errores


def test_validar_recesos_del_archivo():
    recesos = [{"numero": n, "fecha_inicio": "2030-03-16", "fecha_fin": "2030-03-17"} for n in (1, 2, 3, 4)]

    _, errores = leer_calendario(calendario(recesos=recesos))

    assert any("PA2 debe empezar después del fin de PR1" in e or "PR2" in e for e in errores), errores


def test_leer_calendario_con_formato_invalido():
    assert leer_calendario({"anio": {}})[1][0].startswith("formato inválido")
    cal, _ = leer_calendario(calendario())
    assert len(semanas_deseadas(cal.inicio, cal.fin)) == 43
    # Un año que empieza en sábado arranca con la semana siguiente.
    assert semanas_deseadas(date(2030, 3, 2), date(2030, 3, 8))[0].lunes == date(2030, 3, 4)
    # Uno que empieza un miércoles incluye esa semana; uno que termina un lunes, la suya.
    assert [s.lunes for s in semanas_deseadas(date(2030, 3, 6), date(2030, 3, 18))] == [
        date(2030, 3, 4), date(2030, 3, 11), date(2030, 3, 18)
    ]


def test_cargar_calendario_archivo_invalido_o_sin_supervisor(db, con_original, tmp_path):
    assert cargar_calendario(db, tmp_path / "no-existe.json").errores
    assert cargar_calendario(db, escribir(tmp_path, "{no es json")).errores
    assert cargar_calendario(db, escribir(tmp_path, calendario([]))).errores


def test_cargar_calendario_sin_supervisor_original(db, tmp_path):
    resumen = cargar_calendario(db, escribir(tmp_path, calendario()))

    assert resumen.advertencias == ["No se carga el calendario: todavía no existe el Supervisor original"]


def test_cargar_calendario_real_del_repositorio(con_original):
    assert cargar_calendario(con_original).errores == []
    general = cargar_calendarios(con_original).generales
    assert [p.nombre for p in next(iter(general.values())).periodos] == [
        "PA1", "PR1", "PA2", "PR2", "PA3", "PR3", "PA4", "PR4"
    ]


# ── cargar-feriados ─────────────────────────────────────────────────────────────────

# PA3 del 22/07 al 2/08 (feriados el martes 23 y el lunes 29) y PA4 del 26 al 30/08
# (Santa Rosa, el viernes 30). El 6/08 cae en el PR3 y los de octubre en adelante, en el PR4.
PAS_CON_FERIADOS = [
    {"numero": 1, "fecha_inicio": "2030-03-04", "fecha_fin": "2030-03-15"},
    {"numero": 2, "fecha_inicio": "2030-03-25", "fecha_fin": "2030-04-05"},
    {"numero": 3, "fecha_inicio": "2030-07-22", "fecha_fin": "2030-08-02"},
    {"numero": 4, "fecha_inicio": "2030-08-26", "fecha_fin": "2030-08-30"},
]


def no_lectivos(db) -> dict[date, DiaNoLectivo]:
    db.expire_all()
    return {d.fecha_inicio: d for d in db.exec(select(DiaNoLectivo)).all()}


def test_feriados_de_lunes_a_viernes_en_espanol():
    feriados = feriados_entre_semana({2030})

    assert feriados[date(2030, 1, 1)] == "Feriado nacional: Año Nuevo"
    assert all(dia.weekday() <= 4 for dia in feriados)
    assert feriados_entre_semana(set()) == {}


def test_cargar_feriados_en_el_general_solo_dentro_de_un_pa(con_original, tmp_path):
    db = con_original
    assert cargar_feriados(db).omitidos == ["feriados: no hay ningún calendario general en la BD"]
    cargar_calendario(db, escribir(tmp_path, calendario(PAS_CON_FERIADOS)))
    general = db.exec(select(Calendario)).one()
    agregar_no_lectivo(db, general, date(2030, 7, 29), motivo="Otro nombre")

    resumen = cargar_feriados(db)

    assert resumen.creados["dia_no_lectivo"] == 2
    assert resumen.advertencias == [
        "dia_no_lectivo 2030-07-29 (calendario general 1): ya existe con motivo 'Otro nombre' (la librería dice "
        "'Feriado nacional: Día de la Gran Parada Militar'). No se modifica."
    ]
    dias = no_lectivos(db)
    assert set(dias) == {date(2030, 7, 23), date(2030, 7, 29), date(2030, 8, 30)}
    assert (dias[date(2030, 8, 30)].origen, dias[date(2030, 8, 30)].fecha_fin) == ("automatico", date(2030, 8, 30))
    assert releer(db, Calendario, general.id_calendario).version == 2
    assert sum(cargar_feriados(db).creados.values()) == 0
    assert releer(db, Calendario, general.id_calendario).version == 2


def test_cargar_feriados_no_vuelve_no_lectiva_una_fecha_con_asistencias(con_original, tmp_path):
    db = con_original
    cargar_calendario(db, escribir(tmp_path, calendario(PAS_CON_FERIADOS)))
    supervisor = db.exec(select(Usuario)).one()
    colegio = crear_colegio(db, "C", [1], [1], supervisor.id_usuario)
    alumno = crear_alumno(db, "A", "B", colegio, 1, 1, supervisor.id_usuario)
    ficha = Docente(nombres="D", apellidos="D", creado_por=1, creado_en=ahora_utc())
    db.add(ficha)
    db.flush()
    registrar_asistencia(db, alumno, date(2030, 8, 30), id_docente=ficha.id_docente)

    resumen = cargar_feriados(db)

    assert set(no_lectivos(db)) == {date(2030, 7, 23), date(2030, 7, 29)}
    assert any("2030-08-30" in a and "asistencias" in a for a in resumen.advertencias)


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
    salida = capsys.readouterr().out
    assert "Sin calendario cargado" in salida
    assert seed_data.PROFESOR_CORREO in salida


def test_seed_no_imprime_contrasenas(motor_de_la_app, capsys):
    """Bug #500: la salida del seed queda en el log del despliegue."""
    seed_data.seed()

    salida = capsys.readouterr().out
    for contrasena in (seed_data.PROFESOR_PASSWORD, seed_data.JEFA_PASSWORD, seed_data.DIRECTIVO_PASSWORD):
        assert contrasena not in salida
    assert "password" not in salida.lower()


def test_seed_asigna_al_docente_en_el_periodo_vigente(motor_de_la_app, db, entorno):
    seed_data.seed()

    profesor = db.exec(select(Usuario).where(Usuario.correo == seed_data.PROFESOR_CORREO)).one()
    filas = db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == profesor.id_docente)).all()
    assert [f.id_periodo_academico for f in filas] == [entorno.periodos["vigente"].id_periodo_academico]


def test_seed_completa_datos_de_cuentas_antiguas(motor_de_la_app, db, entorno):
    """Si hoy el calendario general no está en un PA, el seed cuelga la asignación del
    último periodo cargado (el PA4)."""
    terminar_pa_vigente(db, entorno)
    crear_docente(db, entorno, "Docente", seed_data.PROFESOR_CORREO, "00000002").dni = None
    db.commit()

    seed_data.seed()

    db.expire_all()
    profesor = db.exec(select(Usuario).where(Usuario.correo == seed_data.PROFESOR_CORREO)).one()
    assert profesor.dni == seed_data.PROFESOR_DNI
    assert seed_data.periodo_para_asignaciones(db).id_periodo_academico == entorno.periodos["cuarto"].id_periodo_academico
