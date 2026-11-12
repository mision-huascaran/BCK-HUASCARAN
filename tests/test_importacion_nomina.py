"""Importación de la nómina (CU014): lectura y análisis, sin base de datos."""
import asyncio
import zipfile
from io import BytesIO

import pytest
from openpyxl.xml import DEFUSEDXML

from app.core.errores import ErrorNegocio
from app.core.proteccion_http import ProteccionHttp
from app.services.importacion_nomina import (
    Contexto,
    Leido,
    MAXIMO_ALUMNOS,
    MAXIMO_DESCOMPRIMIDO,
    MAXIMO_ENTRADAS_ZIP,
    _filas_con_nombre,
    clasificar,
    clave_de_nombre,
    encontrar_encabezados,
    leer_dni,
    leer_grado,
    leer_nomina,
    separar_nombre,
)
from tests.nomina_factory import ENCABEZADOS, FILA_VACIA, SIN_DNI, fila, fila_sin_dni, hoja, hoja_sin_encabezados, libro

COLEGIO = "Colegio Prueba"
ID_COLEGIO = 7
IDS_GRADO = {n: 100 + n for n in range(1, 7)}


def contexto(**cambios) -> Contexto:
    datos = {
        "colegios": {"colegio prueba": ID_COLEGIO},
        "grados_por_colegio": {ID_COLEGIO: {IDS_GRADO[1], IDS_GRADO[2]}},
        "id_grado_por_numero": IDS_GRADO,
        "dnis_existentes": set(),
        "alumnos": {},
        **cambios,
    }
    return Contexto(**datos)


def motivo_de(error: ErrorNegocio) -> str:
    return error.motivo


# ── Nombre ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("QUISPE MAMANI, Juan Carlos", ("QUISPE MAMANI", "Juan Carlos", True)),
        ("  DIAS  SHISHCO,  Lian   Ulises  ", ("DIAS SHISHCO", "Lian Ulises", True)),
        ("PEREZ, Ana, María", ("PEREZ", "Ana, María", True)),
        ("PARIACHI PEÑA Emily Kiara", ("PARIACHI PEÑA", "Emily Kiara", True)),
        ("ÑAHUI  ROJAS Ana", ("ÑAHUI ROJAS", "Ana", True)),
        ("TODO EN MAYUSCULAS", ("TODO EN MAYUSCULAS", "", False)),
        ("todo en minusculas", ("todo en minusculas", "", False)),
        ("Ana QUISPE", ("Ana QUISPE", "", False)),
        ("SOLO, ", ("SOLO,", "", False)),
        (", Ana", (", Ana", "", False)),
    ],
    ids=["coma", "espacios", "dos-comas", "mayusculas", "enie", "todo-mayus", "todo-minus", "nombre-primero", "coma-sin-nombre", "coma-sin-apellido"],
)
def test_separar_nombre(texto, esperado):
    assert separar_nombre(texto) == esperado


def test_separar_nombre_no_toma_un_numero_como_mayuscula():
    assert separar_nombre("123 ROJAS Ana") == ("123 ROJAS Ana", "", False)


# ── Grado ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "valor, numero",
    [(3, 3), (1, 1), (6, 6), (3.0, 3), ("3", 3), ("3°", 3), ("3º", 3), ("3.º", 3), ("3.°", 3), (" 3 ° ", 3), ("3ro", 3),
     ("3ER", 3), ("3o", 3), ("2do", 2), ("4to", 4), ("3.", 3)],
)
def test_leer_grado_valido(valor, numero):
    assert leer_grado(valor) == Leido(numero)


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_leer_grado_vacio_no_advierte(valor):
    assert leer_grado(valor) == Leido()


@pytest.mark.parametrize("valor", ["7°", 7, 0, 3.5, True, "tercero", "3 B", "33"])
def test_leer_grado_no_reconocido(valor):
    assert leer_grado(valor) == Leido(advertencia=True)


# ── DNI ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "valor, dni",
    [(12345678, "12345678"), (1234567, "01234567"), (12345678.0, "12345678"), ("12345678", "12345678"),
     (" 1234 5678 ", "12345678"), ("01234567", "01234567")],
    ids=["numero-8", "numero-7", "float", "texto", "texto-espacios", "texto-cero"],
)
def test_leer_dni_valido(valor, dni):
    assert leer_dni(valor) == Leido(dni)


@pytest.mark.parametrize("valor", [None, "", "  "])
def test_leer_dni_vacio_no_advierte(valor):
    assert leer_dni(valor) == Leido()


