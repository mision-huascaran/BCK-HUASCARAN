"""Pestañas de la vista de detalle del alumno (CU015), de solo lectura.

Todas trabajan sobre el año escolar de referencia: el vigente o, entre años, el último que
ya empezó (`calendario.anio_de_referencia`). Los permisos (alcance del Docente) los aplica
el router con `alumno_service.alumno_visible`.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.core.paginacion import Paginado, pagina, paginar
from app.core.tiempo import ZONA_LIMA
from app.models.evaluacion import (
    AsistenciaSemanal,
    EvaluacionDiagnostica,
    EvaluacionDiagnosticaAlumno,
    LibroSubidaNivel,
    NivelFinalMensual,
    NivelGeneral,
    NivelRazkids,
    NivelRubrica,
    ReporteSemanalAlumno,
    RubricaRegistroSemanal,
    SemanaReporte,
)
from app.models.organizacion import AnioEscolar, CicloEbr, Colegio, Grado, PeriodoAcademico, Programa, Rol, Usuario
from app.models.trazabilidad import Auditoria
from app.schemas.alumno import (
    CambioHistorial,
    EventoHistorial,
    LecturaSemanal,
    LibroLsb,
    NivelActual,
    RegistroVuelo,
    Resumen,
    Rubrica,
    RubricaMensual,
    RubricaSemanal,
)
from app.services.calendario import anio_de_referencia

# ── Resumen ─────────────────────────────────────────────────────────────────────────

def resumen(db: Session, id_alumno: int) -> Resumen:
    anio = anio_de_referencia(db)
    if anio is None:
        return Resumen()
    return Resumen(
        nivel_actual=_nivel_actual(db, id_alumno, anio.id_anio_escolar),
        libros_leidos_anio=_libros_leidos(db, id_alumno, anio.id_anio_escolar),
        asistencia_porcentaje=_asistencia(db, id_alumno, anio.id_anio_escolar),
    )


def _nivel_actual(db: Session, id_alumno: int, id_anio: int) -> Optional[NivelActual]:
    """Nivel del `nivel_final_mensual` más reciente del año, o None."""
    fila = db.exec(
        select(NivelFinalMensual.mes, NivelGeneral.nombre_nivel)
        .join(NivelGeneral, NivelGeneral.id_nivel_general == NivelFinalMensual.id_nivel_final)
        .where(NivelFinalMensual.id_alumno == id_alumno, NivelFinalMensual.id_anio_escolar == id_anio)
        .order_by(col(NivelFinalMensual.mes).desc())
    ).first()
    return NivelActual(nombre=fila[1], mes=fila[0]) if fila else None


def _libros_leidos(db: Session, id_alumno: int, id_anio: int) -> Optional[int]:
    """LSL + LSB de los reportes del año; None si no tiene ningún reporte en el año."""
    reportes, lsl = db.exec(
        select(func.count(ReporteSemanalAlumno.id), func.coalesce(func.sum(ReporteSemanalAlumno.cantidad_lsl), 0))
        .where(ReporteSemanalAlumno.id_alumno == id_alumno, ReporteSemanalAlumno.id_anio_escolar == id_anio)
    ).one()
    if not reportes:
        return None
    lsb = db.exec(
        select(func.count(LibroSubidaNivel.id))
        .join(ReporteSemanalAlumno, ReporteSemanalAlumno.id == LibroSubidaNivel.id_reporte_semanal)
        .where(ReporteSemanalAlumno.id_alumno == id_alumno, ReporteSemanalAlumno.id_anio_escolar == id_anio)
    ).one()
    return int(lsl) + lsb


def _asistencia(db: Session, id_alumno: int, id_anio: int) -> Optional[float]:
    """% de semanas asistidas sobre las registradas en el año, con 1 decimal; None sin filas."""
    filas = db.exec(
        select(AsistenciaSemanal.asistio)
        .join(SemanaReporte, SemanaReporte.id_semana == AsistenciaSemanal.id_semana)
        .where(AsistenciaSemanal.id_alumno == id_alumno, SemanaReporte.id_anio_escolar == id_anio)
    ).all()
    if not filas:
        return None
    return round(100 * sum(1 for asistio in filas if asistio) / len(filas), 1)


# ── Registro de Vuelo, Rúbrica y Seguimiento de Lectura ─────────────────────────────

def registro_vuelo(db: Session, id_alumno: int) -> list[RegistroVuelo]:
    anio = anio_de_referencia(db)
    if anio is None:
        return []
    entrada = aliased(NivelRazkids)
    colocado = aliased(NivelRazkids)
    filas = db.exec(
        select(
            PeriodoAcademico.numero, EvaluacionDiagnostica.fecha_realizacion, CicloEbr.nombre,
            entrada.letra, EvaluacionDiagnosticaAlumno, colocado.letra, NivelGeneral.nombre_nivel,
        )
        .select_from(EvaluacionDiagnosticaAlumno)
        .join(
            EvaluacionDiagnostica,
            EvaluacionDiagnostica.id_evaluacion_diagnostica == EvaluacionDiagnosticaAlumno.id_evaluacion_diagnostica,
        )
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == EvaluacionDiagnostica.id_periodo_academico)
        .join(CicloEbr, CicloEbr.id_ciclo == EvaluacionDiagnosticaAlumno.id_ciclo_evaluado)
        .join(entrada, entrada.id_nivel_rk == EvaluacionDiagnosticaAlumno.id_nivel_rk_entrada)
        .outerjoin(colocado, colocado.id_nivel_rk == EvaluacionDiagnosticaAlumno.nivel_colocado)
        .outerjoin(NivelGeneral, NivelGeneral.id_nivel_general == EvaluacionDiagnosticaAlumno.id_nivel_general)
        .where(
            EvaluacionDiagnosticaAlumno.id_alumno == id_alumno,
            PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar,
        )
        .order_by(PeriodoAcademico.numero)
    ).all()
    return [
        RegistroVuelo(
            corte=corte, fecha=fecha, ciclo_evaluado=ciclo, nivel_entrada=nivel_entrada,
            aciertos=registro.aciertos_prueba, total=registro.total_prueba,
            nivel_colocado=nivel_colocado, nivel_general=nivel_general, observacion=registro.observacion,
        )
        for corte, fecha, ciclo, nivel_entrada, registro, nivel_colocado, nivel_general in filas
    ]


def rubrica(db: Session, id_alumno: int) -> Rubrica:
    anio = anio_de_referencia(db)
    if anio is None:
        return Rubrica(semanales=[], mensuales=[])
    fluidez = aliased(NivelRubrica)
    comprension = aliased(NivelRubrica)
    semanales = db.exec(
        select(SemanaReporte, RubricaRegistroSemanal, fluidez.nombre_nivel, comprension.nombre_nivel)
        .select_from(RubricaRegistroSemanal)
        .join(SemanaReporte, SemanaReporte.id_semana == RubricaRegistroSemanal.id_semana)
        .outerjoin(fluidez, fluidez.id_nivel_rubrica == RubricaRegistroSemanal.id_nivel_fluidez)
        .outerjoin(comprension, comprension.id_nivel_rubrica == RubricaRegistroSemanal.id_nivel_comprension)
        .where(RubricaRegistroSemanal.id_alumno == id_alumno, RubricaRegistroSemanal.id_anio_escolar == anio.id_anio_escolar)
        .order_by(col(SemanaReporte.fecha_inicio).desc())
    ).all()
    mensuales = db.exec(
        select(NivelFinalMensual, NivelGeneral.nombre_nivel)
        .join(NivelGeneral, NivelGeneral.id_nivel_general == NivelFinalMensual.id_nivel_final)
        .where(NivelFinalMensual.id_alumno == id_alumno, NivelFinalMensual.id_anio_escolar == anio.id_anio_escolar)
        .order_by(col(NivelFinalMensual.mes).desc())
    ).all()
    return Rubrica(
        semanales=[
            RubricaSemanal(
                semana=semana.numero_semana, fecha_inicio=semana.fecha_inicio,
                fluidez=nivel_fluidez, comprension=nivel_comprension, observacion=registro.observacion,
            )
            for semana, registro, nivel_fluidez, nivel_comprension in semanales
        ],
        mensuales=[
            RubricaMensual(
                mes=nivel.mes, nivel_final=nombre, ajustado=nivel.ajustado_por_docente,
                justificacion=nivel.justificacion,
            )
            for nivel, nombre in mensuales
        ],
    )


def lectura(db: Session, id_alumno: int) -> list[LecturaSemanal]:
    anio = anio_de_referencia(db)
    if anio is None:
        return []
    reportes = db.exec(
        select(ReporteSemanalAlumno, SemanaReporte)
        .join(SemanaReporte, SemanaReporte.id_semana == ReporteSemanalAlumno.id_semana)
        .where(ReporteSemanalAlumno.id_alumno == id_alumno, ReporteSemanalAlumno.id_anio_escolar == anio.id_anio_escolar)
        .order_by(col(SemanaReporte.fecha_inicio).desc())
    ).all()
    libros: dict[int, list[LibroLsb]] = defaultdict(list)
    if reportes:
        for libro in db.exec(
            select(LibroSubidaNivel)
            .where(col(LibroSubidaNivel.id_reporte_semanal).in_([r.id for r, _ in reportes]))
            .order_by(LibroSubidaNivel.id)
        ).all():
            libros[libro.id_reporte_semanal].append(
                LibroLsb(titulo=libro.titulo_libro, aciertos=libro.aciertos, total=libro.total_preguntas)
            )
    return [
        LecturaSemanal(
            semana=semana.numero_semana, fecha_inicio=semana.fecha_inicio,
            cantidad_lsl=reporte.cantidad_lsl, observaciones=reporte.observaciones,
            libros_lsb=libros[reporte.id],
        )
        for reporte, semana in reportes
    ]


# ── Historial ───────────────────────────────────────────────────────────────────────

# Campo guardado en `auditoria` -> etiqueta legible.
ETIQUETAS = {
    "nombres": "Nombres",
    "apellidos": "Apellidos",
    "id_grado": "Grado",
    "id_programa": "Subprograma",
    "id_colegio": "Colegio",
    "activo": "Estado",
}
# Campos que guardan un id de catálogo -> (modelo, columna id).
CATALOGOS = {
    "id_grado": (Grado, "id_grado"),
    "id_programa": (Programa, "id_programa"),
    "id_colegio": (Colegio, "id_colegio"),
}
ESTADOS = {"true": "Activo", "false": "Inactivo"}


def _limites_del_anio(anio: AnioEscolar) -> tuple[datetime, datetime]:
    """Instantes UTC que cubren el año escolar en hora de Lima: [inicio, fin)."""
    inicio = datetime.combine(anio.fecha_inicio, time.min, tzinfo=ZONA_LIMA)
    fin = datetime.combine(anio.fecha_fin + timedelta(days=1), time.min, tzinfo=ZONA_LIMA)
    return inicio, fin


def _nombres_de_catalogo(db: Session, filas: list[Auditoria]) -> dict[str, dict[str, str]]:
    """Para cada campo de catálogo, id (como texto) -> nombre, en una consulta por catálogo."""
    nombres: dict[str, dict[str, str]] = {}
    for campo, (modelo, columna) in CATALOGOS.items():
        ids = {
            int(v) for f in filas if f.campo == campo
            for v in (f.valor_anterior, f.valor_nuevo) if v is not None and v.isdigit()
        }
        if ids:
            clave = getattr(modelo, columna)
            nombres[campo] = {
                str(getattr(m, columna)): m.nombre
                for m in db.exec(select(modelo).where(col(clave).in_(ids))).all()
            }
    return nombres


def _legible(campo: str, valor: Optional[str], nombres: dict[str, dict[str, str]]) -> Optional[str]:
    if valor is None:
        return None
    if campo == "activo":
        return ESTADOS.get(valor, valor)
    # Si el id ya no existe en el catálogo, se muestra el id.
    return nombres.get(campo, {}).get(valor, valor)


def historial(db: Session, id_alumno: int, page: int) -> Paginado[EventoHistorial]:
    """Eventos de `auditoria` del alumno en el año escolar de referencia, del más reciente
    al más antiguo. Las filas con el mismo (fecha, usuario, acción) son un solo evento, y
    la paginación es sobre los eventos (10 por página), no sobre las filas.

    `crear` no guarda campos: sus `cambios` vienen vacíos. `activar` e `inactivar` se
    guardan con el campo `activo`: vienen con el cambio de "Estado".
    """
    anio = anio_de_referencia(db)
    if anio is None:
        return pagina([], 0, page)
    inicio, fin = _limites_del_anio(anio)
    filtro = [
        Auditoria.tabla == "alumno",
        Auditoria.id_registro == str(id_alumno),
        Auditoria.fecha >= inicio,
        Auditoria.fecha < fin,
    ]
    eventos = (
        select(Auditoria.fecha, Auditoria.id_usuario, Auditoria.accion)
        .where(*filtro)
        .group_by(Auditoria.fecha, Auditoria.id_usuario, Auditoria.accion)
        .order_by(col(Auditoria.fecha).desc(), Auditoria.accion)
    )
    claves, total = paginar(db, eventos, page)
    if not claves:
        return pagina([], total, page)

    filas = list(
        db.exec(
            select(Auditoria)
            .where(*filtro, col(Auditoria.fecha).in_({fecha for fecha, _, _ in claves}))
            .order_by(Auditoria.id_auditoria)
        ).all()
    )
    por_evento: dict[tuple, list[Auditoria]] = defaultdict(list)
    for fila in filas:
        por_evento[(fila.fecha, fila.id_usuario, fila.accion)].append(fila)

    autores = {
        u.id_usuario: (f"{u.nombres} {u.apellidos}", rol)
        for u, rol in db.exec(
            select(Usuario, Rol.nombre)
            .join(Rol, Rol.id_rol == Usuario.id_rol)
            .where(col(Usuario.id_usuario).in_({id_usuario for _, id_usuario, _ in claves}))
        ).all()
    }
    nombres = _nombres_de_catalogo(db, filas)

    items = []
    for fecha, id_usuario, accion in claves:
        autor, rol = autores.get(id_usuario, (str(id_usuario), ""))
        cambios = [
            CambioHistorial(
                campo=ETIQUETAS.get(f.campo, f.campo),
                anterior=_legible(f.campo, f.valor_anterior, nombres),
                nuevo=_legible(f.campo, f.valor_nuevo, nombres),
            )
            for f in por_evento[(fecha, id_usuario, accion)] if f.campo is not None
        ]
        items.append(EventoHistorial(fecha=fecha, usuario=autor, rol=rol, accion=accion, cambios=cambios))
    return pagina(items, total, page)
