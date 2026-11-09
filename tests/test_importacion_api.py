"""Importación de la nómina (CU014): endpoints."""
import asyncio
import uuid
import zipfile
from datetime import date
from io import BytesIO

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.config import settings
from app.main import app
from app.models.organizacion import Alumno, AlumnoPertenencia, ColegioGrado, ImportacionAlumnos, Usuario
from app.models.trazabilidad import Auditoria
from app.services import importacion_service
from app.services.importacion_nomina import MAXIMO_ALUMNOS, TAMANO_MAXIMO
from tests.conftest import a_las, cabecera_de, crear_colegio, motivo
from tests.nomina_factory import FILA_VACIA, SIN_DNI, fila, fila_sin_dni, hoja, hoja_sin_encabezados, libro, nomina_ejemplo

PREVIA = "/alumnos/importaciones/vista-previa"
CONFIRMAR = "/alumnos/importaciones"
ARCHIVO = "nomina.xlsx"
ANDINO, LEJANO = "Colegio Andino", "Colegio Lejano"
DESACTUALIZADA = "Los datos cambiaron desde la vista previa. Carga el archivo nuevamente."
ILEGIBLE = "No se pudo leer el archivo. Verifica que sea un archivo Excel (.xlsx) de hasta 5 MB."
HOY_MIERCOLES, LUNES = date(2026, 6, 17), date(2026, 6, 15)


@pytest.fixture
def sup(cabeceras):
    return cabeceras["Supervisor"]


def previa(client, cabecera, datos, nombre=ARCHIVO):
    return client.post(PREVIA, params={"nombre_archivo": nombre}, content=datos, headers=cabecera)


def confirmar(client, cabecera, datos, huella, id_importacion=None, nombre=ARCHIVO):
    params = {"id_importacion": str(id_importacion or uuid.uuid4()), "huella": huella, "nombre_archivo": nombre}
    return client.post(CONFIRMAR, params=params, content=datos, headers=cabecera)


def importar(client, cabecera, datos, id_importacion=None):
    huella = previa(client, cabecera, datos).json()["huella"]
    respuesta = confirmar(client, cabecera, datos, huella, id_importacion)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta


def libro_simple() -> bytes:
    return libro(hoja("A", fila(ANDINO, 2, "GOMEZ RIOS, Ada", 70000001), fila(ANDINO, None, "VARGAS, Beto")))


def libro_completo() -> bytes:
    """Un libro con todos los motivos. Ana debe tener el DNI 55555555 (`con_dni_de_ana`)."""
    return libro(
        hoja(
            "ANDINO",
            fila("COLEGIO ÁNDINO ", 2, "GOMEZ RIOS, Ada Luz", 70000001),
            fila(ANDINO, "3°", "VARGAS LUNA Beto Ciro", "7000 0002"),
            FILA_VACIA,
            fila(ANDINO, 1, None),
            fila(ANDINO, "7°", "NAVARRO, Elsa", "ABC"),
            fila(ANDINO, None, "todo minusculas sin coma"),
            fila("Colegio Inexistente", 1, "ROJAS, Ina", 70000003),
            fila(None, 1, "PAZ, Hugo", 70000004),
            fila(ANDINO, 2, "PÉREZ QUISPE, Luis", 70000005),
            fila(ANDINO, 2, "INACTIVO Iván"),
            fila(ANDINO, 2, "CASAS, Rosa", 44444444),
            fila(ANDINO, 2, "LUNA, Gina", 55555555),
            fila(ANDINO, 2, "SALAS, Dario", 1234567),
            fila(ANDINO, 2, "TORRES, Ana", 66666666),
        ),
        hoja(
            "LEJANO",
            fila_sin_dni(LEJANO, "4.º", "GOMEZ RIOS, Ada Luz"),
            fila_sin_dni(LEJANO, 9, "VEGA, Noa"),
            encabezados=SIN_DNI,
        ),
        hoja_sin_encabezados("NOTAS"),
        hoja("CINCO", fila("PARIAQAQA", 1, "RAMOS, Ivo", 44444444), filas_previas=4),
    )