@pytest.mark.parametrize("valor", [123456789, 123456, "1234567", "123456789", "1234567a", 12.5, True, -1234567])
def test_leer_dni_no_valido(valor):
    assert leer_dni(valor) == Leido(advertencia=True)


# ── Clave de nombre ─────────────────────────────────────────────────────────────────

def test_clave_ignora_el_orden_las_tildes_y_los_signos():
    assert clave_de_nombre("QUISPE MAMANÍ,", "Juan") == clave_de_nombre("Juan", "Quispe Mamani")
    assert clave_de_nombre("Núñez", "J. Ana") == clave_de_nombre("NUNEZ ANA", "J")


def test_clave_distingue_personas_distintas():
    assert clave_de_nombre("Quispe", "Juan") != clave_de_nombre("Quispe", "Juana")
    assert clave_de_nombre("Quispe Quispe", "Juan") != clave_de_nombre("Quispe", "Juan")


# ── Encabezados ─────────────────────────────────────────────────────────────────────

def test_encabezados_en_la_fila_1_con_espacios_y_columnas_extra():
    numero, columnas = encontrar_encabezados([tuple(ENCABEZADOS)])

    assert numero == 1
    assert columnas == {"colegio": 1, "grado": 2, "nombre": 3, "dni": 4}


def test_encabezados_en_la_fila_5():
    filas = [(), (None, "Nómina 2026"), (), (), tuple(SIN_DNI)]

    numero, columnas = encontrar_encabezados(filas)

    assert numero == 5
    assert "dni" not in columnas


def test_encabezados_sin_importar_tildes_ni_posicion():
    numero, columnas = encontrar_encabezados([("DNÍ", "NOMBRES   Y APELLÍDOS", "ii . ee")])

    assert (numero, columnas) == (1, {"dni": 0, "nombre": 1, "colegio": 2})


def test_encabezado_repetido_usa_la_primera_columna():
    _, columnas = encontrar_encabezados([("II.EE", "NOMBRES Y APELLIDOS", "II.EE", "DNI", "dni")])

    assert (columnas["colegio"], columnas["dni"]) == (0, 3)


@pytest.mark.parametrize(
    "filas",
    [[("II.EE", "GRADO")], [("NOMBRES Y APELLIDOS", "DNI")], [], [()] * 20 + [("II.EE", "NOMBRES Y APELLIDOS")]],
    ids=["sin-nombre", "sin-colegio", "vacia", "pasada-la-fila-20"],
)
def test_sin_encabezados(filas):
    assert encontrar_encabezados(filas) is None


def test_encabezados_en_la_fila_20_todavia_cuentan():
    numero, _ = encontrar_encabezados([()] * 19 + [("II.EE", "NOMBRES Y APELLIDOS")])

    assert numero == 20


# ── Lectura del libro ───────────────────────────────────────────────────────────────

def test_lee_las_filas_con_nombre_y_su_numero_de_excel():
    nomina = leer_nomina(libro(
        hoja("A", fila(COLEGIO, 1, "ROJAS, Ana", 11111111), FILA_VACIA, fila(COLEGIO, 2, None), fila(COLEGIO, "3°", "  ", 2),
             fila(COLEGIO, 2, "PAZ, Hugo"), filas_previas=2),
    ))

    assert [(f.hoja, f.fila, f.nombre, f.dni) for f in nomina.filas] == [
        ("A", 4, "ROJAS, Ana", 11111111), ("A", 8, "PAZ, Hugo", None),
    ]


def test_un_nulo_en_el_nombre_no_llega_a_la_base():
    # ProteccionHttp no revisa este cuerpo y PostgreSQL no admite NUL en textos.
    columnas = {"colegio": 0, "nombre": 1}

    [fila_leida] = _filas_con_nombre(iter([(COLEGIO, "ROJAS,\x00 Ana")]), columnas, 0, "A", 2)

    assert fila_leida.nombre == "ROJAS, Ana"


def test_las_hojas_se_leen_en_su_orden_y_las_omitidas_se_anotan():
    nomina = leer_nomina(libro(
        hoja("B", fila(COLEGIO, 1, "ROJAS, Ana")), hoja_sin_encabezados("X"),
        hoja("A", fila_sin_dni(COLEGIO, 1, "PAZ, Hugo"), encabezados=SIN_DNI),
    ))

    assert [(f.orden_hoja, f.hoja, f.dni) for f in nomina.filas] == [(0, "B", None), (2, "A", None)]
    assert nomina.hojas_omitidas == [(1, "X")]


