"""Catálogos de solo lectura para cualquier usuario autenticado (precarga del frontend).

Sin paginación: son listas cortas y estables.
"""
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, col, select

from app.core.database import get_db
from app.core.tiempo import hoy_lima
from app.dependencies import get_current_user
from app.models.evaluacion import NivelGeneral, NivelRazkids, NivelRubrica, SemanaReporte
from app.models.organizacion import AnioEscolar, Grado, PeriodoAcademico, Programa, Usuario
from app.schemas.catalogos import (
    AnioEscolarResponse,
    GradoResponse,
    NivelGeneralResponse,
    NivelRazkidsResponse,
    NivelRubricaResponse,
    PeriodoAcademicoResponse,
    ProgramaResponse,
    SemanaResponse,
)

router = APIRouter(tags=["catalogos"])

Db = Annotated[Session, Depends(get_db)]
Autenticado = Annotated[Usuario, Depends(get_current_user)]


def _vigente(inicio: date, fin: date, hoy: date) -> bool:
    return inicio <= hoy <= fin


@router.get("/grados", response_model=list[GradoResponse])
def listar_grados(db: Db, _: Autenticado):
    return [
        GradoResponse(id=g.id_grado, id_grado=g.id_grado, nombre=g.nombre, id_ciclo=g.id_ciclo)
        for g in db.exec(select(Grado).order_by(Grado.id_grado)).all()
    ]


@router.get("/programas", response_model=list[ProgramaResponse])
def listar_programas(db: Db, _: Autenticado):
    return [
        ProgramaResponse(id=p.id_programa, id_programa=p.id_programa, nombre=p.nombre)
        for p in db.exec(select(Programa).order_by(Programa.id_programa)).all()
    ]


@router.get("/anios-escolares", response_model=list[AnioEscolarResponse])
def listar_anios_escolares(db: Db, _: Autenticado):
    hoy = hoy_lima()
    return [
        AnioEscolarResponse(
            id=a.id_anio_escolar, nombre=a.nombre, fecha_inicio=a.fecha_inicio,
            fecha_fin=a.fecha_fin, vigente=_vigente(a.fecha_inicio, a.fecha_fin, hoy),
        )
        for a in db.exec(select(AnioEscolar).order_by(AnioEscolar.fecha_inicio)).all()
    ]


@router.get("/periodos-academicos", response_model=list[PeriodoAcademicoResponse])
def listar_periodos_academicos(db: Db, _: Autenticado, id_anio_escolar: Optional[int] = None):
    hoy = hoy_lima()
    consulta = (
        select(PeriodoAcademico, AnioEscolar.fecha_inicio)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .order_by(AnioEscolar.fecha_inicio, PeriodoAcademico.numero)
    )
    if id_anio_escolar is not None:
        consulta = consulta.where(PeriodoAcademico.id_anio_escolar == id_anio_escolar)
    return [
        PeriodoAcademicoResponse(
            id=p.id_periodo_academico, id_anio_escolar=p.id_anio_escolar, numero=p.numero,
            fecha_inicio=p.fecha_inicio, fecha_fin=p.fecha_fin,
            vigente=_vigente(p.fecha_inicio, p.fecha_fin, hoy),
        )
        for p, _ in db.exec(consulta).all()
    ]


@router.get("/semanas", response_model=list[SemanaResponse])
def listar_semanas(db: Db, _: Autenticado, id_periodo_academico: Optional[int] = None):
    hoy = hoy_lima()
    # Por fecha y número: la numeración es correlativa dentro de cada año escolar.
    consulta = select(SemanaReporte).order_by(SemanaReporte.fecha_inicio, SemanaReporte.numero_semana)
    if id_periodo_academico is not None:
        consulta = consulta.where(SemanaReporte.id_periodo_academico == id_periodo_academico)
    return [
        SemanaResponse(
            id=s.id_semana, id_periodo_academico=s.id_periodo_academico, numero_semana=s.numero_semana,
            fecha_inicio=s.fecha_inicio, fecha_fin=s.fecha_fin, cerrada=s.fecha_fin < hoy,
        )
        for s in db.exec(consulta).all()
    ]


@router.get("/niveles-razkids", response_model=list[NivelRazkidsResponse])
def listar_niveles_razkids(db: Db, _: Autenticado):
    """El id es el orden de los niveles (A, B, C...)."""
    return [
        NivelRazkidsResponse(id=n.id_nivel_rk, letra=n.letra)
        for n in db.exec(select(NivelRazkids).order_by(NivelRazkids.id_nivel_rk)).all()
    ]


@router.get("/niveles-rubrica", response_model=list[NivelRubricaResponse])
def listar_niveles_rubrica(db: Db, _: Autenticado, id_programa: Optional[int] = None):
    consulta = select(NivelRubrica).order_by(
        NivelRubrica.id_programa, NivelRubrica.dimension, NivelRubrica.orden
    )
    if id_programa is not None:
        consulta = consulta.where(NivelRubrica.id_programa == id_programa)
    return [
        NivelRubricaResponse(
            id=n.id_nivel_rubrica, id_programa=n.id_programa, dimension=n.dimension,
            orden=n.orden, nombre_nivel=n.nombre_nivel,
        )
        for n in db.exec(consulta).all()
    ]


@router.get("/niveles-generales", response_model=list[NivelGeneralResponse])
def listar_niveles_generales(db: Db, _: Autenticado):
    return [
        NivelGeneralResponse(id=n.id_nivel_general, orden=n.orden, nombre_nivel=n.nombre_nivel)
        for n in db.exec(select(NivelGeneral).order_by(col(NivelGeneral.orden))).all()
    ]
