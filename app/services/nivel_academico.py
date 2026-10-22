"""Nivel Académico del alumno que muestra Seguimiento de Lectura (CU024).

Es el último nivel colocado del Registro de Vuelo de un PA anterior al PA de la clase
(según el calendario del colegio de la clase), en cualquier grupo del alumno; sin él,
null ("Sin evaluar").
"""
from datetime import date
from typing import Optional

from sqlmodel import Session, col, select

from app.models.evaluacion import NivelRazkids, RegistroVuelo
from app.models.organizacion import PeriodoAcademico
from app.services.calendario import periodo_de


def niveles_academicos(db: Session, ids_alumno: list[int], id_colegio: int, fecha: date) -> dict[int, Optional[str]]:
    """Nivel Académico (Lectura): el último nivel colocado de un PA anterior al PA de la
    fecha en el calendario del colegio; None ("Sin evaluar") si no hay."""
    periodo = periodo_de(db, id_colegio, fecha)
    actual = db.get(PeriodoAcademico, periodo.id_periodo_academico) if periodo else None
    if actual is None or not ids_alumno:
        return {}
    filas = db.exec(
        select(RegistroVuelo, PeriodoAcademico.numero, NivelRazkids.letra)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == RegistroVuelo.id_periodo_academico)
        .join(NivelRazkids, NivelRazkids.id_nivel_rk == RegistroVuelo.id_nivel_colocado)
        .where(
            col(RegistroVuelo.id_alumno).in_(ids_alumno),
            PeriodoAcademico.id_anio_escolar == actual.id_anio_escolar,
            PeriodoAcademico.numero < actual.numero,
        )
    ).all()
    ultimo: dict[int, tuple] = {}
    for fila, numero, letra in filas:
        clave = (numero, fila.fecha_registro)
        if fila.id_alumno not in ultimo or clave > ultimo[fila.id_alumno][0]:
            ultimo[fila.id_alumno] = (clave, letra)
    return {id_alumno: letra for id_alumno, (_, letra) in ultimo.items()}


def nivel_academico(db: Session, id_alumno: int, id_colegio: int, fecha_clase: date) -> Optional[str]:
    """Como `niveles_academicos`, para un solo alumno."""
    return niveles_academicos(db, [id_alumno], id_colegio, fecha_clase).get(id_alumno)