def test_cuenta_el_limite_solo_con_filas_con_nombre():
    filas = [fila(COLEGIO, 1, f"ROJAS, Ana {i}") for i in range(MAXIMO_ALUMNOS)]

    nomina = leer_nomina(libro(hoja("A", *filas, FILA_VACIA, fila(COLEGIO, 1, None))))

    assert len(nomina.filas) == MAXIMO_ALUMNOS


def test_mas_de_3000_filas_con_nombre():
    filas = [fila(COLEGIO, 1, f"ROJAS, Ana {i}") for i in range(MAXIMO_ALUMNOS + 1)]
    datos = libro(hoja("A", *filas))

    with pytest.raises(ErrorNegocio) as error:
        leer_nomina(datos)

    assert motivo_de(error.value) == "demasiados_alumnos"


def _zip(entradas: dict[str, bytes]) -> bytes:
    salida = BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as archivo:
        for nombre, contenido in entradas.items():
            archivo.writestr(nombre, contenido)
    return salida.getvalue()


def _con_entidad_externa(datos: bytes) -> bytes:
    """El .xlsx con un DTD dentro de su hoja (ataque de entidades XML)."""
    salida = BytesIO()
    with zipfile.ZipFile(BytesIO(datos)) as origen, zipfile.ZipFile(salida, "w") as destino:
        for entrada in origen.infolist():
            contenido = origen.read(entrada.filename)
            if entrada.filename.startswith("xl/worksheets/sheet"):
                contenido = contenido.replace(b"<worksheet", b'<!DOCTYPE x [<!ENTITY e "a">]><worksheet', 1)
            destino.writestr(entrada, contenido)
    return salida.getvalue()


@pytest.mark.parametrize(
    "datos",
    [
        b"",
        b"<html><body>no soy un excel</body></html>",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 viejo .xls",
        _zip({"hola.txt": b"no es un xlsx"}),
        _zip({"grande.bin": b"\0" * (MAXIMO_DESCOMPRIMIDO + 1)}),
        _zip({f"{i}.txt": b"x" for i in range(MAXIMO_ENTRADAS_ZIP + 1)}),
        libro(hoja("A", fila(COLEGIO, 1, "ROJAS, Ana")))[:200],
    ],
    ids=["vacio", "html", "xls", "zip-cualquiera", "zip-bomb", "demasiadas-entradas", "truncado"],
)
def test_archivo_ilegible(datos):
    with pytest.raises(ErrorNegocio) as error:
        leer_nomina(datos)

    assert motivo_de(error.value) == "archivo_invalido"


def test_xml_con_entidades_se_rechaza():
    assert DEFUSEDXML is True
    datos = _con_entidad_externa(libro(hoja("A", fila(COLEGIO, 1, "ROJAS, Ana"))))

    with pytest.raises(ErrorNegocio) as error:
        leer_nomina(datos)

    assert motivo_de(error.value) == "archivo_invalido"


# ── Clasificación ───────────────────────────────────────────────────────────────────

def clasificar_filas(*filas, ctx=None):
    return clasificar(leer_nomina(libro(hoja("A", *filas))), ctx or contexto())


def motivos(analisis) -> list[tuple]:
    return [(o.fila, o.tipo, o.motivo) for o in analisis.observaciones]


def test_alta_completa():
    analisis = clasificar_filas(fila("COLEGIO PRÚEBA ", "2°", "ROJAS, Ana", 12345678))

    assert motivos(analisis) == []
    assert (analisis.listos, analisis.con_advertencia, analisis.no_importadas) == (1, 0, 0)
    alta = analisis.altas[0]
    assert (alta.id_colegio, alta.apellidos, alta.nombres, alta.id_grado, alta.dni) == (
        ID_COLEGIO, "ROJAS", "Ana", IDS_GRADO[2], "12345678",
    )


def test_grado_que_el_colegio_no_ofrece_no_se_reconoce():
    analisis = clasificar_filas(fila(COLEGIO, 5, "ROJAS, Ana"))

    assert motivos(analisis) == [(2, "advertencia", "grado_no_reconocido")]
    assert analisis.altas[0].id_grado is None


def test_grado_vacio_y_dni_vacio_no_advierten():
    analisis = clasificar_filas(fila(COLEGIO, None, "ROJAS, Ana"))

    assert (analisis.listos, motivos(analisis)) == (1, [])


