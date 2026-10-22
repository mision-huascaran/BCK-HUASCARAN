"""Registros tocados en una actividad, por módulo (atajos de CU021: "Ver Asistencias",
"Ver Alumnos"...). Solo el Supervisor.

Se leen de `auditoria.id_actividad` (no por fecha): cada módulo agrupa sus filas de
auditoría por la entidad que se abre en solo lectura y la une con su contexto (colegio,
grado, sección y, según el módulo, subprograma, alumno, semana, mes o PA). Una consulta
paginada por módulo, sin N+1.
"""
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import status
from sqlalchemy import Integer, String, cast, func, literal, union
from sqlmodel import Session, select

from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.models.evaluacion import (
    Asistencia,
    Clase,
    LecturaSemanal,
    LecturaSemanalAlumno,
    RegistroVuelo,
    RubricaMensual,
    SemanaReporte,
)
from app.models.organizacion import Alumno, Colegio, Grado, Programa, Seccion
from app.models.trazabilidad import Actividad, Auditoria
from app.schemas.comun import ItemCatalogo
from app.schemas.seguimiento import ModuloRegistros, RegistroDeActividad
from app.services.seguimiento_service import usuario_del_docente

MENSAJE_ACTIVIDAD_NO_ENCONTRADA = "La actividad no existe o no es de ese docente."


def _de_la_actividad(id_actividad: uuid.UUID, tabla: str) -> tuple:
    return Auditoria.id_actividad == id_actividad, Auditoria.tabla == tabla


def _por_registro(id_actividad: uuid.UUID, tabla: str):
    """(id, cambios): cada registro de la tabla y cuántas veces se guardó en la actividad."""
    return (
        select(
            cast(Auditoria.id_registro, Integer).label("id"),
            func.count(func.distinct(Auditoria.fecha)).label("cambios"),
        )
        .where(*_de_la_actividad(id_actividad, tabla))
        .group_by(Auditoria.id_registro)
        .subquery()
    )


def _asistencias_por_clase(id_actividad: uuid.UUID):
    """(id de la clase, alumnos de la clase con cambios)."""
    return (
        select(Asistencia.id_clase.label("id"), func.count(func.distinct(Auditoria.id_registro)).label("cambios"))
        .join(Asistencia, cast(Asistencia.id, String) == Auditoria.id_registro)
        .where(*_de_la_actividad(id_actividad, "asistencia"))
        .group_by(Asistencia.id_clase)
        .subquery()
    )


def _lectura_por_tabla(id_actividad: uuid.UUID):
    """(id de la tabla, registros distintos tocados: la tabla y sus filas)."""
    tabla = select(
        cast(Auditoria.id_registro, Integer).label("id"), Auditoria.tabla, Auditoria.id_registro
    ).where(*_de_la_actividad(id_actividad, "lectura_semanal"))
    filas = (
        select(LecturaSemanalAlumno.id_lectura_semanal.label("id"), Auditoria.tabla, Auditoria.id_registro)
        .join(LecturaSemanalAlumno, cast(LecturaSemanalAlumno.id, String) == Auditoria.id_registro)
        .where(*_de_la_actividad(id_actividad, "lectura_semanal_alumno"))
    )
    tocados = union(tabla, filas).subquery()
    return select(tocados.c.id, func.count().label("cambios")).group_by(tocados.c.id).subquery()


@dataclass(frozen=True)
class _Modulo:
    """Cómo se arma la lista de un módulo: qué se tocó y qué entidad lo contiene."""

    tocados: Callable[[uuid.UUID], Any]
    entidad: Any
    clave: Any
    extras: Callable[[Any], list]
    joins: Callable[[Any], Any] = staticmethod(lambda consulta: consulta)


def _con_programa_y_alumno(entidad) -> Callable[[Any], Any]:
    return lambda consulta: consulta.join(Programa, Programa.id_programa == entidad.id_programa).join(
        Alumno, Alumno.id_alumno == entidad.id_alumno
    )


def _alumno_y_programa(entidad) -> list:
    return [
        entidad.id_programa.label("id_programa"), Programa.nombre.label("programa"),
        entidad.id_alumno.label("id_alumno"), Alumno.nombres.label("nombres"), Alumno.apellidos.label("apellidos"),
    ]


