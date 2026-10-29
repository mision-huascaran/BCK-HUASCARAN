"""Registro de Vuelo (CU031, CU032).

Por alumno y PA del año, en el grupo (colegio × grado × sección × subprograma) en que
pertenecía:

- Entrada (calculada): en PA1, la letra esperada del grado (`nivel_esperado_por_grado`);
  en PA2-PA4, el nivel colocado del PA anterior del mismo alumno y año en cualquier
  grupo (REGLA PROVISIONAL V1). Sin él, "Pendiente del periodo anterior": nunca se
  rellena con la letra del grado.
- Sugerencia (calculada): el motor sobre la última rúbrica completa del alumno en ese PA,
  con las excepciones aa/Z sobre la entrada.
- Nivel del programa (calculado): `escala_nivel_descriptivo` del grado y el nivel colocado.
- El Docente registra el nivel colocado (no se puede volver a borrar), la proyección y la
  observación. Al registrar el nivel colocado se guarda con qué se decidió: la rúbrica de
  referencia y su versión, el resultado y la entrada vigente; si después la entrada
  cambia (se corrigió el PA anterior) se avisa.

Los cálculos trabajan sobre datos cargados en lote (`cargar_datos`): un número fijo de
consultas por página, no por alumno ni por PA.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.paginacion import TAMANO_PAGINA, pagina
from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.evaluacion import (
    EscalaNivelDescriptivo,
    NivelEsperadoPorGrado,
    NivelRazkids,
    NivelRubrica,
    RegistroVuelo,
    RubricaMensual,
)
from app.models.organizacion import Alumno, AlumnoPertenencia, AnioEscolar, PeriodoAcademico, Programa, Usuario
from app.schemas.asistencia import Escritura
from app.schemas.registro_vuelo import (
    AlumnoVuelo,
    Entrada,
    Letra,
    PeriodoAlumno,
    PeriodoDelAnio,
    RegistrosVueloDelGrupo,
    RegistroVueloGuardar,
    RubricaReferencia,
    SugerenciaVuelo,
)
from app.schemas.sincronizacion import ResultadoOperacion
from app.services import claves_registro
from app.services.asistencia_service import exigir_seccion
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import (
    PeriodoCalendario,
    cargar_calendarios,
    en_pa_de_su_colegio,
    rango_del_mes,
)
from app.services.clase_service import colegio_existente, grados_visibles
from app.services.escritura import Destino, MensajesEscritura, escritura
from app.services.habilitacion import habilitacion_escritura
from app.services.motor_sugerencias import BAJAR, SUBIR, TEXTO_PENDIENTE, sugerencia
from app.services.nivel_descriptivo import EscalaDescriptiva
from app.services.pertenencia import alumnos_del_grupo_en_rango
from app.services.programacion import FiltroGrupos
from app.services.reglas_provisionales import regla_decision_vigente
from app.services.rubrica_service import ultimas_rubricas_completas_del_anio
from app.services.sincronizacion import (
    MENSAJE_ACTIVIDAD_REQUERIDA,
    ContextoOperacion,
    Operacion,
    RechazoOperacion,
    procesar_operacion,
)
from app.services.usuario_service import DOCENTE, nombre_rol

MODULO = "registro_vuelo"
TABLA = "registro_vuelo"
CAMPOS = ("id_nivel_colocado", "id_nivel_proyeccion", "observacion")
CAMPOS_NIVEL = ("id_nivel_colocado", "id_nivel_proyeccion")
FUTURO, EN_CURSO, CULMINADO = "futuro", "en_curso", "culminado"
ORIGEN_GRADO, ORIGEN_ANTERIOR, ORIGEN_PENDIENTE = "grado", "periodo_anterior", "pendiente_periodo_anterior"
AVANCE_POR_PERIODO = 25

AYUDA_GRADO = "Nivel inicial según el grado del alumno."
AYUDA_ANTERIOR = "Proviene del nivel colocado en el periodo anterior."
AYUDA_TRASLADO = "Registrado antes del traslado."
TEXTO_PENDIENTE_ANTERIOR = "Pendiente del periodo anterior"
AVISO_ENTRADA_CAMBIADA = (
    "El nivel de entrada cambió por una corrección del periodo anterior. Revisa la decisión de este periodo."
)
MENSAJE_SIN_HABILITACION = "No tienes autorización vigente para registrar o modificar este periodo."
MENSAJE_SIN_ACTIVIDAD = MENSAJE_ACTIVIDAD_REQUERIDA
MENSAJE_SOLO_LECTURA = "El Supervisor consulta el Registro de Vuelo en solo lectura."
MENSAJE_PERIODO_FUTURO = "No se puede registrar un periodo académico que todavía no empieza."
MENSAJE_CONCURRENTE = (
    "El registro de este alumno y periodo fue modificado desde que lo consultaste. Revisa la versión actual antes "
    "de guardar nuevamente."
)
MENSAJE_NIVEL_INVALIDO = "Selecciona un nivel válido entre aa y Z."
MENSAJE_COLOCADO_OBLIGATORIO = "El nivel colocado ya registrado no se puede quitar; selecciona otro nivel."
MENSAJE_FUERA_DEL_GRUPO = "El estudiante no pertenecía a este grupo en el periodo seleccionado."
MENSAJE_SIN_ALUMNOS = "No hay alumnos que correspondan a este grupo en el año seleccionado."
MENSAJE_PERIODO_INEXISTENTE = "El periodo académico no existe."
MENSAJE_PROGRAMA_INEXISTENTE = "El subprograma no existe."
MENSAJE_ANIO_NO_ENCONTRADO = "El año escolar no existe."

MENSAJES_ESCRITURA = MensajesEscritura(MENSAJE_SOLO_LECTURA, MENSAJE_SIN_HABILITACION, MENSAJE_SIN_ACTIVIDAD)

_registro_vuelo = RegistroVuelo.__table__


# ── Contexto y datos en lote ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Contexto:
    """El grupo de la fila: colegio, grado, sección y subprograma."""

    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int

    @property
    def grupo(self) -> FiltroGrupos:
        return FiltroGrupos(self.id_colegio, self.id_grado, self.id_seccion)

    def de_la_fila(self, fila: RegistroVuelo) -> bool:
        return (fila.id_colegio, fila.id_grado, fila.id_seccion, fila.id_programa) == (
            self.id_colegio, self.id_grado, self.id_seccion, self.id_programa,
        )

    @classmethod
    def de(cls, origen) -> "Contexto":
        return cls(origen.id_colegio, origen.id_grado, origen.id_seccion, origen.id_programa)


@dataclass
class DatosVuelo:
    """Lo necesario para resolver los PA de varios alumnos de un año sin más consultas."""

    anio: AnioEscolar
    periodos: list[PeriodoAcademico]
    letras: dict[int, str]
    entrada_por_grado: dict[int, int]
    escalas: dict[int, EscalaDescriptiva]
    niveles_rubrica: dict[int, NivelRubrica]
    programas: dict[int, str]
    registros: dict[int, list[RegistroVuelo]] = field(default_factory=dict)
    # (alumno, PA) -> su última rúbrica completa.
    rubricas: dict[tuple[int, int], RubricaMensual] = field(default_factory=dict)

    def anterior(self, periodo: PeriodoAcademico) -> Optional[PeriodoAcademico]:
        return next((p for p in self.periodos if p.numero == periodo.numero - 1), None)

    def letra(self, id_nivel: Optional[int]) -> Optional[Letra]:
        return Letra(id=id_nivel, letra=self.letras[id_nivel]) if id_nivel in self.letras else None


def escalas_por_grado(db: Session) -> dict[int, EscalaDescriptiva]:
    """{grado: escala de nivel descriptivo}, todas en una consulta."""
    tramos: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for fila in db.exec(select(EscalaNivelDescriptivo).order_by(EscalaNivelDescriptivo.id_nivel_rk_desde)).all():
        tramos[fila.id_grado].append((fila.id_nivel_rk_desde, fila.nivel))
    return {grado: EscalaDescriptiva(tuple(t)) for grado, t in tramos.items()}


def cargar_datos(db: Session, anio: AnioEscolar, ids_alumno: list[int]) -> DatosVuelo:
    """Catálogos, PA del año, decisiones y rúbricas completas de esos alumnos."""
    registros: dict[int, list[RegistroVuelo]] = defaultdict(list)
    for fila in db.exec(select(RegistroVuelo).where(
        col(RegistroVuelo.id_alumno).in_(ids_alumno), RegistroVuelo.id_anio_escolar == anio.id_anio_escolar,
    )).all():
        registros[fila.id_alumno].append(fila)
    return DatosVuelo(
        anio=anio,
        periodos=list(db.exec(select(PeriodoAcademico).where(
            PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar
        ).order_by(PeriodoAcademico.numero)).all()),
        letras=dict(db.exec(select(NivelRazkids.id_nivel_rk, NivelRazkids.letra)).all()),
        entrada_por_grado=dict(db.exec(select(NivelEsperadoPorGrado.id_grado, NivelEsperadoPorGrado.id_nivel_rk_esperado)).all()),
        escalas=escalas_por_grado(db),
        niveles_rubrica={n.id_nivel_rubrica: n for n in db.exec(select(NivelRubrica)).all()},
        programas=dict(db.exec(select(Programa.id_programa, Programa.nombre)).all()),
        registros=registros,
        rubricas=ultimas_rubricas_completas_del_anio(db, ids_alumno, anio.id_anio_escolar),
    )


# ── Cálculos ────────────────────────────────────────────────────────────────────────

def decision_del_periodo(registros: list[RegistroVuelo], id_periodo: int) -> Optional[RegistroVuelo]:
    """La decisión vigente del alumno en el PA, en cualquier grupo (V1)."""
    return regla_decision_vigente([
        (r.fecha_registro, r if r.id_nivel_colocado is not None else None)
        for r in registros if r.id_periodo_academico == id_periodo
    ])


def nivel_programa(escalas: dict[int, EscalaDescriptiva], id_grado: int, id_nivel_colocado: Optional[int]) -> Optional[str]:
    """Nivel del programa (Inicio ... Avanzado) de un nivel colocado en la escala del grado."""
    return escalas.get(id_grado, EscalaDescriptiva(())).nivel(id_nivel_colocado)


def niveles_programa_del_periodo(db: Session, ids_alumno: list[int], id_periodo: int) -> dict[int, str]:
    """{alumno: nivel del programa de su decisión vigente en el PA (V1)}, con el grado del
    registro de esa decisión. Sin decisión con nivel colocado, el alumno no está."""
    if not ids_alumno:
        return {}
    registros: dict[int, list[RegistroVuelo]] = defaultdict(list)
    for fila in db.exec(select(RegistroVuelo).where(
        col(RegistroVuelo.id_alumno).in_(ids_alumno), RegistroVuelo.id_periodo_academico == id_periodo,
    )).all():
        registros[fila.id_alumno].append(fila)
    escalas = escalas_por_grado(db)
    niveles = {}
    for id_alumno, filas in registros.items():
        decision = decision_del_periodo(filas, id_periodo)
        nivel = nivel_programa(escalas, decision.id_grado, decision.id_nivel_colocado) if decision else None
        if nivel is not None:
            niveles[id_alumno] = nivel
    return niveles


def colocado_del_periodo(registros: list[RegistroVuelo], id_periodo: int) -> Optional[int]:
    """El nivel colocado del alumno en el PA, en cualquier grupo (V1)."""
    decision = decision_del_periodo(registros, id_periodo)
    return decision.id_nivel_colocado if decision else None


def decision_de_entrada(datos: DatosVuelo, id_alumno: int, periodo: PeriodoAcademico) -> Optional[RegistroVuelo]:
    """La decisión del PA inmediatamente anterior del mismo año que da la entrada (V1), en
    cualquier grupo o colegio; None en el PA1 o si está pendiente. `datos` solo tiene los
    registros y PA del año consultado."""
    anterior = datos.anterior(periodo)
    if anterior is None:
        return None
    return decision_del_periodo(datos.registros.get(id_alumno, []), anterior.id_periodo_academico)


def entrada_de(datos: DatosVuelo, id_alumno: int, periodo: PeriodoAcademico, id_grado: int) -> tuple[Optional[int], str]:
    """(id del nivel de entrada o None, origen)."""
    if datos.anterior(periodo) is None:
        return datos.entrada_por_grado.get(id_grado), ORIGEN_GRADO
    decision = decision_de_entrada(datos, id_alumno, periodo)
    return (decision.id_nivel_colocado, ORIGEN_ANTERIOR) if decision is not None else (None, ORIGEN_PENDIENTE)


def _entrada(datos: DatosVuelo, id_nivel: Optional[int], origen: str, de_otro_colegio: bool = False) -> Entrada:
    textos = {ORIGEN_GRADO: AYUDA_GRADO, ORIGEN_ANTERIOR: AYUDA_ANTERIOR, ORIGEN_PENDIENTE: TEXTO_PENDIENTE_ANTERIOR}
    texto = f"{textos[origen]} {AYUDA_TRASLADO}" if de_otro_colegio else textos[origen]
    return Entrada(nivel=datos.letra(id_nivel), origen=origen, texto=texto, de_otro_colegio=de_otro_colegio)


def _mover(datos: DatosVuelo, id_nivel: int, accion: Optional[str]) -> Optional[Letra]:
    """La letra a la que lleva la acción desde la entrada (ids ascendentes = niveles)."""
    if accion is None:
        return None
    paso = {BAJAR: -1, SUBIR: 1}.get(accion, 0)
    destino = min(max(id_nivel + paso, min(datos.letras)), max(datos.letras))
    return datos.letra(destino)


def _rubrica_referencia(datos: DatosVuelo, rubrica: RubricaMensual) -> RubricaReferencia:
    fluidez = datos.niveles_rubrica[rubrica.id_nivel_fluidez]
    comprension = datos.niveles_rubrica[rubrica.id_nivel_comprension]
    primer_dia = rango_del_mes(datos.anio, rubrica.mes)[0]
    return RubricaReferencia(
        id=rubrica.id, mes=f"{primer_dia.year}-{rubrica.mes:02d}", fluidez=fluidez.nombre_nivel,
        comprension=comprension.nombre_nivel, resultado_decimas=4 * fluidez.valor + 6 * comprension.valor,
    )


def sugerencia_de(datos: DatosVuelo, id_alumno: int, id_periodo: int, id_entrada: Optional[int]) -> SugerenciaVuelo:
    """El motor sobre la última rúbrica completa del PA, con las excepciones sobre la
    entrada; sin rúbrica completa o sin entrada, "Pendiente de evaluar"."""
    rubrica = datos.rubricas.get((id_alumno, id_periodo))
    if rubrica is None:
        return SugerenciaVuelo(texto=TEXTO_PENDIENTE)
    referencia = _rubrica_referencia(datos, rubrica)
    if id_entrada is None:
        return SugerenciaVuelo(texto=TEXTO_PENDIENTE, resultado_decimas=referencia.resultado_decimas, rubrica=referencia)
    resultado = sugerencia(
        datos.niveles_rubrica[rubrica.id_nivel_fluidez].valor, datos.niveles_rubrica[rubrica.id_nivel_comprension].valor,
        datos.programas[rubrica.id_programa], datos.letras[id_entrada],
    )
    return SugerenciaVuelo(
        accion=resultado.accion, nivel=_mover(datos, id_entrada, resultado.accion), texto=resultado.texto,
        resultado_decimas=resultado.resultado_decimas, excepcion=resultado.excepcion, rubrica=referencia,
    )


def avance_anual(datos: DatosVuelo, id_alumno: int) -> int:
    registros = datos.registros.get(id_alumno, [])
    con_nivel = [p for p in datos.periodos if colocado_del_periodo(registros, p.id_periodo_academico) is not None]
    return AVANCE_POR_PERIODO * len(con_nivel)


def _fila_del_contexto(datos: DatosVuelo, contexto: Contexto, id_alumno: int, id_periodo: int) -> Optional[RegistroVuelo]:
    return next(
        (r for r in datos.registros.get(id_alumno, [])
         if r.id_periodo_academico == id_periodo and contexto.de_la_fila(r)),
        None,
    )


def resolver_periodo(datos: DatosVuelo, contexto: Contexto, id_alumno: int, periodo: PeriodoAcademico) -> PeriodoAlumno:
    """La fila del alumno en el PA y el grupo, con lo calculado."""
    fila = _fila_del_contexto(datos, contexto, id_alumno, periodo.id_periodo_academico)
    id_entrada, origen = entrada_de(datos, id_alumno, periodo, contexto.id_grado)
    origen_entrada = decision_de_entrada(datos, id_alumno, periodo)
    de_otro_colegio = origen_entrada is not None and origen_entrada.id_colegio != contexto.id_colegio
    colocado = fila.id_nivel_colocado if fila else None
    cambio = colocado is not None and fila.id_nivel_entrada_al_decidir != id_entrada
    return PeriodoAlumno(
        numero=periodo.numero, id_periodo_academico=periodo.id_periodo_academico,
        id_registro=fila.id if fila else None, version=fila.version if fila else None,
        entrada=_entrada(datos, id_entrada, origen, de_otro_colegio),
        sugerencia=sugerencia_de(datos, id_alumno, periodo.id_periodo_academico, id_entrada),
        nivel_colocado=datos.letra(colocado), nivel_programa=nivel_programa(datos.escalas, contexto.id_grado, colocado),
        proyeccion=datos.letra(fila.id_nivel_proyeccion if fila else None),
        observacion=fila.observacion if fila else None,
        aviso_entrada_cambio=cambio, aviso=AVISO_ENTRADA_CAMBIADA if cambio else None, incompleto=colocado is None,
    )


# ── Consulta (CU031) ────────────────────────────────────────────────────────────────

def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def _validar_contexto(db: Session, usuario: Usuario, contexto: Contexto) -> None:
    colegio_existente(db, contexto.id_colegio)
    grados_visibles(db, usuario, contexto.id_colegio, contexto.id_grado)
    exigir_seccion(db, contexto.grupo)
    if db.get(Programa, contexto.id_programa) is None:
        raise _no_procesable(MENSAJE_PROGRAMA_INEXISTENTE, "programa_inexistente")


def _anio(db: Session, id_anio_escolar: int) -> AnioEscolar:
    anio = db.get(AnioEscolar, id_anio_escolar)
    if anio is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, MENSAJE_ANIO_NO_ENCONTRADO, motivo="anio_no_encontrado")
    return anio


def estado_del_periodo(pa: Optional[PeriodoCalendario], hoy: date) -> Optional[str]:
    if pa is None:
        return None
    if pa.fecha_inicio > hoy:
        return FUTURO
    return EN_CURSO if pa.fecha_fin >= hoy else CULMINADO


def _escritura_del_periodo(
    db: Session, usuario: Usuario, contexto: Contexto, pa: Optional[PeriodoCalendario], estado: Optional[str]
) -> Escritura:
    es_docente = nombre_rol(db, usuario.id_rol) == DOCENTE
    if es_docente and estado == FUTURO:
        return Escritura(permitida=False, motivo=MENSAJE_PERIODO_FUTURO)
    if pa is None:
        return Escritura(permitida=False, motivo=MENSAJE_SIN_HABILITACION if es_docente else MENSAJE_SOLO_LECTURA)
    destino = Destino(MODULO, contexto.id_colegio, contexto.id_grado, pa)
    return escritura(db, usuario, destino, MENSAJES_ESCRITURA)


def _periodos_del_anio(db: Session, usuario: Usuario, contexto: Contexto, datos: DatosVuelo) -> list[PeriodoDelAnio]:
    """Los PA con las fechas del calendario del colegio, su estado y si se puede escribir
    en cada uno (la autorización de un PA no habilita los demás)."""
    calendario = cargar_calendarios(db, datos.anio.id_anio_escolar, ids_colegio=[contexto.id_colegio]).aplicable(
        contexto.id_colegio, datos.anio.id_anio_escolar
    )
    hoy = hoy_lima()
    salida = []
    for periodo in datos.periodos:
        pa = calendario.pa(periodo.numero) if calendario is not None else None
        estado = estado_del_periodo(pa, hoy)
        salida.append(PeriodoDelAnio(
            numero=periodo.numero, id_periodo_academico=periodo.id_periodo_academico, nombre=f"PA{periodo.numero}",
            fecha_inicio=pa.fecha_inicio.isoformat() if pa else None, fecha_fin=pa.fecha_fin.isoformat() if pa else None,
            estado=estado, escritura=_escritura_del_periodo(db, usuario, contexto, pa, estado),
        ))
    return salida


def _alumno_vuelo(datos: DatosVuelo, contexto: Contexto, alumno: Alumno) -> AlumnoVuelo:
    return AlumnoVuelo(
        id_alumno=alumno.id_alumno, nombre=f"{alumno.nombres} {alumno.apellidos}", id_programa=contexto.id_programa,
        inactivo=not alumno.activo, avance_anual=avance_anual(datos, alumno.id_alumno),
        periodos=[resolver_periodo(datos, contexto, alumno.id_alumno, p) for p in datos.periodos],
    )


def listar(db: Session, usuario: Usuario, contexto: Contexto, id_anio_escolar: int, page: int) -> RegistrosVueloDelGrupo:
    """Una fila por alumno que perteneció al grupo en algún momento del año (incluidos los
    hoy inactivos), por apellidos, en páginas de 40, con los 4 PA resueltos."""
    _validar_contexto(db, usuario, contexto)
    anio = _anio(db, id_anio_escolar)
    alumnos = alumnos_del_grupo_en_rango(
        db, contexto.id_colegio, contexto.id_grado, contexto.id_seccion, anio.fecha_inicio, anio.fecha_fin,
        contexto.id_programa,
    )
    en_pagina = alumnos[(page - 1) * TAMANO_PAGINA: page * TAMANO_PAGINA]
    datos = cargar_datos(db, anio, [a.id_alumno for a in en_pagina])
    base = pagina([_alumno_vuelo(datos, contexto, a) for a in en_pagina], len(alumnos), page)
    return RegistrosVueloDelGrupo(
        **base.model_dump(), periodos=_periodos_del_anio(db, usuario, contexto, datos),
        aviso=None if alumnos else MENSAJE_SIN_ALUMNOS,
    )


# ── Registro (CU031, CU032) ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Captura:
    usuario: Usuario
    contexto: ContextoOperacion
    id_permiso: Optional[int]

    def auditoria(self, ahora: datetime) -> dict[str, Any]:
        return {
            "id_usuario": self.usuario.id_usuario, "fecha": ahora, "id_actividad": self.contexto.id_actividad,
            "id_permiso": self.id_permiso,
        }

    def firma(self) -> dict[str, Any]:
        return {
            "id_docente": self.usuario.id_docente, "id_actividad": self.contexto.id_actividad,
            "id_permiso": self.id_permiso, "fecha_registro": self.contexto.en,
        }


def _pa_del_item(db: Session, item: RegistroVueloGuardar, en: datetime) -> tuple[PeriodoAcademico, PeriodoCalendario]:
    """El PA del ítem y sus fechas en el calendario del colegio; rechaza uno inexistente
    o que todavía no empieza a la hora real de la operación."""
    periodo = db.get(PeriodoAcademico, item.id_periodo_academico)
    if periodo is None:
        raise RechazoOperacion("periodo_inexistente", MENSAJE_PERIODO_INEXISTENTE)
    calendario = cargar_calendarios(db, periodo.id_anio_escolar, ids_colegio=[item.id_colegio]).aplicable(
        item.id_colegio, periodo.id_anio_escolar
    )
    pa = calendario.pa(periodo.numero) if calendario is not None else None
    if pa is None or pa.fecha_inicio > fecha_lima(en):
        raise RechazoOperacion("periodo_futuro", MENSAJE_PERIODO_FUTURO)
    return periodo, pa


def _pertenecia(db: Session, id_alumno: int, contexto: Contexto, pa: PeriodoCalendario) -> bool:
    """Si el alumno perteneció al grupo algún día del PA."""
    return db.exec(
        select(func.count()).select_from(AlumnoPertenencia).where(
            AlumnoPertenencia.id_alumno == id_alumno,
            AlumnoPertenencia.id_colegio == contexto.id_colegio,
            AlumnoPertenencia.id_grado == contexto.id_grado,
            AlumnoPertenencia.id_seccion == contexto.id_seccion,
            AlumnoPertenencia.id_programa == contexto.id_programa,
            AlumnoPertenencia.desde <= pa.fecha_fin,
            (col(AlumnoPertenencia.hasta).is_(None)) | (col(AlumnoPertenencia.hasta) > pa.fecha_inicio),
        )
    ).one() > 0


def _validar_niveles(db: Session, item: RegistroVueloGuardar) -> None:
    for campo in CAMPOS_NIVEL:
        valor = getattr(item, campo)
        if campo in item.model_fields_set and valor is not None and db.get(NivelRazkids, valor) is None:
            raise RechazoOperacion("nivel_invalido", MENSAJE_NIVEL_INVALIDO, {"campo": campo})


def _cambios_de(fila: Optional[RegistroVuelo], item: RegistroVueloGuardar) -> dict[str, tuple]:
    cambios = {}
    for campo in CAMPOS:
        if campo not in item.model_fields_set:
            continue
        nuevo = getattr(item, campo)
        nuevo = (nuevo.strip() or None) if isinstance(nuevo, str) else nuevo
        anterior = getattr(fila, campo) if fila else None
        if nuevo != anterior:
            cambios[campo] = (anterior, nuevo)
    if "id_nivel_colocado" in cambios and cambios["id_nivel_colocado"][1] is None:
        raise RechazoOperacion("nivel_colocado_obligatorio", MENSAJE_COLOCADO_OBLIGATORIO)
    return cambios


def _decision(datos: DatosVuelo, id_alumno: int, periodo: PeriodoAcademico, id_grado: int) -> dict[str, Any]:
    """Con qué se decide el nivel colocado: la entrada vigente y la rúbrica de la sugerencia."""
    id_entrada, _ = entrada_de(datos, id_alumno, periodo, id_grado)
    origen_entrada = decision_de_entrada(datos, id_alumno, periodo)
    rubrica = datos.rubricas.get((id_alumno, periodo.id_periodo_academico))
    sugerida = sugerencia_de(datos, id_alumno, periodo.id_periodo_academico, id_entrada)
    return {
        "id_nivel_entrada_al_decidir": id_entrada,
        "id_registro_entrada": origen_entrada.id if origen_entrada else None,
        "id_rubrica_referencia": rubrica.id if rubrica else None,
        "rubrica_version": rubrica.version if rubrica else None,
        "resultado_decimas": sugerida.resultado_decimas,
    }


def _fila_bloqueada(db: Session, item: RegistroVueloGuardar, id_anio: int) -> Optional[RegistroVuelo]:
    return db.exec(
        select(RegistroVuelo)
        .where(
            RegistroVuelo.id_alumno == item.id_alumno, RegistroVuelo.id_anio_escolar == id_anio,
            RegistroVuelo.id_periodo_academico == item.id_periodo_academico,
            RegistroVuelo.id_colegio == item.id_colegio, RegistroVuelo.id_grado == item.id_grado,
            RegistroVuelo.id_seccion == item.id_seccion, RegistroVuelo.id_programa == item.id_programa,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _insertar(db: Session, item: RegistroVueloGuardar, id_anio: int, valores: dict, captura: _Captura) -> None:
    ahora = ahora_utc()
    id_nuevo = db.execute(
        insert_con_conflicto(db, _registro_vuelo)
        .values(
            id_alumno=item.id_alumno, id_anio_escolar=id_anio, id_periodo_academico=item.id_periodo_academico,
            **Contexto.de(item).__dict__, **valores, **captura.firma(), version=1,
            creado_por=captura.usuario.id_usuario, creado_en=ahora,
        )
        .on_conflict_do_nothing()
        .returning(_registro_vuelo.c.id)
    ).scalar_one_or_none()
    if id_nuevo is None:
        raise RechazoOperacion("modificacion_concurrente", MENSAJE_CONCURRENTE)
    registrar_auditoria(db, tabla=TABLA, id_registro=id_nuevo, accion=CREAR, **captura.auditoria(ahora))


def _actualizar(db: Session, fila: RegistroVuelo, cambios: dict, valores: dict, captura: _Captura) -> None:
    ahora = ahora_utc()
    registrar_auditoria(db, tabla=TABLA, id_registro=fila.id, accion=EDITAR, cambios=cambios, **captura.auditoria(ahora))
    for campo, valor in {**valores, **captura.firma()}.items():
        setattr(fila, campo, valor)
    fila.version, fila.modificado_por, fila.modificado_en = fila.version + 1, captura.usuario.id_usuario, ahora
    db.add(fila)
    db.flush()


def _resuelto(db: Session, anio: AnioEscolar, item: RegistroVueloGuardar, periodo: PeriodoAcademico) -> dict[str, Any]:
    datos = cargar_datos(db, anio, [item.id_alumno])
    return resolver_periodo(datos, Contexto.de(item), item.id_alumno, periodo).model_dump(mode="json")


def _guardar(db: Session, item: RegistroVueloGuardar, periodo: PeriodoAcademico, captura: _Captura) -> None:
    """Crea o actualiza la fila; sin cambios no escribe. La versión solo cuenta si cambia la fila."""
    anio = db.get(AnioEscolar, periodo.id_anio_escolar)
    fila = _fila_bloqueada(db, item, anio.id_anio_escolar)
    cambios = _cambios_de(fila, item)
    if not cambios:
        return
    if item.version != (fila.version if fila else None):
        raise RechazoOperacion("modificacion_concurrente", MENSAJE_CONCURRENTE, _resuelto(db, anio, item, periodo))
    valores = {campo: nuevo for campo, (_, nuevo) in cambios.items()}
    if "id_nivel_colocado" in cambios:
        valores.update(_decision(cargar_datos(db, anio, [item.id_alumno]), item.id_alumno, periodo, item.id_grado))
    if fila is None:
        _insertar(db, item, anio.id_anio_escolar, valores, captura)
    else:
        _actualizar(db, fila, cambios, valores, captura)


def _aplicador(db: Session, usuario: Usuario, item: RegistroVueloGuardar):
    def aplicar(contexto_op: ContextoOperacion) -> dict[str, Any]:
        contexto = Contexto.de(item)
        _validar_contexto(db, usuario, contexto)
        periodo, pa = _pa_del_item(db, item, contexto_op.en)
        habilitacion = habilitacion_escritura(
            db, usuario, item.id_colegio, item.id_grado, periodo.id_periodo_academico, MODULO, en=contexto_op.en
        )
        if not habilitacion.permitido:
            raise RechazoOperacion("sin_habilitacion", MENSAJE_SIN_HABILITACION)
        if not _pertenecia(db, item.id_alumno, contexto, pa):
            raise RechazoOperacion("alumno_fuera_del_grupo", MENSAJE_FUERA_DEL_GRUPO)
        _validar_niveles(db, item)
        _guardar(db, item, periodo, _Captura(usuario, contexto_op, habilitacion.id_permiso))
        return _resuelto(db, db.get(AnioEscolar, periodo.id_anio_escolar), item, periodo)

    return aplicar


def guardar(db: Session, usuario: Usuario, items: list[RegistroVueloGuardar]) -> list[ResultadoOperacion]:
    """Registra o corrige decisiones (CU031, CU032). Cada ítem es una operación con su
    resultado; un lote puede tener aceptadas y rechazadas."""
    resultados = [
        procesar_operacion(
            db, usuario, item, _aplicador(db, usuario, item),
            Operacion(MODULO, TABLA, _id_entidad(item), MENSAJE_SIN_ACTIVIDAD, _clave(db, item)),
        )
        for item in items
    ]
    db.commit()
    return resultados


def _clave(db: Session, item: RegistroVueloGuardar) -> Optional[str]:
    """Clave del registro (alumno × PA y grupo); None si el PA no existe (se rechaza)."""
    periodo = db.get(PeriodoAcademico, item.id_periodo_academico)
    if periodo is None:
        return None
    return claves_registro.registro_vuelo(
        item.id_colegio, item.id_grado, item.id_seccion, item.id_programa, periodo.id_anio_escolar,
        item.id_periodo_academico, item.id_alumno,
    )


def _id_entidad(item: RegistroVueloGuardar) -> str:
    contexto = Contexto.de(item)
    return f"{item.id_alumno}:{item.id_periodo_academico}:" + "-".join(str(v) for v in contexto.__dict__.values())


# ── Para otros módulos ──────────────────────────────────────────────────────────────

def _decididas_del_pa_vigente(pa_por_colegio: dict) -> tuple:
    return (
        col(RegistroVuelo.id_nivel_colocado).is_not(None),
        en_pa_de_su_colegio(RegistroVuelo.id_colegio, RegistroVuelo.id_periodo_academico, pa_por_colegio),
    )


def decisiones_confirmadas(db: Session, pa_por_colegio: dict) -> int:
    """Salud del Sistema (CU012): alumnos × PA vigente de su colegio con nivel colocado."""
    unidades = (
        select(RegistroVuelo.id_alumno, RegistroVuelo.id_periodo_academico)
        .where(*_decididas_del_pa_vigente(pa_por_colegio))
        .distinct()
        .subquery()
    )
    return db.exec(select(func.count()).select_from(unidades)).one()


def colegios_con_decisiones(db: Session, pa_por_colegio: dict) -> set[int]:
    """CU011 (Incumplimiento de Sede): colegios con algún nivel colocado en su PA vigente."""
    return set(db.exec(
        select(RegistroVuelo.id_colegio).where(*_decididas_del_pa_vigente(pa_por_colegio)).distinct()
    ).all())
