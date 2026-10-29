"""Catálogos de solo lectura para cualquier usuario autenticado (precarga del frontend).

Sin paginación: son listas cortas y estables.
"""
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.database import get_db
from app.core.tiempo import hoy_lima
from app.dependencies import get_current_user, require_role
from app.models.evaluacion import NivelRazkids, NivelRubrica
from app.models.organizacion import TIPO_PA, AnioEscolar, Grado, PeriodoAcademico, Programa, Usuario
from app.schemas.catalogos import (
    AnioEscolarResponse,
    GradoResponse,
    NivelRazkidsResponse,
    NivelRubricaResponse,
    PeriodoAcademicoResponse,
    ProgramaResponse,
    SemanaResponse,
)
from app.services import lectura_service
from app.services.calendario import PeriodoCalendario, cargar_calendarios
from app.services.programacion import FiltroGrupos
from app.services.usuario_service import DOCENTE, SUPERVISOR

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


def _periodo_academico(p: PeriodoAcademico, pa: Optional[PeriodoCalendario], hoy: date) -> PeriodoAcademicoResponse:
    return PeriodoAcademicoResponse(
        id=p.id_periodo_academico, id_anio_escolar=p.id_anio_escolar, numero=p.numero, tipo=TIPO_PA,
        fecha_inicio=pa.fecha_inicio if pa else None, fecha_fin=pa.fecha_fin if pa else None,
        vigente=pa is not None and pa.contiene(hoy),
    )


@router.get("/periodos-academicos", response_model=list[PeriodoAcademicoResponse])
def listar_periodos_academicos(
    db: Db, _: Autenticado, id_anio_escolar: Optional[int] = None, id_colegio: Optional[int] = None
):
    """PA1 a PA4 de cada año con las fechas del calendario general (CU027) o, con
    `id_colegio`, del calendario que aplica a ese colegio (su particular activo si lo
    tiene). Fechas null si el año no tiene calendario. Los recesos (PR) y los días no
    lectivos están en GET /calendarios."""
    hoy = hoy_lima()
    consulta = (
        select(PeriodoAcademico, AnioEscolar.fecha_inicio)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PeriodoAcademico.id_anio_escolar)
        .order_by(AnioEscolar.fecha_inicio, PeriodoAcademico.numero)
    )
    if id_anio_escolar is not None:
        consulta = consulta.where(PeriodoAcademico.id_anio_escolar == id_anio_escolar)
    calendarios = cargar_calendarios(db, id_anio_escolar, ids_colegio=[] if id_colegio is None else [id_colegio])
    respuesta = []
    for p, _ in db.exec(consulta).all():
        calendario = calendarios.aplicable(id_colegio, p.id_anio_escolar)
        respuesta.append(_periodo_academico(p, calendario.pa(p.numero) if calendario else None, hoy))
    return respuesta


@router.get("/semanas", response_model=list[SemanaResponse])
def listar_semanas(
    db: Db,
    # Es parte de Seguimiento de Lectura (CU023): el Directivo no tiene acceso.
    usuario: Annotated[Usuario, Depends(require_role(SUPERVISOR, DOCENTE))],
    id_anio_escolar: int,
    mes: Annotated[int, Query(ge=1, le=12)],
    id_colegio: int,
    id_grado: int,
    id_seccion: int,
    id_programa: Optional[int] = None,
):
    """Semanas (lunes a viernes) del año ubicadas en ese mes, para elegir la tabla de
    lectura (CU023): las que tienen una tabla del grupo con ese `mes` y, sin tabla, las
    que tienen su clase habitual o reprogramada en ese mes (si solo hay adicionales, la
    primera). Con `id_programa`, la tabla y la regla son de ese subprograma. Una semana
    que cae en dos meses aparece en uno solo, con sus fechas completas. Errores: 403
    `sin_permiso` (Directivo), 403 `aula_fuera_de_alcance` (Docente), 404
    `colegio_no_encontrado` y `anio_no_encontrado`, 422 `seccion_inexistente`."""
    grupo = FiltroGrupos(id_colegio, id_grado, id_seccion)
    return lectura_service.semanas_con_clase(db, usuario, grupo, id_anio_escolar, mes, id_programa)


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
        NivelRubrica.id_programa, NivelRubrica.dimension, NivelRubrica.valor
    )
    if id_programa is not None:
        consulta = consulta.where(NivelRubrica.id_programa == id_programa)
    return [
        NivelRubricaResponse(
            id=n.id_nivel_rubrica, id_programa=n.id_programa, dimension=n.dimension,
            orden=n.orden, valor=n.valor, nombre_nivel=n.nombre_nivel,
        )
        for n in db.exec(consulta).all()
    ]