@pytest.fixture
def con_dni_de_ana(db, entorno):
    ana = entorno.alumnos["ana"]
    ana.dni = "55555555"
    db.add(ana)
    db.commit()


def escrituras(db) -> tuple[int, int, int]:
    db.expire_all()
    return (
        len(db.exec(select(ImportacionAlumnos)).all()),
        len(db.exec(select(Alumno).where(Alumno.id_importacion.is_not(None))).all()),
        len(db.exec(select(Auditoria).where(Auditoria.accion == "importar")).all()),
    )


# ── 1. Vista previa ─────────────────────────────────────────────────────────────────

def test_vista_previa_con_todos_los_motivos(client, db, entorno, sup, con_dni_de_ana):
    respuesta = previa(client, sup, libro_completo())

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["listos"], cuerpo["con_advertencia"], cuerpo["no_importadas"]) == (8, 3, 7)
    assert len(cuerpo["huella"]) == 64
    assert [(o["hoja"], o["fila"], o["tipo"], o["motivo"]) for o in cuerpo["observaciones"]] == [
        ("ANDINO", 6, "advertencia", "grado_no_reconocido"),
        ("ANDINO", 6, "advertencia", "dni_no_valido"),
        ("ANDINO", 7, "advertencia", "nombre_sin_separar"),
        ("ANDINO", 8, "rechazo", "colegio_no_registrado"),
        ("ANDINO", 9, "rechazo", "colegio_no_registrado"),
        ("ANDINO", 10, "rechazo", "alumno_ya_registrado"),
        ("ANDINO", 11, "rechazo", "alumno_ya_registrado"),
        ("ANDINO", 12, "rechazo", "dni_repetido"),
        ("ANDINO", 13, "rechazo", "dni_repetido"),
        ("LEJANO", 3, "advertencia", "grado_no_reconocido"),
        ("NOTAS", None, "hoja_omitida", "hoja_omitida"),
        ("CINCO", 6, "rechazo", "colegio_no_registrado"),
    ]
    assert escrituras(db) == (0, 0, 0)


def test_vista_previa_usa_los_textos_del_cu(client, entorno, sup, con_dni_de_ana):
    detalles = {o["motivo"]: o["detalle"] for o in previa(client, sup, libro_completo()).json()["observaciones"]}

    assert detalles == {
        "colegio_no_registrado": "Colegio no registrado.",
        "dni_repetido": "DNI repetido.",
        "alumno_ya_registrado": "Alumno ya registrado en el colegio.",
        "grado_no_reconocido": "Grado no reconocido. Se importará sin grado.",
        "dni_no_valido": "DNI no válido. Se importará sin DNI.",
        "nombre_sin_separar": "Nombre sin separar. Revisa los nombres y apellidos después de importar.",
        "hoja_omitida": "Hoja omitida: no tiene las columnas II.EE y NOMBRES Y APELLIDOS.",
    }


def test_vista_previa_no_compara_con_colegios_inactivos(client, db, entorno, sup):
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()

    cuerpo = previa(client, sup, libro(hoja("A", fila(LEJANO, 1, "ROJAS, Ana")))).json()

    assert (cuerpo["listos"], cuerpo["observaciones"][0]["motivo"]) == (0, "colegio_no_registrado")


def test_vista_previa_grado_que_el_colegio_no_ofrece(client, db, entorno, sup):
    crear_colegio(db, "Solo Primero", [entorno.grados["1.º"]], [], entorno.supervisor.id_usuario)
    db.commit()

    cuerpo = previa(client, sup, libro(hoja("A", fila("Solo Primero", 2, "ROJAS, Ana")))).json()

    assert (cuerpo["listos"], cuerpo["observaciones"][0]["motivo"]) == (1, "grado_no_reconocido")


# ── 2. Confirmar ────────────────────────────────────────────────────────────────────

