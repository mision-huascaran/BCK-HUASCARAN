"""Lectura y análisis de la nómina de alumnos en Excel (CU014), sin tocar la base de datos.

`leer_nomina` abre el archivo con límites de seguridad y devuelve las filas con nombre;
`clasificar` decide qué pasa con cada una a partir de un `Contexto` que arma el servicio
(colegios activos, grados ofrecidos y alumnos existentes). El archivo no se conserva y de
él solo se usan las columnas II.EE, GRADO, NOMBRES Y APELLIDOS y DNI.
"""
import hashlib
import json
import re
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from io import BytesIO
from itertools import chain, islice
from typing import Any, Optional

from fastapi import status
from openpyxl import load_workbook

from app.core.errores import ErrorNegocio
from app.core.texto import nombre_comparable

TAMANO_MAXIMO = 5 * 1024 * 1024
MAXIMO_DESCOMPRIMIDO = 50 * 1024 * 1024
MAXIMO_ENTRADAS_ZIP = 2000
MAXIMO_ALUMNOS = 3000
FILAS_DE_ENCABEZADO = 20

# Encabezados aceptados, normalizados (`_encabezado`). Para admitir una variante, se agrega aquí.
COLUMNA_COLEGIO = "colegio"
COLUMNA_GRADO = "grado"
COLUMNA_NOMBRE = "nombre"
COLUMNA_DNI = "dni"
ENCABEZADOS = {
    "ii.ee": COLUMNA_COLEGIO,
    "grado": COLUMNA_GRADO,
    "nombresyapellidos": COLUMNA_NOMBRE,
    "dni": COLUMNA_DNI,
}
OBLIGATORIAS = {COLUMNA_COLEGIO, COLUMNA_NOMBRE}

GRADO_TEXTO = re.compile(r"([1-6])(°|º|\.°|\.º|o|ro|do|er|to)?\.?")
DNI_VALIDO = re.compile(r"\d{8}", re.ASCII)
GRADOS_VALIDOS = range(1, 7)
LARGO_DNI = 8

RECHAZO = "rechazo"
ADVERTENCIA = "advertencia"
HOJA_OMITIDA = "hoja_omitida"
COLEGIO_NO_REGISTRADO = "colegio_no_registrado"
DNI_REPETIDO = "dni_repetido"
ALUMNO_YA_REGISTRADO = "alumno_ya_registrado"
GRADO_NO_RECONOCIDO = "grado_no_reconocido"
DNI_NO_VALIDO = "dni_no_valido"
NOMBRE_SIN_SEPARAR = "nombre_sin_separar"

DETALLES = {
    COLEGIO_NO_REGISTRADO: "Colegio no registrado.",
    DNI_REPETIDO: "DNI repetido.",
    ALUMNO_YA_REGISTRADO: "Alumno ya registrado en el colegio.",
    GRADO_NO_RECONOCIDO: "Grado no reconocido. Se importará sin grado.",
    DNI_NO_VALIDO: "DNI no válido. Se importará sin DNI.",
    NOMBRE_SIN_SEPARAR: "Nombre sin separar. Revisa los nombres y apellidos después de importar.",
    HOJA_OMITIDA: "Hoja omitida: no tiene las columnas II.EE y NOMBRES Y APELLIDOS.",
}
MENSAJE_ARCHIVO_INVALIDO = "No se pudo leer el archivo. Verifica que sea un archivo Excel (.xlsx) de hasta 5 MB."
MENSAJE_DEMASIADOS = "El archivo supera el máximo de 3000 alumnos por importación. Divide la nómina e inténtalo nuevamente."


def archivo_invalido() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, MENSAJE_ARCHIVO_INVALIDO, motivo="archivo_invalido")


def _demasiados_alumnos() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, MENSAJE_DEMASIADOS, motivo="demasiados_alumnos")


# ── Datos de una celda ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Leido:
    """El valor de una celda, o None; `advertencia` si venía un valor que no se pudo usar."""

    valor: Any = None
    advertencia: bool = False


