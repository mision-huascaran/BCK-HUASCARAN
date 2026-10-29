"""Lo que el front guarda para validar escrituras sin conexión (GET /me/habilitaciones).

Incluye todo lo que el Docente puede usar ahora o más adelante: asignaciones de PA en
curso o futuros, asignaciones retroactivas cuyo PR todavía no termina y permisos
programados o vigentes, más el calendario de cada (colegio, año escolar) que aparece en
ellos. No depende del "año en curso": el PR4 puede pasar al año calendario siguiente. El
backend vuelve a validar todo al sincronizar.
"""
from datetime import date
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    AnioEscolar,
    Colegio,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    PermisoCorreccion,
    Usuario,
)
from app.schemas.calendario import CalendarioDeColegio
from app.schemas.comun import ItemCatalogo
from app.schemas.habilitacion import AsignacionUtilizable, MisHabilitaciones, PermisoUtilizable
from app.services.calendario import CalendarioResuelto, Calendarios, PeriodoCalendario, cargar_calendarios
from app.services.calendario_gestion import respuesta
from app.services.habilitacion import docente_habilitable

ParColegioAnio = tuple[int, int]


def _receso_de(calendario: CalendarioResuelto, pa: PeriodoCalendario) -> Optional[PeriodoCalendario]:
    """El PR que sigue a ese PA."""
    return next((p for p in calendario.periodos if not p.es_pa and p.numero == pa.numero), None)


def _utilizable(calendarios: Calendarios, fila: DocenteColegioGrado, id_anio: int, hoy: date) -> bool:
    """Si la asignación todavía sirve: su PA no terminó o, si es retroactiva, su PR no
    terminó. Sin calendario configurado para su año, se incluye (es de un año futuro)."""
    calendario = calendarios.aplicable(fila.id_colegio, id_anio)
    pa = calendario.pa_de(fila.id_periodo_academico) if calendario is not None else None
    if pa is None:
        return calendario is None
    if pa.fecha_fin >= hoy:
        return True
    receso = _receso_de(calendario, pa) if fila.retroactiva else None
    return receso is not None and receso.fecha_fin >= hoy


def _colegios_activos(db: Session, ids: set[int]) -> dict[int, Colegio]:
    if not ids:
        return {}
    filas = db.exec(select(Colegio).where(col(Colegio.id_colegio).in_(ids), col(Colegio.activo).is_(True))).all()
    return {c.id_colegio: c for c in filas}


def _grados(db: Session, ids: set[int]) -> dict[int, str]:
    """Nombre de cada grado, en una sola consulta."""
    if not ids:
        return {}
    return dict(db.exec(select(Grado.id_grado, Grado.nombre).where(col(Grado.id_grado).in_(ids))).all())


def _nombres(colegios: dict[int, Colegio], grados: dict[int, str], id_colegio: int, id_grado: int) -> dict:
    """`colegio` y `grado` con nombre, para que el front arme el filtro de aulas sin red."""
    return {
        "colegio": ItemCatalogo(id=id_colegio, nombre=colegios[id_colegio].nombre),
        "grado": ItemCatalogo(id=id_grado, nombre=grados[id_grado]),
    }


def _asignaciones(db: Session, id_docente: int) -> list[tuple[DocenteColegioGrado, int]]:
    return list(
        db.exec(
            select(DocenteColegioGrado, PeriodoAcademico.id_anio_escolar)
            .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
            .where(DocenteColegioGrado.id_docente == id_docente)
            .order_by(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado, DocenteColegioGrado.id_periodo_academico)
        ).all()
    )


def _permisos(db: Session, id_docente: int) -> list[PermisoCorreccion]:
    """Programados o vigentes: no revocados y sin vencer."""
    return list(
        db.exec(
            select(PermisoCorreccion)
            .where(
                PermisoCorreccion.id_docente == id_docente,
                col(PermisoCorreccion.revocado_en).is_(None),
                PermisoCorreccion.fin > ahora_utc(),
            )
            .order_by(PermisoCorreccion.inicio, PermisoCorreccion.id_permiso)
        ).all()
    )


def _calendarios_de(
    db: Session, pares: list[ParColegioAnio], calendarios: Calendarios, colegios: dict[int, Colegio]
) -> list[CalendarioDeColegio]:
    nombres = dict(db.exec(select(AnioEscolar.id_anio_escolar, AnioEscolar.nombre)).all())
    salida = []
    for id_colegio, id_anio in sorted(set(pares)):
        aplicable = calendarios.aplicable(id_colegio, id_anio)
        if aplicable is not None:
            datos = respuesta(aplicable, colegios[id_colegio], nombres.get(id_anio)).model_dump()
            salida.append(CalendarioDeColegio(**datos, id_colegio=id_colegio))
    return salida


def mis_habilitaciones(db: Session, usuario: Usuario) -> MisHabilitaciones:
    generado_en = ahora_utc()
    id_docente = docente_habilitable(db, usuario)
    if id_docente is None:
        return MisHabilitaciones(generado_en=generado_en, asignaciones=[], permisos=[], calendarios=[])
    filas, permisos = _asignaciones(db, id_docente), _permisos(db, id_docente)
    colegios = _colegios_activos(db, {f.id_colegio for f, _ in filas} | {p.id_colegio for p in permisos})
    calendarios = cargar_calendarios(db, ids_colegio=list(colegios))
    hoy = hoy_lima()
    asignaciones = [
        (fila, id_anio)
        for fila, id_anio in filas
        if fila.id_colegio in colegios and _utilizable(calendarios, fila, id_anio, hoy)
    ]
    permisos = [p for p in permisos if p.id_colegio in colegios]
    pares = [(f.id_colegio, id_anio) for f, id_anio in asignaciones] + [(p.id_colegio, p.id_anio_escolar) for p in permisos]
    grados = _grados(db, {f.id_grado for f, _ in asignaciones} | {p.id_grado for p in permisos})
    return MisHabilitaciones(
        generado_en=generado_en,
        asignaciones=[
            AsignacionUtilizable(
                id_colegio=f.id_colegio, id_grado=f.id_grado, id_periodo_academico=f.id_periodo_academico,
                retroactiva=f.retroactiva, **_nombres(colegios, grados, f.id_colegio, f.id_grado),
            )
            for f, _ in asignaciones
        ],
        permisos=[
            PermisoUtilizable(
                id_permiso=p.id_permiso, id_colegio=p.id_colegio, id_grado=p.id_grado,
                id_periodo_academico=p.id_periodo_academico, modulo=p.modulo, inicio=p.inicio, fin=p.fin,
                **_nombres(colegios, grados, p.id_colegio, p.id_grado),
            )
            for p in permisos
        ],
        calendarios=_calendarios_de(db, pares, calendarios, colegios),
    )