def test_confirmar_crea_alumnos_pendientes(client, db, entorno, sup, con_dni_de_ana):
    datos = libro_completo()
    id_importacion = uuid.uuid4()
    huella = previa(client, sup, datos).json()["huella"]

    respuesta = confirmar(client, sup, datos, huella, id_importacion)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["id_importacion"], cuerpo["nombre_archivo"], cuerpo["importados"]) == (str(id_importacion), ARCHIVO, 8)
    assert (cuerpo["no_importadas"], cuerpo["con_advertencia"]) == (7, 3)

    db.expire_all()
    alumnos = db.exec(select(Alumno).where(Alumno.id_importacion == id_importacion).order_by(Alumno.id_alumno)).all()
    assert len(alumnos) == 8
    ada = alumnos[0]
    assert (ada.apellidos, ada.nombres, ada.dni, ada.id_colegio) == (
        "GOMEZ RIOS", "Ada Luz", "70000001", entorno.colegios["Andino"].id_colegio
    )
    assert ada.id_grado == entorno.grados["2.º"]
    assert (ada.id_seccion, ada.id_programa_actual, ada.activo, ada.fecha_registro) == (None, None, True, entorno.hoy)
    assert ada.creado_por == entorno.supervisor.id_usuario
    assert {a.dni for a in alumnos} >= {"01234567", "66666666"}
    sin_separar = next(a for a in alumnos if a.apellidos == "todo minusculas sin coma")
    assert sin_separar.nombres == ""
    assert db.exec(select(AlumnoPertenencia).where(AlumnoPertenencia.id_alumno.in_([a.id_alumno for a in alumnos]))).all() == []


def test_confirmar_deja_la_importacion_y_una_auditoria_por_alumno(client, db, entorno, sup):
    id_importacion = uuid.uuid4()

    importar(client, sup, libro_simple(), id_importacion)

    db.expire_all()
    importacion = db.get(ImportacionAlumnos, id_importacion)
    assert (importacion.id_usuario, importacion.nombre_archivo, importacion.importados) == (entorno.supervisor.id_usuario, ARCHIVO, 2)
    filas = db.exec(select(Auditoria).where(Auditoria.accion == "importar")).all()
    assert len(filas) == 2
    assert {(f.tabla, f.campo, f.valor_anterior, f.valor_nuevo, f.id_actividad) for f in filas} == {
        ("alumno", "archivo", None, ARCHIVO, None)
    }


def test_historial_del_alumno_importado(client, db, entorno, sup):
    importar(client, sup, libro_simple())
    alumno = db.exec(select(Alumno).where(Alumno.dni == "70000001")).one()

    [evento] = client.get(f"/alumnos/{alumno.id_alumno}/historial", headers=sup).json()["items"]

    assert (evento["accion"], evento["usuario"], evento["rol"]) == ("importar", "Sara Supervisora", "Supervisor")
    assert evento["cambios"] == [{"campo": "Archivo", "anterior": None, "nuevo": ARCHIVO}]


@pytest.mark.parametrize(
    "enviado, guardado",
    [("nomina.xlsx", "nomina.xlsx"), (r"C:\Users\ana\Desktop\nomina final.xlsx", "nomina final.xlsx"), ("/tmp/x/  n.xlsx ", "n.xlsx")],
    ids=["simple", "windows", "unix"],
)
def test_se_guarda_solo_el_nombre_base(client, db, entorno, sup, enviado, guardado):
    datos = libro_simple()
    huella = previa(client, sup, datos, enviado).json()["huella"]

    respuesta = confirmar(client, sup, datos, huella, nombre=enviado)

    assert respuesta.json()["nombre_archivo"] == guardado


def test_nombres_vacio_se_corrige_editando(client, db, entorno, sup):
    importar(client, sup, libro(hoja("A", fila(ANDINO, None, "ROJAS MAMANI PAZ"))))
    alumno = db.exec(select(Alumno).where(Alumno.apellidos == "ROJAS MAMANI PAZ")).one()

    respuesta = client.patch(f"/alumnos/{alumno.id_alumno}", json={"apellidos": "ROJAS MAMANI", "nombres": "Paz"}, headers=sup)

    assert (respuesta.status_code, respuesta.json()["nombres"], respuesta.json()["pertenencia_pendiente"]) == (200, "Paz", True)


# ── 3. Después de importar ──────────────────────────────────────────────────────────

