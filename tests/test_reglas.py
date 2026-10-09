"""Reglas puras: política de contraseña, tiempo, ciclo, correo, Recovery Keys, PIN,
cambios por módulo, auditoría, días hábiles y formato de errores."""
from datetime import date, datetime, timedelta, timezone
from enum import Enum

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core import ciclo, correo, pin, politica_contrasena, recovery_key, tiempo
from app.core.database import insert_con_conflicto, opciones_de_conexion
from app.core.email import enmascarar_correo
from app.core.errores import ErrorNegocio
from app.core.paginacion import pagina
from app.core.validacion import documentar_formato_422, errores_legibles
from app.models.organizacion import DiaNoLaborable, PeriodoAcademico
from app.services import auditoria, cambios, usuario_service
from app.services.calendario import anio_de_referencia, cargar_dias_habiles, dias_habiles, periodo_de_referencia

# ── Política de contraseña (CU006) ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "contrasena, incumplidos",
    [
        ("Valida-123", []),
        ("Ñandú-2026", []),
        ("Corta1!", ["longitud_minima"]),
        ("sinmayuscula1!", ["mayuscula"]),
        ("SINMINUSCULA1!", ["minuscula"]),
        ("SinNumero!!", ["numero"]),
        ("SinEspecial1", ["caracter_especial"]),
        ("Con Espacio1", ["caracter_especial"]),
        ("", ["longitud_minima", "mayuscula", "minuscula", "numero", "caracter_especial"]),
    ],
)
def test_cu006_requisitos_incumplidos(contrasena, incumplidos):
    assert politica_contrasena.requisitos_incumplidos(contrasena) == incumplidos
    assert politica_contrasena.cumple_politica_contrasena(contrasena) is (not incumplidos)
    assert len(politica_contrasena.errores_politica_contrasena(contrasena)) == len(incumplidos)


def test_contrasena_temporal_siempre_cumple_la_politica():
    temporales = {politica_contrasena.generar_contrasena_temporal() for _ in range(50)}

    assert len(temporales) == 50
    assert all(politica_contrasena.cumple_politica_contrasena(t) and len(t) == 14 for t in temporales)
    with pytest.raises(ValueError):
        politica_contrasena.generar_contrasena_temporal(4)


# ── Tiempo ──────────────────────────────────────────────────────────────────────────

def test_hoy_lima_no_depende_de_la_zona_del_servidor(reloj):
    reloj.fijar(datetime(2026, 10, 9, 3, 30, tzinfo=timezone.utc))

    assert tiempo.hoy_lima() == date(2026, 10, 8)
    assert tiempo.ahora_utc().tzinfo == timezone.utc
    assert tiempo.fecha_lima(datetime(2026, 10, 9, 4, 59, tzinfo=timezone.utc)) == date(2026, 10, 8)


def test_limites_lima_cubre_los_dias_completos():
    inicio, fin = tiempo.limites_lima(date(2026, 10, 1), date(2026, 10, 2))

    assert inicio == datetime(2026, 10, 1, 5, tzinfo=timezone.utc)
    assert fin == datetime(2026, 10, 3, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (None, None),
        (datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 10, tzinfo=timezone.utc)),
        (datetime(2026, 1, 1, 5, tzinfo=tiempo.ZONA_LIMA), datetime(2026, 1, 1, 10, tzinfo=timezone.utc)),
    ],
    ids=["nulo", "sin_zona_es_utc", "con_zona"],
)
def test_utc_datetime_normaliza_a_utc(valor, esperado):
    tipo = tiempo.UTCDateTime()

    assert tipo.process_bind_param(valor, None) == esperado
    assert tipo.process_result_value(valor, None) == esperado


# ── Ciclo, correo, Recovery Keys, PIN ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "programa, ciclo_del_grado, esperado",
    [("Alfabetización", "V", "III"), ("Comprensión Lectora", "IV", "IV"), ("Comprensión Lectora", "III", "III")],
)
def test_cu014_ciclo_calculado(programa, ciclo_del_grado, esperado):
    assert ciclo.calcular_ciclo(programa, ciclo_del_grado) == esperado


def test_ciclos_ordenados_y_regla_de_primero():
    assert ciclo.ordenar_ciclos({"V", "III", "X", "IV"}) == ["III", "IV", "V", "X"]
    assert ciclo.programa_permitido_en_grado("1.º", "Alfabetización")
    assert not ciclo.programa_permitido_en_grado("1.º", "Comprensión Lectora")
    assert ciclo.programa_permitido_en_grado("2.º", "Comprensión Lectora")


@pytest.mark.parametrize("valor", ["a@b", "@b.c", "a@b..c", "a b@c.d", "a@b@c.d"])
def test_correos_mal_formados(valor):
    with pytest.raises(ValueError):
        correo._validar_formato(valor)


