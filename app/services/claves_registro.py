"""Clave canónica de un registro de los módulos operativos (CU012, Salud del Sistema).

Identifica "un registro" con sus datos naturales, que el front conoce aunque esté sin
conexión y aunque la fila todavía no tenga id. La misma clave se calcula desde la fila
de la BD (sincronizados), desde una operación (aceptada, rechazada o descartada) y desde
un aviso de pendiente (`registro` de POST /sincronizacion/pendientes):

- Asistencia: `asistencias:{id_colegio}-{id_grado}-{id_seccion}-{fecha}:{id_alumno}`,
  con la fecha efectiva de la clase (AAAA-MM-DD).
- Fila de lectura: `lectura:{id_colegio}-{id_grado}-{id_seccion}-{id_programa}-{id_semana}:{id_alumno}`.
- Tabla de lectura (crear, cambiar clase, "No realizada" y su reversión):
  `lectura_tabla:{id_colegio}-{id_grado}-{id_seccion}-{id_programa}-{id_semana}`.
- Rúbrica: `rubrica:{id_alumno}-{id_anio_escolar}-{mes}` (una por alumno, año y mes).
- Registro de Vuelo: `registro_vuelo:{id_colegio}-{id_grado}-{id_seccion}-{id_programa}-{id_anio_escolar}-{id_periodo_academico}:{id_alumno}`
  (la UNIQUE de `registro_vuelo`).

Sin dependencias de la app (lo usan los schemas y los servicios).
"""
from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

ASISTENCIAS, LECTURA, RUBRICA, REGISTRO_VUELO = "asistencias", "lectura", "rubrica", "registro_vuelo"
LECTURA_TABLA = "lectura_tabla"


def _unir(*valores) -> str:
    return "-".join(str(v) for v in valores)


def asistencia(id_colegio: int, id_grado: int, id_seccion: int, fecha: date, id_alumno: int) -> str:
    return f"{ASISTENCIAS}:{_unir(id_colegio, id_grado, id_seccion, fecha.isoformat())}:{id_alumno}"


def tabla_lectura(id_colegio: int, id_grado: int, id_seccion: int, id_programa: int, id_semana: int) -> str:
    return f"{LECTURA_TABLA}:{_unir(id_colegio, id_grado, id_seccion, id_programa, id_semana)}"


def fila_lectura(
    id_colegio: int, id_grado: int, id_seccion: int, id_programa: int, id_semana: int, id_alumno: int
) -> str:
    return f"{LECTURA}:{_unir(id_colegio, id_grado, id_seccion, id_programa, id_semana)}:{id_alumno}"


def rubrica(id_alumno: int, id_anio_escolar: int, mes: int) -> str:
    return f"{RUBRICA}:{_unir(id_alumno, id_anio_escolar, mes)}"


def registro_vuelo(
    id_colegio: int, id_grado: int, id_seccion: int, id_programa: int, id_anio_escolar: int,
    id_periodo_academico: int, id_alumno: int,
) -> str:
    grupo = _unir(id_colegio, id_grado, id_seccion, id_programa, id_anio_escolar, id_periodo_academico)
    return f"{REGISTRO_VUELO}:{grupo}:{id_alumno}"


# ── Desde el `registro` de un aviso ─────────────────────────────────────────────────

class _Datos(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Grupo(_Datos):
    id_colegio: StrictInt
    id_grado: StrictInt
    id_seccion: StrictInt


class _Asistencia(_Grupo):
    fecha: date
    id_alumno: StrictInt


class _TablaLectura(_Grupo):
    id_programa: StrictInt
    id_semana: StrictInt


class _FilaLectura(_TablaLectura):
    id_alumno: StrictInt


class _Rubrica(_Datos):
    id_alumno: StrictInt
    id_anio_escolar: StrictInt
    mes: StrictInt = Field(ge=1, le=12)


class _RegistroVuelo(_Grupo):
    id_programa: StrictInt
    id_anio_escolar: StrictInt
    id_periodo_academico: StrictInt
    id_alumno: StrictInt


def _datos_de(modulo: str, registro: dict[str, Any]) -> _Datos:
    if modulo == ASISTENCIAS:
        return _Asistencia.model_validate(registro)
    if modulo == LECTURA:
        # Con alumno, una fila; sin él, la tabla ("No realizada", reversión, crear o cambiar clase).
        return (_FilaLectura if "id_alumno" in registro else _TablaLectura).model_validate(registro)
    if modulo == RUBRICA:
        return _Rubrica.model_validate(registro)
    if modulo == REGISTRO_VUELO:
        return _RegistroVuelo.model_validate(registro)
    raise ValueError(f"El módulo {modulo} no admite `registro`.")


def clave_de_aviso(modulo: str, registro: Optional[dict[str, Any]]) -> Optional[str]:
    """La clave del registro de un aviso (None sin `registro`). ValueError si faltan
    datos o no corresponden al módulo."""
    if registro is None:
        return None
    try:
        datos = _datos_de(modulo, registro)
    except ValidationError as error:
        faltan = ", ".join(".".join(str(p) for p in e["loc"]) for e in error.errors())
        raise ValueError(f"El registro no corresponde al módulo {modulo}: revisa {faltan}.") from error
    campos = datos.model_dump()
    constructores = {
        _Asistencia: asistencia, _TablaLectura: tabla_lectura, _FilaLectura: fila_lectura,
        _Rubrica: rubrica, _RegistroVuelo: registro_vuelo,
    }
    return constructores[type(datos)](**campos)