def test_los_importados_salen_como_pendientes(client, entorno, sup):
    importar(client, sup, libro_simple())

    items = client.get("/alumnos", params={"pertenencia": "pendiente"}, headers=sup).json()["items"]

    assert sorted(a["apellidos"] for a in items) == ["GOMEZ RIOS", "VARGAS"]
    assert {(a["seccion"], a["programa"], a["ciclo"]) for a in items} == {(None, None, None)}


def test_los_importados_no_salen_en_asistencias(reloj, client, db, entorno, cabeceras):
    reloj.fijar(a_las(HOY_MIERCOLES, 9))
    fila_grado = db.get(ColegioGrado, (entorno.colegios["Andino"].id_colegio, entorno.grados["2.º"]))
    fila_grado.dia_clase = 1
    db.add(fila_grado)
    db.commit()
    ana = entorno.alumnos["ana"]
    importar(client, cabeceras["Supervisor"], libro_simple())

    respuesta = client.get(
        "/asistencias",
        params={"id_colegio": ana.id_colegio, "id_grado": ana.id_grado, "id_seccion": ana.id_seccion, "fecha": LUNES.isoformat()},
        headers=cabeceras["Docente"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert "GOMEZ" not in respuesta.text
    assert "Torres" in respuesta.text


# ── 4. Datos que cambian ────────────────────────────────────────────────────────────

def test_huella_distinta(client, db, entorno, sup):
    respuesta = confirmar(client, sup, libro_simple(), "a" * 64)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (409, "importacion_desactualizada", DESACTUALIZADA)
    assert escrituras(db) == (0, 0, 0)


def test_colegio_inactivado_entre_vista_previa_y_confirmacion(client, db, entorno, sup):
    datos = libro(hoja("A", fila(ANDINO, 2, "ROJAS, Ana"), fila(LEJANO, 1, "PAZ, Hugo")))
    huella = previa(client, sup, datos).json()["huella"]
    lejano = entorno.colegios["Lejano"]
    lejano.activo = False
    db.add(lejano)
    db.commit()

    respuesta = confirmar(client, sup, datos, huella)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "importacion_desactualizada")
    assert escrituras(db) == (0, 0, 0)


def test_alumno_creado_con_el_mismo_dni_entre_vista_previa_y_confirmacion(client, db, entorno, sup):
    datos = libro_simple()
    huella = previa(client, sup, datos).json()["huella"]
    ana = entorno.alumnos["ana"]
    ana.dni = "70000001"
    db.add(ana)
    db.commit()

    respuesta = confirmar(client, sup, datos, huella)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "importacion_desactualizada")
    assert escrituras(db) == (0, 0, 0)


def test_grado_retirado_entre_vista_previa_y_confirmacion(client, db, entorno, sup):
    datos = libro(hoja("A", fila(ANDINO, 4, "ROJAS, Ana")))
    huella = previa(client, sup, datos).json()["huella"]
    db.delete(db.get(ColegioGrado, (entorno.colegios["Andino"].id_colegio, entorno.grados["4.º"])))
    db.commit()

    assert confirmar(client, sup, datos, huella).status_code == 409


def test_carrera_por_el_dni_responde_409_sin_escribir(client, db, entorno, sup, monkeypatch):
    datos = libro_simple()
    analisis = importacion_service._analizar(db, datos)
    ana = entorno.alumnos["ana"]
    ana.dni = "70000001"
    db.add(ana)
    db.commit()
    monkeypatch.setattr(importacion_service, "_analizar", lambda _db, _datos: analisis)

    respuesta = confirmar(client, sup, datos, analisis.huella)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "importacion_desactualizada")
    assert escrituras(db) == (0, 0, 0)


# ── 5. Mismo id e importación consultada ────────────────────────────────────────────

def test_el_mismo_id_no_importa_dos_veces(client, db, entorno, sup):
    datos = libro_simple()
    id_importacion = uuid.uuid4()
    primero = importar(client, sup, datos, id_importacion)

    segundo = confirmar(client, sup, datos, "b" * 64, id_importacion)

    assert (segundo.status_code, segundo.json()) == (200, primero.json())
    assert escrituras(db) == (1, 2, 2)


