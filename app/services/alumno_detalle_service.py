"""Pestañas de la vista de detalle del alumno (CU015), de solo lectura.

Todas trabajan sobre el año escolar de referencia: el vigente o, entre años, el último que
ya empezó (`calendario.anio_de_referencia`). Los permisos (alcance del Docente) los aplica
el router con `alumno_service.alumno_visible`.

Las pestañas son mini-grillas: Registro de Vuelo, una fila por periodo (4 como máximo);
Rúbrica y Seguimiento de Lectura, los 5 registros más recientes.
"""
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.core.paginacion import Paginado, pagina, paginar
from app.core.tiempo import limites_lima
from app.models.evaluacion import (
    ESTADOS_ASISTIO,
    Asistencia,
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
from app.models.organizacion import (
    Alumno,
    AnioEscolar,
    CicloEbr,
    Colegio,
    Grado,
    PeriodoAcademico,
    Programa,
    Seccion,
    Rol,
    Usuario,
)
from app.models.trazabilidad import Auditoria
from app.schemas.alumno import (
    CambioHistorial,
    EventoHistorial,
    LecturaSemanal,
    LibroLsb,
    NivelColocado,
    RegistroVuelo,
    Resumen,
    Rubrica,
    RubricaMensual,
    RubricaSemanal,
)
from app.services.calendario import anio_de_referencia
from app.services.nivel_descriptivo import escala_del_grado
from app.services.pertenencia import CAMPO_AUDITORIA, fecha_legible

# Filas de las mini-grillas del detalle (CU015).
FILAS_REGISTRO_VUELO = 4  # una por periodo
FILAS_RECIENTES = 5  # Rúbrica y Seguimiento de Lectura

# ── Resumen ─────────────────────────────────────────────────────────────────────────

def resumen(db: Session, alumno: Alumno) -> Resumen:
    """Tarjetas del detalle. Sin datos: nivel colocado null, 0 libros y 0 % (CU015)."""
    anio = anio_de_referencia(db)
    if anio is None:
        return Resumen()
    return Resumen(
        nivel_colocado=_nivel_colocado(db, alumno, anio.id_anio_escolar),
        libros_leidos_anio=_libros_leidos(db, alumno.id_alumno, anio.id_anio_escolar),
        asistencia_porcentaje=_asistencia(db, alumno, anio),
    )


def _nivel_colocado(db: Session, alumno: Alumno, id_anio: int) -> Optional[NivelColocado]:
    """Letra Raz-Kids colocada en el Registro de Vuelo más reciente del año: el del
    periodo más avanzado que tenga `nivel_colocado` (uno incompleto, sin él, no cuenta).
    None si ninguno lo tiene. Su nivel descriptivo es el del grado actual del alumno."""
    fila = db.exec(
        select(NivelRazkids.letra, NivelRazkids.id_nivel_rk, PeriodoAcademico.numero)
        .select_from(EvaluacionDiagnosticaAlumno)
        .join(
            EvaluacionDiagnostica,
            EvaluacionDiagnostica.id_evaluacion_diagnostica == EvaluacionDiagnosticaAlumno.id_evaluacion_diagnostica,
        )
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == EvaluacionDiagnostica.id_periodo_academico)
        .join(NivelRazkids, NivelRazkids.id_nivel_rk == EvaluacionDiagnosticaAlumno.nivel_colocado)
        .where(EvaluacionDiagnosticaAlumno.id_alumno == alumno.id_alumno, PeriodoAcademico.id_anio_escolar == id_anio)
        .order_by(col(PeriodoAcademico.numero).desc())
    ).first()
    if fila is None:
        return None
    letra, id_nivel_rk, numero = fila
    return NivelColocado(
        letra=letra, id_nivel_rk=id_nivel_rk, numero_periodo=numero,
        nivel_descriptivo=escala_del_grado(db, alumno.id_grado).nivel(id_nivel_rk),
    )


def _libros_leidos(db: Session, id_alumno: int, id_anio: int) -> int:
    """LSL + LSB de los reportes del año (0 si no tiene ninguno)."""
    lsl = db.exec(
        select(func.coalesce(func.sum(ReporteSemanalAlumno.cantidad_lsl), 0))
        .where(ReporteSemanalAlumno.id_alumno == id_alumno, ReporteSemanalAlumno.id_anio_escolar == id_anio)
    ).one()
    lsb = db.exec(
        select(func.count(LibroSubidaNivel.id))
        .join(ReporteSemanalAlumno, ReporteSemanalAlumno.id == LibroSubidaNivel.id_reporte_semanal)
        .where(ReporteSemanalAlumno.id_alumno == id_alumno, ReporteSemanalAlumno.id_anio_escolar == id_anio)
    ).one()
    return int(lsl) + lsb


