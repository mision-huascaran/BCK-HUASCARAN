"""Endpoints provisionales del Dashboard (Romina). Se reemplazan en las tandas siguientes.

El año de referencia sigue CU015; el PA es opcional en indicadores académicos.
"""
from collections import defaultdict
from typing import Optional

from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.models.evaluacion import (
    ESTADOS_ASISTIO, Asistencia, Clase, LECTURA_NORMAL, LecturaSemanal, LecturaSemanalAlumno,
)
from app.models.organizacion import AnioEscolar, Colegio, Grado, PeriodoAcademico, Programa, Seccion
from app.schemas.dashboard import FiltrosDashboard, LecturaDashboard
from app.services.calendario import anio_de_referencia


def validar_filtros(db: Session, f: FiltrosDashboard) -> Optional[AnioEscolar]:
    """Comprobar existencia y coherencia de identificadores, nunca ignorar filtros inválidos."""
    if f.id_colegios is not None:
        if not f.id_colegios or len(f.id_colegios) != len(set(f.id_colegios)):
            raise error_de_campo("id_colegios", "Seleccione colegios sin duplicados.")
        if f.id_colegio is not None and f.id_colegio not in f.id_colegios:
            raise error_de_campo("id_colegios", "Los filtros de colegio son incompatibles.")
        for identificador in f.id_colegios:
            if identificador <= 0 or db.get(Colegio, identificador) is None:
                raise error_de_campo("id_colegios", "Uno de los colegios no existe.")
    for campo, modelo, pk in (
        ("id_colegio", Colegio, Colegio.id_colegio),
        ("id_anio_escolar", AnioEscolar, AnioEscolar.id_anio_escolar),
        ("id_grado", Grado, Grado.id_grado),
        ("id_seccion", Seccion, Seccion.id_seccion),
        ("id_programa", Programa, Programa.id_programa),
        ("id_periodo_academico", PeriodoAcademico, PeriodoAcademico.id_periodo_academico),
    ):
        valor = getattr(f, campo)
        if valor is not None and (valor <= 0 or db.exec(select(pk).where(pk == valor)).first() is None):
            raise error_de_campo(campo, "El identificador no existe o no es válido.")
    if f.id_seccion is not None:
        seccion = db.get(Seccion, f.id_seccion)
        if (f.id_colegio is not None and seccion.id_colegio != f.id_colegio) or (
            f.id_colegios is not None and seccion.id_colegio not in f.id_colegios
        ):
            raise error_de_campo("id_seccion", "La sección no pertenece a los colegios indicados.")
        if f.id_grado is not None and seccion.id_grado != f.id_grado:
            raise error_de_campo("id_seccion", "La sección no pertenece al grado indicado.")
    if f.id_periodo_academico is not None:
        periodo = db.get(PeriodoAcademico, f.id_periodo_academico)
        if f.id_anio_escolar is not None and periodo.id_anio_escolar != f.id_anio_escolar:
            raise error_de_campo("id_periodo_academico", "El PA no pertenece al año escolar seleccionado.")
        return db.get(AnioEscolar, periodo.id_anio_escolar)
    if f.id_anio_escolar is not None:
        return db.get(AnioEscolar, f.id_anio_escolar)
    return anio_de_referencia(db)


def _grupo(f: FiltrosDashboard, modelo, programa: bool = True):
    condiciones = []
    for campo in ("id_colegio", "id_grado", "id_seccion"):
        if getattr(f, campo) is not None:
            condiciones.append(getattr(modelo, campo) == getattr(f, campo))
    if f.id_colegios is not None:
        condiciones.append(col(modelo.id_colegio).in_(f.id_colegios))
    if programa and f.id_programa is not None:
        condiciones.append(modelo.id_programa == f.id_programa)
    return condiciones


def libros(db: Session, f: FiltrosDashboard) -> LecturaDashboard:
    """Total de libros en el año o PA solicitado, según la clase de referencia."""
    anio = validar_filtros(db, f)
    if anio is None:
        return LecturaDashboard(total_libros=0, libros_lsn=0, libros_lsl=0)
    filas = db.exec(select(
        LecturaSemanalAlumno.id_alumno, LecturaSemanalAlumno.lsn_titulo,
        LecturaSemanalAlumno.cantidad_lsl, Clase.fecha,
    ).join(LecturaSemanal, LecturaSemanal.id == LecturaSemanalAlumno.id_lectura_semanal)
    .join(Clase, Clase.id_clase == LecturaSemanal.id_clase_referencia)
    .join(Asistencia, (Asistencia.id_clase == Clase.id_clase) &
          (Asistencia.id_alumno == LecturaSemanalAlumno.id_alumno))
    .where(
        LecturaSemanal.id_anio_escolar == anio.id_anio_escolar,
        *([Clase.id_periodo_academico == f.id_periodo_academico]
          if f.id_periodo_academico is not None else []),
        LecturaSemanal.estado == LECTURA_NORMAL,
        col(Asistencia.estado).in_(ESTADOS_ASISTIO),
        *_grupo(f, LecturaSemanal),
    )).all()
    lsn, lsl = 0, 0
    por_mes = defaultdict(int)
    alumnos_con_lecturas = set()
    for id_alumno, titulo, cantidad, fecha_clase in filas:
        a = int(titulo is not None)
        b = cantidad or 0
        if a + b > 0:
            alumnos_con_lecturas.add(id_alumno)
        lsn += a
        lsl += b
        por_mes[fecha_clase.month] += a + b
    total = lsn + lsl
    n_alumnos = len(alumnos_con_lecturas)
    return LecturaDashboard(
        total_libros=total, libros_lsn=lsn, libros_lsl=lsl,
        alumnos_con_lecturas=n_alumnos,
        promedio_por_alumno_con_lecturas=round(total / n_alumnos, 2) if n_alumnos else None,
        por_mes=dict(sorted(por_mes.items())),
    )