def test_el_reintento_no_lee_el_cuerpo(client, entorno, sup):
    id_importacion = uuid.uuid4()
    importar(client, sup, libro_simple(), id_importacion)

    respuesta = confirmar(client, sup, b"", "c" * 64, id_importacion)

    assert respuesta.status_code == 200


def test_el_id_de_otro_usuario(client, db, entorno, sup):
    id_importacion = uuid.uuid4()
    importar(client, sup, libro_simple(), id_importacion)
    original = db.exec(select(Usuario).where(Usuario.es_supervisor_original.is_(True))).one()

    respuesta = confirmar(client, cabecera_de(db, original), libro_simple(), "c" * 64, id_importacion)

    assert (respuesta.status_code, motivo(respuesta)) == (409, "importacion_existente")


def test_carrera_por_el_mismo_id_devuelve_el_resultado_guardado(client, db, entorno, sup, monkeypatch):
    datos = libro_simple()
    id_importacion = uuid.uuid4()
    analisis = importacion_service._analizar(db, datos)
    primero = importar(client, sup, datos, id_importacion)
    real = importacion_service.resultado_previo
    llamadas = []

    def sin_ver_la_primera(*args):
        llamadas.append(1)
        return None if len(llamadas) == 1 else real(*args)

    monkeypatch.setattr(importacion_service, "resultado_previo", sin_ver_la_primera)
    monkeypatch.setattr(importacion_service, "_analizar", lambda _db, _datos: analisis)

    segundo = confirmar(client, sup, datos, analisis.huella, id_importacion)

    assert (segundo.status_code, segundo.json()) == (200, primero.json())
    assert escrituras(db) == (1, 2, 2)


def test_otro_error_de_integridad_no_se_oculta(client, db, entorno, sup, monkeypatch):
    datos = libro_simple()
    huella = importacion_service._analizar(db, datos).huella

    def falla(*_args):
        raise IntegrityError("x", {}, Exception("otra restriccion"))

    monkeypatch.setattr(importacion_service, "_registrar", falla)

    with pytest.raises(IntegrityError):
        confirmar(client, sup, datos, huella)


def test_consultar_una_importacion(client, entorno, sup):
    id_importacion = uuid.uuid4()
    creada = importar(client, sup, libro_simple(), id_importacion).json()

    respuesta = client.get(f"{CONFIRMAR}/{id_importacion}", headers=sup)

    assert (respuesta.status_code, respuesta.json()) == (200, creada)


def test_consultar_una_importacion_inexistente(client, entorno, sup):
    respuesta = client.get(f"{CONFIRMAR}/{uuid.uuid4()}", headers=sup)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (
        404, "importacion_no_encontrada", "No se encontró la importación."
    )


# ── 6. Sin alumnos listos ───────────────────────────────────────────────────────────

def test_sin_alumnos_listos(client, db, entorno, sup):
    datos = libro(hoja("A", fila("No existe", 1, "ROJAS, Ana")))

    vista = previa(client, sup, datos)
    respuesta = confirmar(client, sup, datos, vista.json()["huella"])

    assert (vista.status_code, vista.json()["listos"], vista.json()["no_importadas"]) == (200, 0, 1)
    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (
        422, "sin_alumnos", "No hay alumnos para importar en este archivo."
    )
    assert escrituras(db) == (0, 0, 0)


# ── 7. Archivos inválidos ───────────────────────────────────────────────────────────

ARCHIVOS_INVALIDOS = {
    "vacio": b"",
    "html": b"<html><body>hola</body></html>",
    "demasiado-grande": b"x" * (TAMANO_MAXIMO + 1),
}


@pytest.mark.parametrize("datos", ARCHIVOS_INVALIDOS.values(), ids=ARCHIVOS_INVALIDOS.keys())
def test_archivo_invalido_en_vista_previa_y_confirmacion(client, db, entorno, sup, datos):
    for respuesta in (previa(client, sup, datos), confirmar(client, sup, datos, "a" * 64)):
        assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (422, "archivo_invalido", ILEGIBLE)
    assert escrituras(db) == (0, 0, 0)