def _asistencia(db: Session, alumno: Alumno, anio: AnioEscolar) -> float:
    """% de asistencia del año, con 1 decimal (CU015): asistencias del alumno (presente
    o tardanza) en su aula actual (colegio, grado y subprograma) sobre las clases
    dictadas en esa aula (las fechas con al menos un registro con estado). El aula es la
    guardada en cada registro: lo que el alumno registró en otra aula antes de rotar no
    cuenta aquí. 0 si no hubo clases."""
    de_su_aula = (
        Asistencia.fecha >= anio.fecha_inicio,
        Asistencia.fecha <= anio.fecha_fin,
        Asistencia.id_colegio == alumno.id_colegio,
        Asistencia.id_grado == alumno.id_grado,
        Asistencia.id_programa == alumno.id_programa_actual,
    )
    clases = db.exec(
        select(func.count(func.distinct(Asistencia.fecha))).where(*de_su_aula, col(Asistencia.estado).is_not(None))
    ).one()
    if not clases:
        return 0.0
    asistidas = db.exec(
        select(func.count())
        .select_from(Asistencia)
        .where(*de_su_aula, Asistencia.id_alumno == alumno.id_alumno, col(Asistencia.estado).in_(ESTADOS_ASISTIO))
    ).one()
    return round(100 * asistidas / clases, 1)


# ── Registro de Vuelo, Rúbrica y Seguimiento de Lectura ─────────────────────────────

def registro_vuelo(db: Session, alumno: Alumno) -> list[RegistroVuelo]:
    """Una fila por periodo del año. Cada letra colocada trae su nivel descriptivo según
    el grado actual del alumno (eje Y de la gráfica)."""
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
            EvaluacionDiagnosticaAlumno.id_alumno == alumno.id_alumno,
            PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar,
        )
        # Una fila por periodo (UNIQUE de la evaluación por periodo y del alumno por
        # evaluación); si el año tuviera más periodos que filas, se quedan los últimos.
        .order_by(col(PeriodoAcademico.numero).desc())
        .limit(FILAS_REGISTRO_VUELO)
    ).all()
    escala = escala_del_grado(db, alumno.id_grado)
    return [
        RegistroVuelo(
            corte=corte, fecha=fecha, ciclo_evaluado=ciclo, nivel_entrada=nivel_entrada,
            aciertos=registro.aciertos_prueba, total=registro.total_prueba,
            nivel_colocado=nivel_colocado, nivel_descriptivo=escala.nivel(registro.nivel_colocado),
            nivel_general=nivel_general, observacion=registro.observacion,
        )
        for corte, fecha, ciclo, nivel_entrada, registro, nivel_colocado, nivel_general in reversed(filas)
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
        .limit(FILAS_RECIENTES)
    ).all()
    mensuales = db.exec(
        select(NivelFinalMensual, NivelGeneral.nombre_nivel)
        .join(NivelGeneral, NivelGeneral.id_nivel_general == NivelFinalMensual.id_nivel_final)
        .where(NivelFinalMensual.id_alumno == id_alumno, NivelFinalMensual.id_anio_escolar == anio.id_anio_escolar)
        .order_by(col(NivelFinalMensual.mes).desc())
        .limit(FILAS_RECIENTES)
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
        .limit(FILAS_RECIENTES)
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
    "id_seccion": "Sección",
    "activo": "Estado",
    # Campo virtual de la pertenencia (CU014): la fecha desde la que rige el evento.
    CAMPO_AUDITORIA: "Fecha efectiva",
}
# Campos que guardan un id de catálogo -> (modelo, columna id).
CATALOGOS = {
    "id_grado": (Grado, "id_grado"),
    "id_programa": (Programa, "id_programa"),
    "id_colegio": (Colegio, "id_colegio"),
    "id_seccion": (Seccion, "id_seccion"),
}
ESTADOS = {"true": "Activo", "false": "Inactivo"}


def _limites_del_anio(anio: AnioEscolar) -> tuple[datetime, datetime]:
    """Instantes UTC que cubren el año escolar en hora de Lima: [inicio, fin)."""
    return limites_lima(anio.fecha_inicio, anio.fecha_fin)


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
    if campo == CAMPO_AUDITORIA:
        return fecha_legible(date.fromisoformat(valor))
    # Si el id ya no existe en el catálogo, se muestra el id.
    return nombres.get(campo, {}).get(valor, valor)


def historial(db: Session, id_alumno: int, page: int) -> Paginado[EventoHistorial]:
    """Eventos de `auditoria` del alumno en el año escolar de referencia, del más reciente
    al más antiguo. Las filas con el mismo (fecha, usuario, acción) son un solo evento, y
    la paginación es sobre los eventos (`TAMANO_PAGINA` por página), no sobre las filas.

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