def test_varias_advertencias_cuentan_una_fila():
    analisis = clasificar_filas(fila(COLEGIO, "9°", "SIN SEPARAR", "abc"))

    assert motivos(analisis) == [
        (2, "advertencia", "grado_no_reconocido"), (2, "advertencia", "dni_no_valido"), (2, "advertencia", "nombre_sin_separar"),
    ]
    assert (analisis.listos, analisis.con_advertencia) == (1, 1)
    assert (analisis.altas[0].apellidos, analisis.altas[0].nombres, analisis.altas[0].dni) == ("SIN SEPARAR", "", None)


def test_rechazo_no_muestra_advertencias_y_el_primero_decide():
    analisis = clasificar_filas(fila("Otro", "9°", "SIN SEPARAR", 11111111), fila("Otro", 1, "PAZ, Hugo", 11111111))

    assert motivos(analisis) == [(2, "rechazo", "colegio_no_registrado"), (3, "rechazo", "colegio_no_registrado")]


def test_dni_repetido_cuenta_filas_de_colegio_invalido():
    analisis = clasificar_filas(fila(COLEGIO, 1, "ROJAS, Ana", 11111111), fila("Otro", 1, "PAZ, Hugo", 11111111))

    assert motivos(analisis)[0] == (2, "rechazo", "dni_repetido")


def test_dni_existente_en_el_sistema():
    analisis = clasificar_filas(fila(COLEGIO, 1, "ROJAS, Ana", 11111111), ctx=contexto(dnis_existentes={"11111111"}))

    assert motivos(analisis) == [(2, "rechazo", "dni_repetido")]


CLAVE_ANA = clave_de_nombre("Rojas", "Ana")


@pytest.mark.parametrize(
    "dni_fila, dni_existente, rechaza",
    [(None, None, True), (11111111, None, True), (None, "22222222", True), ("abc", "22222222", True), (11111111, "22222222", False)],
    ids=["ninguno", "falta-existente", "falta-fila", "fila-invalido", "ambos-distintos"],
)
def test_alumno_ya_registrado(dni_fila, dni_existente, rechaza):
    analisis = clasificar_filas(
        fila(COLEGIO, 1, "ROJAS, Ana", dni_fila), ctx=contexto(alumnos={(ID_COLEGIO, CLAVE_ANA): [dni_existente]})
    )

    assert (analisis.no_importadas == 1) is rechaza


def test_las_filas_del_archivo_no_se_comparan_por_nombre():
    analisis = clasificar_filas(fila(COLEGIO, 1, "ROJAS, Ana"), fila(COLEGIO, 2, "Ana ROJAS"), fila(COLEGIO, 2, "ROJAS Ana"))

    assert analisis.listos == 3


def test_otro_colegio_no_cuenta_como_ya_registrado():
    analisis = clasificar_filas(fila(COLEGIO, 1, "ROJAS, Ana"), ctx=contexto(alumnos={(99, CLAVE_ANA): [None]}))

    assert analisis.listos == 1


def test_la_huella_depende_del_resultado():
    nomina = leer_nomina(libro(hoja("A", fila(COLEGIO, 1, "ROJAS, Ana", 11111111))))

    base = clasificar(nomina, contexto()).huella

    assert base == clasificar(nomina, contexto()).huella
    assert len(base) == 64
    assert base != clasificar(nomina, contexto(dnis_existentes={"11111111"})).huella
    assert base != clasificar(nomina, contexto(colegios={})).huella
    assert base != clasificar(nomina, contexto(grados_por_colegio={})).huella


# ── Middleware ──────────────────────────────────────────────────────────────────────

def _llamar_middleware(ruta: str, metodo: str = "POST") -> tuple[int, bool]:
    """(veces que se leyó el cuerpo, si llegó a la aplicación)."""
    lecturas, llego = [], []

    async def app(_scope, _receive, _send):
        llego.append(True)

    async def receive():
        lecturas.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_mensaje):
        return None

    scope = {"type": "http", "method": metodo, "path": ruta, "query_string": b""}
    asyncio.run(ProteccionHttp(app)(scope, receive, send))
    return len(lecturas), bool(llego)


@pytest.mark.parametrize("ruta", ["/alumnos/importaciones", "/alumnos/importaciones/vista-previa", "/api/alumnos/importaciones"])
def test_el_middleware_no_lee_el_cuerpo_de_las_importaciones(ruta):
    assert _llamar_middleware(ruta) == (0, True)


def test_el_middleware_sigue_leyendo_los_demas_cuerpos():
    assert _llamar_middleware("/alumnos") == (1, True)