def test_cuerpo_sin_content_length_que_supera_el_tope(client, entorno, sup):
    def trozos():
        yield b"x" * TAMANO_MAXIMO
        yield b"y"

    respuesta = client.post(PREVIA, params={"nombre_archivo": ARCHIVO}, content=trozos(), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "archivo_invalido")


def test_zip_que_no_es_xlsx(client, entorno, sup):
    salida = BytesIO()
    with zipfile.ZipFile(salida, "w") as archivo:
        archivo.writestr("hola.txt", "no soy un excel")

    respuesta = previa(client, sup, salida.getvalue())

    assert (respuesta.status_code, motivo(respuesta)) == (422, "archivo_invalido")


def test_mas_de_3000_filas(client, entorno, sup):
    datos = libro(hoja("A", *[fila(ANDINO, 1, f"ROJAS, Ana {i}") for i in range(MAXIMO_ALUMNOS + 1)]))

    respuesta = previa(client, sup, datos)

    assert (respuesta.status_code, motivo(respuesta), respuesta.json()["detail"]) == (
        422, "demasiados_alumnos",
        "El archivo supera el máximo de 3000 alumnos por importación. Divide la nómina e inténtalo nuevamente.",
    )


@pytest.mark.parametrize(
    "params, campo",
    [
        ({"huella": "a" * 64, "nombre_archivo": ARCHIVO}, "id_importacion"),
        ({"id_importacion": "no-uuid", "huella": "a" * 64, "nombre_archivo": ARCHIVO}, "id_importacion"),
        ({"id_importacion": str(uuid.uuid4()), "nombre_archivo": ARCHIVO}, "huella"),
        ({"id_importacion": str(uuid.uuid4()), "huella": "XYZ", "nombre_archivo": ARCHIVO}, "huella"),
        ({"id_importacion": str(uuid.uuid4()), "huella": "a" * 64}, "nombre_archivo"),
        ({"id_importacion": str(uuid.uuid4()), "huella": "a" * 64, "nombre_archivo": "/ruta/  "}, "nombre_archivo"),
        ({"id_importacion": str(uuid.uuid4()), "huella": "a" * 64, "nombre_archivo": "n" * 256}, "nombre_archivo"),
        ({"id_importacion": str(uuid.uuid4()), "huella": "a" * 64, "nombre_archivo": "a\tb.xlsx"}, "nombre_archivo"),
    ],
    ids=["falta-id", "id-invalido", "falta-huella", "huella-invalida", "falta-nombre", "nombre-vacio", "nombre-largo", "nombre-control"],
)
def test_parametros_invalidos(client, entorno, sup, params, campo):
    respuesta = client.post(CONFIRMAR, params=params, content=libro_simple(), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert [e["campo"] for e in respuesta.json()["errores"]] == [campo]


# ── 8. Permisos ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol", ["Docente", "Directivo"])
def test_solo_el_supervisor(client, entorno, cabeceras, rol):
    cabecera = cabeceras[rol]
    id_importacion = uuid.uuid4()
    respuestas = [
        previa(client, cabecera, libro_simple()),
        confirmar(client, cabecera, libro_simple(), "a" * 64, id_importacion),
        client.get(f"{CONFIRMAR}/{id_importacion}", headers=cabecera),
    ]

    assert [(r.status_code, motivo(r)) for r in respuestas] == [(403, "sin_permiso")] * 3


def test_sin_sesion(client, entorno):
    id_importacion = uuid.uuid4()
    respuestas = [previa(client, {}, b"x"), confirmar(client, {}, b"x", "a" * 64, id_importacion), client.get(f"{CONFIRMAR}/{id_importacion}")]

    assert [(r.status_code, motivo(r)) for r in respuestas] == [(401, "sesion_invalida")] * 3


# ── Protecciones HTTP: el cuerpo no pasa por ProteccionHttp, lo demás sí ─────────────

def test_sin_sesion_no_se_lee_el_cuerpo(client, entorno):
    lecturas, mensajes = [], []

    async def receive():
        lecturas.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensaje):
        mensajes.append(mensaje)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": PREVIA, "raw_path": PREVIA.encode(), "query_string": b"nombre_archivo=n.xlsx", "root_path": "",
        "headers": [], "client": ("testclient", 1), "server": ("testserver", 80),
    }

    asyncio.run(app(scope, receive, send))

    assert (mensajes[0]["status"], lecturas) == (401, [])