MODULOS: dict[str, _Modulo] = {
    "asistencias": _Modulo(_asistencias_por_clase, Clase, Clase.id_clase, lambda _: [Clase.fecha.label("fecha")]),
    "lectura": _Modulo(
        _lectura_por_tabla, LecturaSemanal, LecturaSemanal.id,
        lambda _: [
            LecturaSemanal.id_programa.label("id_programa"), Programa.nombre.label("programa"),
            LecturaSemanal.id_semana.label("id_semana"), SemanaReporte.fecha_inicio.label("semana_inicio"),
            LecturaSemanal.mes.label("mes"), LecturaSemanal.id_anio_escolar.label("id_anio_escolar"),
        ],
        lambda consulta: consulta.join(Programa, Programa.id_programa == LecturaSemanal.id_programa).join(
            SemanaReporte, SemanaReporte.id_semana == LecturaSemanal.id_semana
        ),
    ),
    "rubrica": _Modulo(
        lambda a: _por_registro(a, "rubrica_mensual"), RubricaMensual, RubricaMensual.id,
        lambda _: [*_alumno_y_programa(RubricaMensual), RubricaMensual.mes.label("mes"),
                   RubricaMensual.id_anio_escolar.label("id_anio_escolar")],
        _con_programa_y_alumno(RubricaMensual),
    ),
    "registro_vuelo": _Modulo(
        lambda a: _por_registro(a, "registro_vuelo"), RegistroVuelo, RegistroVuelo.id,
        lambda _: [*_alumno_y_programa(RegistroVuelo), RegistroVuelo.id_anio_escolar.label("id_anio_escolar"),
                   RegistroVuelo.id_periodo_academico.label("id_periodo_academico")],
        _con_programa_y_alumno(RegistroVuelo),
    ),
    "alumnos": _Modulo(
        lambda a: _por_registro(a, "alumno"), Alumno, Alumno.id_alumno,
        lambda _: [Alumno.id_alumno.label("id_alumno"), Alumno.nombres.label("nombres"),
                   Alumno.apellidos.label("apellidos"), literal(None).label("id_programa")],
    ),
}


def _consulta(modulo: _Modulo, id_actividad: uuid.UUID):
    tocados = modulo.tocados(id_actividad)
    entidad = modulo.entidad
    consulta = (
        select(
            tocados.c.id, tocados.c.cambios,
            Colegio.id_colegio, Colegio.nombre.label("colegio"), Grado.id_grado, Grado.nombre.label("grado"),
            Seccion.id_seccion, Seccion.nombre.label("seccion"), *modulo.extras(entidad),
        )
        .select_from(tocados)
        .join(entidad, modulo.clave == tocados.c.id)
        .join(Colegio, Colegio.id_colegio == entidad.id_colegio)
        .join(Grado, Grado.id_grado == entidad.id_grado)
        .join(Seccion, Seccion.id_seccion == entidad.id_seccion)
    )
    return modulo.joins(consulta).order_by(Colegio.nombre, Grado.id_grado, Seccion.nombre, tocados.c.id)


def _item(modulo: str, fila) -> RegistroDeActividad:
    datos = dict(fila._mapping)
    alumno = None
    if datos.get("id_alumno") is not None:
        alumno = ItemCatalogo(id=datos["id_alumno"], nombre=f"{datos['nombres']} {datos['apellidos']}")
    programa = None
    if datos.get("id_programa") is not None:
        programa = ItemCatalogo(id=datos["id_programa"], nombre=datos["programa"])
    return RegistroDeActividad(
        modulo=modulo, id=datos["id"], cambios=datos["cambios"],
        colegio=ItemCatalogo(id=datos["id_colegio"], nombre=datos["colegio"]),
        grado=ItemCatalogo(id=datos["id_grado"], nombre=datos["grado"]),
        seccion=ItemCatalogo(id=datos["id_seccion"], nombre=datos["seccion"]),
        programa=programa, alumno=alumno,
        **{c: datos.get(c) for c in ("fecha", "id_semana", "semana_inicio", "mes", "id_anio_escolar",
                                       "id_periodo_academico")},
    )


def registros_de_la_actividad(
    db: Session, id_docente: int, id_actividad: uuid.UUID, modulo: ModuloRegistros, page: int
) -> Paginado[RegistroDeActividad]:
    """Los registros del módulo tocados en la actividad del docente. 404
    `docente_no_encontrado` y `actividad_no_encontrada` (inexistente o de otro docente)."""
    usuario_del_docente(db, id_docente)
    actividad = db.get(Actividad, id_actividad)
    if actividad is None or actividad.id_docente != id_docente:
        raise ErrorNegocio(
            status.HTTP_404_NOT_FOUND, MENSAJE_ACTIVIDAD_NO_ENCONTRADA, motivo="actividad_no_encontrada"
        )
    filas, total = paginar(db, _consulta(MODULOS[modulo], id_actividad), page)
    return pagina([_item(modulo, f) for f in filas], total, page)