def _es_vacio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _entero(valor: Any) -> Optional[int]:
    """El entero de una celda numérica (int o float integral); None en cualquier otro caso."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return None


def leer_grado(valor: Any) -> Leido:
    """Número de grado del 1 al 6, en número o en texto (3, 3°, 3.º, 3ro, 3er). Vacío: sin
    grado ni advertencia. Cualquier otro valor: sin grado y con advertencia."""
    if _es_vacio(valor):
        return Leido()
    entero = _entero(valor)
    if entero is not None:
        return Leido(entero) if entero in GRADOS_VALIDOS else Leido(advertencia=True)
    coincide = GRADO_TEXTO.fullmatch("".join(str(valor).split()).lower()) if isinstance(valor, str) else None
    return Leido(int(coincide.group(1))) if coincide else Leido(advertencia=True)


def leer_dni(valor: Any) -> Leido:
    """DNI de 8 dígitos. En una celda numérica, 7 dígitos se completan con un cero a la
    izquierda (Excel lo pierde). Vacío: sin DNI ni advertencia. Otro valor: sin DNI y con
    advertencia."""
    if _es_vacio(valor):
        return Leido()
    entero = _entero(valor)
    if entero is not None:
        texto = str(entero)
        texto = texto.zfill(LARGO_DNI) if len(texto) == LARGO_DNI - 1 else texto
    else:
        texto = "".join(str(valor).split())
    return Leido(texto) if DNI_VALIDO.fullmatch(texto) else Leido(advertencia=True)


def _es_mayuscula(palabra: str) -> bool:
    return any(c.isalpha() for c in palabra) and palabra == palabra.upper()


def separar_nombre(texto: str) -> tuple[str, str, bool]:
    """(apellidos, nombres, se pudo separar). 'APELLIDOS, Nombres' por la primera coma;
    sin coma, las palabras iniciales en mayúsculas son los apellidos. Si no se puede,
    todo va en apellidos. Conserva las mayúsculas y minúsculas."""
    limpio = " ".join(texto.split())
    if "," in limpio:
        apellidos, _, nombres = limpio.partition(",")
        apellidos, nombres = apellidos.strip(), nombres.strip()
        return (apellidos, nombres, True) if apellidos and nombres else (limpio, "", False)
    palabras = limpio.split()
    iniciales = next((i for i, p in enumerate(palabras) if not _es_mayuscula(p)), len(palabras))
    if 0 < iniciales < len(palabras):
        return " ".join(palabras[:iniciales]), " ".join(palabras[iniciales:]), True
    return limpio, "", False


def clave_de_nombre(apellidos: str, nombres: str) -> tuple[str, ...]:
    """Las palabras del nombre sin tildes, mayúsculas, comas ni puntos, ordenadas: la misma
    persona aunque cambie el orden de nombres y apellidos."""
    sin_signos = f"{apellidos} {nombres}".replace(",", " ").replace(".", " ")
    return tuple(sorted(nombre_comparable(sin_signos).split()))


# ── Lectura del libro ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FilaNomina:
    orden_hoja: int
    hoja: str
    fila: int
    colegio: Any
    grado: Any
    nombre: str
    dni: Any


@dataclass
class Nomina:
    filas: list[FilaNomina] = field(default_factory=list)
    # (orden de la hoja, nombre).
    hojas_omitidas: list[tuple[int, str]] = field(default_factory=list)


def _encabezado(valor: Any) -> str:
    return "".join(nombre_comparable(str(valor)).split()) if valor is not None else ""


def columnas_de(fila: Iterable[Any]) -> dict[str, int]:
    """{columna: posición} de una fila; si un encabezado se repite, vale el primero."""
    columnas: dict[str, int] = {}
    for posicion, valor in enumerate(fila):
        columna = ENCABEZADOS.get(_encabezado(valor))
        if columna is not None:
            columnas.setdefault(columna, posicion)
    return columnas


def encontrar_encabezados(filas: Iterable[tuple]) -> Optional[tuple[int, dict[str, int]]]:
    """(número de fila, columnas) de la primera fila con II.EE y NOMBRES Y APELLIDOS dentro
    de las primeras 20; None si ninguna los tiene."""
    for numero, fila in enumerate(islice(filas, FILAS_DE_ENCABEZADO), start=1):
        columnas = columnas_de(fila)
        if OBLIGATORIAS <= columnas.keys():
            return numero, columnas
    return None


def _celda(fila: tuple, columnas: dict[str, int], columna: str) -> Any:
    posicion = columnas.get(columna)
    return fila[posicion] if posicion is not None and posicion < len(fila) else None


def _filas_con_nombre(
    filas: Iterator[tuple], columnas: dict[str, int], orden: int, hoja: str, inicio: int
) -> Iterator[FilaNomina]:
    for numero, fila in enumerate(filas, start=inicio):
        nombre = _celda(fila, columnas, COLUMNA_NOMBRE)
        if _es_vacio(nombre):
            continue
        yield FilaNomina(
            orden, hoja, numero, _celda(fila, columnas, COLUMNA_COLEGIO), _celda(fila, columnas, COLUMNA_GRADO),
            str(nombre).replace("\x00", ""), _celda(fila, columnas, COLUMNA_DNI),
        )


def _leer_hoja(hoja: Any, orden: int, nomina: Nomina) -> None:
    # Algunos programas declaran mal las dimensiones de la hoja: se lee lo que haya.
    hoja.reset_dimensions()
    filas = hoja.iter_rows(values_only=True)
    iniciales = list(islice(filas, FILAS_DE_ENCABEZADO))
    encontrado = encontrar_encabezados(iniciales)
    if encontrado is None:
        nomina.hojas_omitidas.append((orden, hoja.title))
        return
    numero, columnas = encontrado
    resto = chain(iniciales[numero:], filas)
    for fila in _filas_con_nombre(resto, columnas, orden, hoja.title, numero + 1):
        nomina.filas.append(fila)
        if len(nomina.filas) > MAXIMO_ALUMNOS:
            raise _demasiados_alumnos()


def _validar_zip(datos: bytes) -> None:
    """Un .xlsx es un zip: se revisan sus límites antes de descomprimirlo (zip bomb)."""
    if not zipfile.is_zipfile(BytesIO(datos)):
        raise archivo_invalido()
    with zipfile.ZipFile(BytesIO(datos)) as archivo:
        entradas = archivo.infolist()
    if len(entradas) > MAXIMO_ENTRADAS_ZIP or sum(e.file_size for e in entradas) > MAXIMO_DESCOMPRIMIDO:
        raise archivo_invalido()


def _leer_libro(datos: bytes) -> Nomina:
    nomina = Nomina()
    libro = load_workbook(BytesIO(datos), read_only=True, data_only=True, keep_links=False)
    try:
        for orden, hoja in enumerate(libro.worksheets):
            _leer_hoja(hoja, orden, nomina)
    finally:
        libro.close()
    return nomina


def leer_nomina(datos: bytes) -> Nomina:
    """Las filas con nombre de las hojas válidas. 422 `archivo_invalido` ante cualquier
    problema al abrirlo y 422 `demasiados_alumnos` si pasa de 3000 filas con nombre."""
    try:
        _validar_zip(datos)
        return _leer_libro(datos)
    except ErrorNegocio:
        raise
    except Exception as error:  # Un archivo dañado puede fallar de muchas formas: nunca un 500.
        raise archivo_invalido() from error


# ── Clasificación ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Contexto:
    # {nombre comparable: id} de los colegios activos.
    colegios: dict[str, int]
    # {id_colegio: ids de los grados que ofrece}.
    grados_por_colegio: dict[int, set[int]]
    # {1: id del grado "1.º", ...}.
    id_grado_por_numero: dict[int, int]
    # Los DNI del archivo que ya tiene algún alumno.
    dnis_existentes: set[str]
    # {(id_colegio, clave de nombre): DNI (o None) de cada alumno con ese nombre}.
    alumnos: dict[tuple[int, tuple[str, ...]], list[Optional[str]]]


@dataclass(frozen=True)
class AltaNomina:
    """Lo que se guardará de una fila importable."""

    id_colegio: int
    apellidos: str
    nombres: str
    id_grado: Optional[int]
    dni: Optional[str]


@dataclass(frozen=True)
class Observacion:
    orden_hoja: int
    hoja: str
    fila: Optional[int]
    tipo: str
    motivo: str

    @property
    def detalle(self) -> str:
        return DETALLES[self.motivo]


@dataclass
class Analisis:
    altas: list[AltaNomina]
    observaciones: list[Observacion]
    con_advertencia: int
    no_importadas: int
    huella: str

    @property
    def listos(self) -> int:
        return len(self.altas)


def dnis_de(nomina: Nomina) -> list[str]:
    """Los DNI válidos de las filas con nombre, uno por fila."""
    return [leido.valor for leido in (leer_dni(f.dni) for f in nomina.filas) if leido.valor is not None]


def colegio_de(fila: FilaNomina, colegios: dict[str, int]) -> Optional[int]:
    return colegios.get(nombre_comparable(str(fila.colegio))) if not _es_vacio(fila.colegio) else None


def _id_grado(leido: Leido, id_colegio: int, contexto: Contexto) -> tuple[Optional[int], bool]:
    """(id del grado o None, advertencia): un grado que el colegio no ofrece no se reconoce."""
    if leido.valor is None:
        return None, leido.advertencia
    id_grado = contexto.id_grado_por_numero.get(leido.valor)
    if id_grado is None or id_grado not in contexto.grados_por_colegio.get(id_colegio, set()):
        return None, True
    return id_grado, False


def _ya_registrado(alta_dni: Optional[str], clave: tuple[str, ...], id_colegio: int, contexto: Contexto) -> bool:
    """Mismo nombre en el colegio y a alguno de los dos le falta el DNI."""
    existentes = contexto.alumnos.get((id_colegio, clave), [])
    return any(alta_dni is None or dni is None for dni in existentes)


def _motivo_de_rechazo(
    id_colegio: Optional[int], dni: Optional[str], apellidos: str, nombres: str, contexto: Contexto, repetidos: Counter
) -> Optional[str]:
    """El primer rechazo, en el orden de las reglas."""
    if id_colegio is None:
        return COLEGIO_NO_REGISTRADO
    if dni is not None and (dni in contexto.dnis_existentes or repetidos[dni] > 1):
        return DNI_REPETIDO
    if _ya_registrado(dni, clave_de_nombre(apellidos, nombres), id_colegio, contexto):
        return ALUMNO_YA_REGISTRADO
    return None


def _firma(fila: FilaNomina, importa: bool, motivos: list[str], alta: Optional[AltaNomina]) -> dict[str, Any]:
    return {
        "hoja": fila.hoja, "fila": fila.fila, "importa": importa, "motivos": motivos,
        "datos": asdict(alta) if alta is not None else None,
    }


def _huella(firmas: list[dict[str, Any]], omitidas: list[tuple[int, str]]) -> str:
    canonico = json.dumps(
        {"filas": firmas, "hojas_omitidas": [nombre for _, nombre in omitidas]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _clasificar_fila(
    fila: FilaNomina, contexto: Contexto, repetidos: Counter
) -> tuple[Optional[AltaNomina], list[Observacion], dict[str, Any]]:
    id_colegio = colegio_de(fila, contexto.colegios)
    dni = leer_dni(fila.dni)
    apellidos, nombres, separado = separar_nombre(fila.nombre)

    def observacion(tipo: str, motivo: str) -> Observacion:
        return Observacion(fila.orden_hoja, fila.hoja, fila.fila, tipo, motivo)

    rechazo = _motivo_de_rechazo(id_colegio, dni.valor, apellidos, nombres, contexto, repetidos)
    if rechazo is not None:
        return None, [observacion(RECHAZO, rechazo)], _firma(fila, False, [rechazo], None)

    id_grado, grado_invalido = _id_grado(leer_grado(fila.grado), id_colegio, contexto)
    motivos = [
        motivo
        for motivo, aplica in (
            (GRADO_NO_RECONOCIDO, grado_invalido), (DNI_NO_VALIDO, dni.advertencia), (NOMBRE_SIN_SEPARAR, not separado),
        )
        if aplica
    ]
    alta = AltaNomina(id_colegio, apellidos, nombres, id_grado, dni.valor)
    return alta, [observacion(ADVERTENCIA, m) for m in motivos], _firma(fila, True, motivos, alta)


def clasificar(nomina: Nomina, contexto: Contexto) -> Analisis:
    """Aplica las reglas a cada fila, en orden: colegio, DNI repetido y alumno ya
    registrado. El primer rechazo decide; una fila importable puede tener varias
    advertencias, pero cuenta una sola vez."""
    repetidos = Counter(dnis_de(nomina))
    altas, observaciones, firmas = [], [], []
    no_importadas = con_advertencia = 0
    for fila in nomina.filas:
        alta, propias, firma = _clasificar_fila(fila, contexto, repetidos)
        observaciones.extend(propias)
        firmas.append(firma)
        if alta is None:
            no_importadas += 1
            continue
        altas.append(alta)
        con_advertencia += bool(propias)
    observaciones.extend(Observacion(orden, nombre, None, HOJA_OMITIDA, HOJA_OMITIDA) for orden, nombre in nomina.hojas_omitidas)
    # El sort es estable: las de una misma fila conservan el orden de las reglas.
    observaciones.sort(key=lambda o: (o.orden_hoja, o.fila or 0))
    return Analisis(altas, observaciones, con_advertencia, no_importadas, _huella(firmas, nomina.hojas_omitidas))