def test_un_nulo_en_la_query_se_sigue_rechazando(client, entorno, sup):
    respuesta = client.post(PREVIA, params={"nombre_archivo": "a\x00b.xlsx"}, content=libro_simple(), headers=sup)

    assert (respuesta.status_code, motivo(respuesta)) == (422, "validacion")
    assert respuesta.json()["errores"][0]["campo"] == "nombre_archivo"


def test_los_bytes_nulos_del_xlsx_no_se_rechazan(client, entorno, sup):
    datos = libro_simple()

    assert b"\x00" in datos
    assert previa(client, sup, datos).status_code == 200


@pytest.mark.parametrize("cuerpo", [libro_simple(), b"no soy un excel"], ids=["valido", "invalido"])
def test_las_respuestas_llevan_las_cabeceras_de_seguridad(client, entorno, sup, cuerpo):
    respuesta = previa(client, sup, cuerpo)

    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["cross-origin-resource-policy"] == "same-origin"


def test_cors_aplica_igual_que_en_las_demas_rutas(client, entorno, sup):
    origen = settings.cors_origins_list[0]

    permitido = client.post(
        PREVIA, params={"nombre_archivo": ARCHIVO}, content=b"x", headers={**sup, "Origin": origen}
    )
    ajeno = client.post(
        PREVIA, params={"nombre_archivo": ARCHIVO}, content=b"x", headers={**sup, "Origin": "https://ajeno.example"}
    )

    assert permitido.headers["access-control-allow-origin"] == origen
    assert "access-control-allow-origin" not in ajeno.headers


# ── 9. Rutas y documentación ────────────────────────────────────────────────────────

def test_las_rutas_no_chocan(client, entorno, sup):
    ana = entorno.alumnos["ana"].id_alumno

    assert client.get(f"/alumnos/{ana}", headers=sup).status_code == 200
    assert motivo(client.get("/alumnos/123", headers=sup)) == "alumno_no_encontrado"
    assert client.get(f"/alumnos/{ana}/resumen", headers=sup).status_code == 200
    invalido = client.get(f"{CONFIRMAR}/no-uuid", headers=sup)
    assert (invalido.status_code, invalido.json()["errores"][0]["campo"]) == (422, "id_importacion")


def test_openapi_documenta_el_cuerpo_binario(client):
    rutas = client.get("/openapi.json").json()["paths"]
    tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    for ruta in (PREVIA, CONFIRMAR):
        cuerpo = rutas[ruta]["post"]["requestBody"]
        assert cuerpo["content"][tipo]["schema"] == {"type": "string", "format": "binary"}


# ── Excel de ejemplo para los testers ───────────────────────────────────────────────

def test_nomina_de_ejemplo(client, db, entorno, sup):
    grados, programas = sorted(entorno.grados.values()), sorted(entorno.programas.values())
    crear_colegio(db, "I.E. E2E Playwright", grados, programas, entorno.supervisor.id_usuario)
    db.commit()

    cuerpo = previa(client, sup, nomina_ejemplo()).json()

    assert (cuerpo["listos"], cuerpo["con_advertencia"], cuerpo["no_importadas"]) == (8, 2, 4)
    assert [(o["hoja"], o["fila"], o["motivo"]) for o in cuerpo["observaciones"]] == [
        ("PLAYWRIGHT", 7, "grado_no_reconocido"),
        ("PLAYWRIGHT", 7, "dni_no_valido"),
        ("PLAYWRIGHT", 8, "nombre_sin_separar"),
        ("PLAYWRIGHT", 10, "dni_repetido"),
        ("OTRO COLEGIO", 2, "colegio_no_registrado"),
        ("OTRO COLEGIO", 3, "colegio_no_registrado"),
        ("REPETIDOS", 6, "dni_repetido"),
        ("RESUMEN", None, "hoja_omitida"),
    ]