def test_correo_normalizado_y_enmascarado():
    assert correo.normalizar_correo("  Rosa@MH.org ") == "rosa@mh.org"
    assert enmascarar_correo("rosa.c@mh.org") == "ro***@mh.org"
    assert enmascarar_correo("sin-arroba") == "***"


def test_recovery_key_formato_y_normalizacion():
    llave = recovery_key.generar_llave()

    assert len(llave) == 34
    assert llave.count("-") == 6
    assert recovery_key.normalizar_llave(llave.lower().replace("-", "—")) == llave
    assert recovery_key.normalizar_llave(" ab-cd ") == "ABCD"


def test_pin_hmac_depende_del_usuario():
    codigo = pin.generar_pin()

    assert len(codigo) == 6
    assert codigo.isdigit()
    assert pin.pin_coincide(1, codigo, pin.hmac_pin(1, codigo))
    assert not pin.pin_coincide(2, codigo, pin.hmac_pin(1, codigo))


# ── Cambios por módulo (CU017, CU019, CU021) ────────────────────────────────────────

@pytest.mark.parametrize(
    "conteos, texto",
    [
        ({}, "Sin registros"),
        ({"rubrica": 1}, "1 Rúbrica"),
        ({"registro_vuelo": 2, "rubrica": 15}, "15 Rúbricas, 2 Registros de Vuelo"),
        ({"alumnos": 1, "asistencia": 3, "otros": 0}, "1 Alumno, 3 Registros de Asistencia"),
        ({"seguimiento_lectura": 1, "otros": 2}, "1 Seguimiento de Lectura, 2 Otros registros"),
    ],
)
def test_cu021_productividad_texto(conteos, texto):
    assert cambios.productividad_texto(conteos) == texto


def test_modulo_de_cada_tabla():
    assert cambios.modulo_de("nivel_final_mensual") == "rubrica"
    assert cambios.modulo_de("libro_subida_nivel") == "seguimiento_lectura"
    assert cambios.modulo_de("tabla_sin_modulo") == "otros"
    assert cambios.modulo_de("tabla_sin_modulo") == "otros"


def test_resumen_por_modulo_cuenta_registros_distintos():
    registros = [("alumno", "1", "crear"), ("alumno", "1", "editar"), ("asistencia_semanal", "7", "crear")]

    resumen = cambios.resumen_por_modulo(registros)

    assert [(m.modulo, m.total, m.creados, m.editados) for m in resumen] == [("alumnos", 1, 1, 1), ("asistencia", 1, 1, 0)]


def test_asignaciones_involucradas_de_registros_de_otras_tablas(db, entorno):
    luis = entorno.alumnos["luis"].id_alumno
    registros = [("asistencia_semanal", "999", "crear"), ("libro_subida_nivel", "999", "crear"), ("alumno", str(luis), "editar")]

    asignaciones = cambios.asignaciones_involucradas(db, registros)

    assert [a.grado.nombre for a in asignaciones] == ["1.º"]


def test_cambios_por_actividad_sin_ids(db):
    assert cambios.cambios_por_actividad(db, []) == {}


# ── Auditoría ───────────────────────────────────────────────────────────────────────

class Color(Enum):
    ROJO = "rojo"


@pytest.mark.parametrize(
    "valor, esperado",
    [(None, None), (True, "true"), (date(2026, 1, 2), "2026-01-02"), (Color.ROJO, "rojo"), (3, "3")],
)
def test_serializar_valores_de_auditoria(valor, esperado):
    assert auditoria.serializar(valor) == esperado


def test_auditoria_rechaza_acciones_desconocidas_y_no_guarda_secretos(db):
    fecha = tiempo.ahora_utc()
    with pytest.raises(ValueError):
        auditoria.registrar_auditoria(db, tabla="t", id_registro=1, accion="borrar", id_usuario=1, fecha=fecha)

    filas = auditoria.registrar_auditoria(
        db, tabla="usuario", id_registro=1, accion="editar", id_usuario=1, fecha=tiempo.ahora_utc(),
        cambios={"password_hash": ("a", "b"), "nombres": ("Ana", "Ana")},
    )

    assert filas == 0


def test_id_actividad_activa(db, entorno, actividad):
    assert str(auditoria.id_actividad_activa(db, entorno.id_docente)) == actividad["id"]
    assert auditoria.id_actividad_activa(db, 999) is None


# ── Calendario: días hábiles y periodo de referencia ────────────────────────────────

def test_dias_habiles_lunes_a_viernes_dentro_de_periodos_sin_feriados(db, entorno):
    lunes = entorno.hoy - timedelta(days=entorno.hoy.weekday() + 7)
    db.add(DiaNoLaborable(fecha=lunes + timedelta(days=2), motivo="Feriado", creado_por=1, creado_en=tiempo.ahora_utc()))
    db.commit()

    assert dias_habiles(db, lunes, lunes + timedelta(days=7)) == 4
    assert dias_habiles(db, lunes, lunes) == 0
    assert cargar_dias_habiles(db, lunes, lunes - timedelta(days=1)).contar(lunes, lunes) == 0


def test_dias_entre_periodos_no_son_habiles(db, entorno):
    """Entre el fin del periodo pasado y el inicio del vigente no hay clases."""
    desde = entorno.periodos["pasado"].fecha_fin + timedelta(days=1)

    assert dias_habiles(db, desde, entorno.periodos["vigente"].fecha_inicio) == 0


def test_periodo_y_anio_de_referencia_entre_anios(db, entorno, reloj):
    reloj.fijar(tiempo.ahora_utc() + timedelta(days=400))

    assert anio_de_referencia(db).id_anio_escolar == entorno.anio.id_anio_escolar
    assert periodo_de_referencia(db).id_periodo_academico == entorno.periodos["futuro"].id_periodo_academico


def test_anio_de_referencia_antes_de_que_empiece(db, entorno, reloj):
    reloj.fijar(tiempo.ahora_utc() - timedelta(days=400))

    assert anio_de_referencia(db).id_anio_escolar == entorno.anio.id_anio_escolar


def test_sin_calendario_no_hay_referencia(db):
    assert periodo_de_referencia(db) is None
    assert db.exec(select(PeriodoAcademico)).all() == []


# ── Formato de errores, paginación y base de datos ──────────────────────────────────

@pytest.mark.parametrize(
    "error, mensaje",
    [
        ({"type": "string_too_short", "ctx": {"min_length": 3}}, "Debe tener al menos 3 caracteres."),
        ({"type": "too_short", "ctx": {"min_length": 1}}, "Debe tener al menos un elemento."),
        ({"type": "too_short", "ctx": {"min_length": 2}}, "Debe tener al menos 2 elementos."),
        ({"type": "less_than_equal", "ctx": {"le": 5}}, "Debe ser menor o igual que 5."),
        ({"type": "enum", "ctx": {"expected": "'a'"}}, "Valor no permitido. Valores aceptados: 'a'."),
        ({"type": "int_type", "input": None}, "Este campo no puede ser nulo."),
        ({"type": "desconocido"}, "Valor inválido."),
    ],
)
def test_mensajes_de_validacion_en_espanol(error, mensaje):
    assert errores_legibles([{"loc": ("body", "campo", 0), **error}]) == [{"campo": "campo.0", "mensaje": mensaje}]


def test_openapi_documenta_el_formato_propio_de_422():
    app = FastAPI()

    @app.get("/x")
    def ruta(n: int):
        return n

    documentar_formato_422(app)
    esquema = app.openapi()

    assert set(esquema["components"]["schemas"]["HTTPValidationError"]["properties"]) == {"detail", "motivo", "errores"}
    assert app.openapi() is esquema


def test_rutas_inexistentes_y_metodos_no_admitidos_en_espanol(client):
    assert client.get("/no-existe").json() == {"detail": "Recurso no encontrado."}
    respuesta = client.put("/login")
    assert respuesta.json() == {"detail": "Método no permitido."}
    assert "POST" in respuesta.headers["allow"]
    assert client.get("/").json() == {"status": "ok"}


def test_error_de_negocio_sin_motivo():
    assert ErrorNegocio(400, "x").cuerpo() == {"detail": "x"}


def test_paginacion_vacia():
    assert pagina([], 0, 1).total_pages == 0


def test_opciones_de_conexion_e_insert_por_dialecto(db):
    assert opciones_de_conexion("postgresql://u:p@h/db") == {"connect_timeout": 10}
    assert opciones_de_conexion("sqlite://") == {}
    insert_con_conflicto(db, DiaNoLaborable.__table__)

    class OtroMotor:
        class dialect:  # noqa: N801 - imita la forma de un Engine de SQLAlchemy
            name = "oracle"

    class SesionFalsa:
        def get_bind(self):
            return OtroMotor()

    sesion, tabla = SesionFalsa(), DiaNoLaborable.__table__
    with pytest.raises(NotImplementedError):
        insert_con_conflicto(sesion, tabla)


@pytest.mark.parametrize(
    "mensaje, esperado",
    [("UNIQUE docente_colegio_grado", "grado_asignado"), ("uq_usuario_dni", "dni_duplicado"), ("usuario.correo", "correo_duplicado")],
)
def test_confirmar_traduce_unicidad_a_409(mensaje, esperado):
    class SesionQueChoca:
        def commit(self):
            raise IntegrityError("INSERT", {}, Exception(mensaje))

        def rollback(self):
            self.revertida = True

    sesion = SesionQueChoca()
    with pytest.raises(ErrorNegocio) as error:
        usuario_service._confirmar(sesion)

    assert (error.value.status_code, error.value.motivo) == (409, esperado)


def test_confirmar_propaga_otras_violaciones():
    class SesionQueChoca:
        def commit(self):
            raise IntegrityError("INSERT", {}, Exception("otra cosa"))

        def rollback(self):
            pass

    sesion = SesionQueChoca()
    with pytest.raises(IntegrityError):
        usuario_service._confirmar(sesion)
