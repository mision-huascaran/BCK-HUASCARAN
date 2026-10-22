"""Rúbrica mensual (CU029, CU030).

Una evaluación por alumno y mes, en el grupo (colegio × grado × sección × subprograma)
que le corresponde ese mes, con Fluidez y Comprensión en códigos 0-4 (null = "Sin
evaluar"). Se puede guardar incompleta.

- El mes: no se evalúan meses futuros ni meses sin días lectivos de un PA en el
  calendario del colegio (422 `mes_futuro`, `mes_sin_actividad`). Su PA es el de su
  último día lectivo (REGLA PROVISIONAL R1); con él se habilita la escritura (módulo
  `rubrica`) y se guarda la evaluación.
- Los alumnos del mes: los que pertenecieron al grupo algún día del mes y, si cambiaron
  de grupo, solo en el grupo del último día lectivo del mes en que estuvieron (REGLA
  PROVISIONAL R2). Los hoy inactivos también salen (`inactivo: true`).
- Lectura: el Docente en su alcance de lectura; el Supervisor, de todo y en solo lectura.
  Escritura (solo Docente): cada evaluación es una operación (`procesar_operacion`) con
  actividad y habilitación a la hora real, y control de versión: una `version` distinta
  de la guardada se rechaza (`modificacion_concurrente`) sin sobrescribir.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from fastapi import status
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.ciclo import calcular_ciclo
from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.paginacion import TAMANO_PAGINA, pagina
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.evaluacion import DescriptorRubrica, NivelRubrica, RubricaMensual
from app.models.organizacion import Alumno, AlumnoPertenencia, AnioEscolar, CicloEbr, Grado, Programa, Usuario
from app.schemas.rubrica import Criterio, RubricaAlumno, RubricaGuardar, RubricasDelMes
from app.schemas.sincronizacion import ResultadoOperacion
from app.services.asistencia_service import exigir_seccion
from app.services.auditoria import CREAR, EDITAR, registrar_auditoria
from app.services.calendario import PeriodoCalendario, cargar_calendarios, en_pa_de_su_colegio, rango_del_mes
from app.services.clase_service import colegio_existente, grados_visibles
from app.services.escritura import Destino, MensajesEscritura, escritura
from app.services.habilitacion import habilitacion_escritura
from app.services.motor_sugerencias import COMPRENSION, FLUIDEZ, resultado_decimas
from app.services.pertenencia import alumnos_del_grupo_en_rango
from app.services.programacion import FiltroGrupos
from app.services.reglas_provisionales import regla_grupo_del_mes, regla_mes_a_pa
from app.services.sincronizacion import ContextoOperacion, Operacion, RechazoOperacion, procesar_operacion

MODULO = "rubrica"
TABLA = "rubrica_mensual"
# Campo del request -> (columna, dimensión).
DIMENSIONES = {"fluidez": ("id_nivel_fluidez", FLUIDEZ), "comprension": ("id_nivel_comprension", COMPRENSION)}

PENDIENTE, INCOMPLETA, COMPLETADA = "pendiente", "incompleta", "completada"

MENSAJE_MES_FUTURO = "No es posible evaluar meses futuros."
MENSAJE_MES_SIN_ACTIVIDAD = (
    "El mes seleccionado no tiene días lectivos de un periodo académico en el calendario del colegio."
)
MENSAJE_SIN_ALUMNOS = "No hay alumnos que corresponda evaluar en este grupo y mes."
MENSAJE_VALOR_NO_PERMITIDO = "Selecciona una valoración permitida para esta dimensión."
MENSAJE_SIN_HABILITACION = (
    "No tienes una habilitación vigente para registrar o modificar las rúbricas del grupo y mes seleccionados."
)
MENSAJE_SIN_ACTIVIDAD = "Debes iniciar una actividad de trabajo para registrar o modificar las rúbricas."
MENSAJE_CONCURRENTE = (
    "Esta evaluación fue modificada desde que la consultaste. Revisa la versión actual antes de guardar nuevamente."
)
MENSAJE_FUERA_DEL_GRUPO = "El estudiante no pertenecía a este grupo en el mes seleccionado."
MENSAJE_SOLO_LECTURA = "El Supervisor consulta las rúbricas en solo lectura."
MENSAJE_PROGRAMA_INEXISTENTE = "El subprograma no existe."
MENSAJE_CICLO_INEXISTENTE = "El ciclo no existe."
MENSAJE_ANIO_NO_ENCONTRADO = "El año escolar no existe."

MENSAJES_ESCRITURA = MensajesEscritura(MENSAJE_SOLO_LECTURA, MENSAJE_SIN_HABILITACION, MENSAJE_SIN_ACTIVIDAD)

_rubrica = RubricaMensual.__table__


# ── Grupo y mes ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GrupoMes:
    id_colegio: int
    id_grado: int
    id_seccion: int
    id_programa: int
    id_anio_escolar: int
    mes: int

    @property
    def grupo(self) -> FiltroGrupos:
        return FiltroGrupos(self.id_colegio, self.id_grado, self.id_seccion)

    @property
    def clave_grupo(self) -> tuple[int, int, int, int]:
        return self.id_colegio, self.id_grado, self.id_seccion, self.id_programa

    def columnas(self) -> dict[str, int]:
        return dict(self.__dict__)

    @classmethod
    def de(cls, origen) -> "GrupoMes":
        return cls(**{campo: getattr(origen, campo) for campo in cls.__dataclass_fields__})


@dataclass(frozen=True)
class MesEvaluado:
    desde: date
    hasta: date
    dias_lectivos: list[date]
    periodo: PeriodoCalendario


def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def mes_evaluado(db: Session, grupo: GrupoMes) -> MesEvaluado:
    """El mes con sus días lectivos y su PA (R1). 404 `anio_no_encontrado`; 422
    `mes_futuro` y `mes_sin_actividad`."""
    anio = db.get(AnioEscolar, grupo.id_anio_escolar)
    if anio is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, MENSAJE_ANIO_NO_ENCONTRADO, motivo="anio_no_encontrado")
    rango = rango_del_mes(anio, grupo.mes)
    if rango is not None and rango[0] > hoy_lima():
        raise _no_procesable(MENSAJE_MES_FUTURO, "mes_futuro")
    calendarios = cargar_calendarios(db, ids_colegio=[grupo.id_colegio])
    dias = calendarios.dias_lectivos(grupo.id_colegio, *rango) if rango is not None else []
    periodo = regla_mes_a_pa(dias, lambda dia: calendarios.pa_de(grupo.id_colegio, dia))
    if periodo is None:
        raise _no_procesable(MENSAJE_MES_SIN_ACTIVIDAD, "mes_sin_actividad")
    return MesEvaluado(rango[0], rango[1], dias, periodo)


def _validar_grupo(db: Session, usuario: Usuario, grupo: GrupoMes) -> Programa:
    colegio_existente(db, grupo.id_colegio)
    grados_visibles(db, usuario, grupo.id_colegio, grupo.id_grado)
    exigir_seccion(db, grupo.grupo)
    programa = db.get(Programa, grupo.id_programa)
    if programa is None:
        raise _no_procesable(MENSAJE_PROGRAMA_INEXISTENTE, "programa_inexistente")
    return programa


def _intervalos(db: Session, ids_alumno: list[int], mes: MesEvaluado) -> dict[int, list[tuple]]:
    """Intervalos de pertenencia de los alumnos que tocan el mes, con su grupo."""
    filas = db.exec(
        select(AlumnoPertenencia).where(
            col(AlumnoPertenencia.id_alumno).in_(ids_alumno),
            AlumnoPertenencia.desde <= mes.hasta,
        )
    ).all()
    por_alumno: dict[int, list[tuple]] = {i: [] for i in ids_alumno}
    for p in filas:
        if p.hasta is None or p.hasta > mes.desde:
            grupo = (p.id_colegio, p.id_grado, p.id_seccion, p.id_programa)
            por_alumno[p.id_alumno].append((p.desde, p.hasta, grupo))
    return por_alumno


def alumnos_del_mes(db: Session, grupo: GrupoMes, mes: MesEvaluado) -> list[Alumno]:
    """Los alumnos que corresponde evaluar en el grupo ese mes (R2), por apellidos."""
    candidatos = alumnos_del_grupo_en_rango(
        db, grupo.id_colegio, grupo.id_grado, grupo.id_seccion, mes.desde, mes.hasta, grupo.id_programa
    )
    intervalos = _intervalos(db, [a.id_alumno for a in candidatos], mes)
    return [
        a for a in candidatos
        if regla_grupo_del_mes(intervalos[a.id_alumno], mes.dias_lectivos) == grupo.clave_grupo
    ]


def _pertenece(db: Session, id_alumno: int, grupo: GrupoMes, mes: MesEvaluado) -> bool:
    return regla_grupo_del_mes(_intervalos(db, [id_alumno], mes)[id_alumno], mes.dias_lectivos) == grupo.clave_grupo


def ciclo_de(db: Session, programa: Programa, id_grado: int) -> str:
    grado = db.get(Grado, id_grado)
    return calcular_ciclo(programa.nombre, db.get(CicloEbr, grado.id_ciclo).nombre)


# ── Valores y estado ────────────────────────────────────────────────────────────────

def estado_de(fluidez: Optional[int], comprension: Optional[int]) -> str:
    """`pendiente` (ninguna dimensión), `incompleta` (una) o `completada` (las dos)."""
    evaluadas = (fluidez is not None) + (comprension is not None)
    return (PENDIENTE, INCOMPLETA, COMPLETADA)[evaluadas]


def _niveles(db: Session, id_programa: int) -> dict[tuple[str, int], int]:
    """{(dimensión, valor): id_nivel_rubrica} del subprograma."""
    return {
        (n.dimension, n.valor): n.id_nivel_rubrica
        for n in db.exec(select(NivelRubrica).where(NivelRubrica.id_programa == id_programa)).all()
    }


def _valores(db: Session) -> dict[int, int]:
    """{id_nivel_rubrica: valor}."""
    return dict(db.exec(select(NivelRubrica.id_nivel_rubrica, NivelRubrica.valor)).all())


def _item(alumno: Alumno, fila: Optional[RubricaMensual], valores: dict[int, int]) -> RubricaAlumno:
    fluidez = valores.get(fila.id_nivel_fluidez) if fila else None
    comprension = valores.get(fila.id_nivel_comprension) if fila else None
    completa = fluidez is not None and comprension is not None
    return RubricaAlumno(
        id_alumno=alumno.id_alumno, nombre=f"{alumno.nombres} {alumno.apellidos}", inactivo=not alumno.activo,
        id_rubrica=fila.id if fila else None, fluidez=fluidez, comprension=comprension,
        estado=estado_de(fluidez, comprension),
        resultado_decimas=resultado_decimas(fluidez, comprension) if completa else None,
        version=fila.version if fila else None,
    )


def _del_grupo(grupo: GrupoMes) -> list:
    return [getattr(RubricaMensual, campo) == valor for campo, valor in grupo.columnas().items()]


# ── Consulta (CU029) ────────────────────────────────────────────────────────────────

def listar(db: Session, usuario: Usuario, grupo: GrupoMes, page: int) -> RubricasDelMes:
    """Las evaluaciones del grupo y mes, por apellidos, en páginas de 40."""
    programa = _validar_grupo(db, usuario, grupo)
    mes = mes_evaluado(db, grupo)
    alumnos = alumnos_del_mes(db, grupo, mes)
    en_pagina = alumnos[(page - 1) * TAMANO_PAGINA: page * TAMANO_PAGINA]
    filas = {
        r.id_alumno: r
        for r in db.exec(select(RubricaMensual).where(
            *_del_grupo(grupo), col(RubricaMensual.id_alumno).in_([a.id_alumno for a in en_pagina])
        )).all()
    }
    valores = _valores(db)
    base = pagina([_item(a, filas.get(a.id_alumno), valores) for a in en_pagina], len(alumnos), page)
    destino = Destino(MODULO, grupo.id_colegio, grupo.id_grado, mes.periodo)
    return RubricasDelMes(
        **base.model_dump(), ciclo=ciclo_de(db, programa, grupo.id_grado),
        id_periodo_academico=mes.periodo.id_periodo_academico,
        escritura=escritura(db, usuario, destino, MENSAJES_ESCRITURA),
        detail=None if alumnos else MENSAJE_SIN_ALUMNOS,
    )


# ── Registro (CU029, CU030) ─────────────────────────────────────────────────────────

def _ids_nuevos(db: Session, item: RubricaGuardar) -> dict[str, Optional[int]]:
    """{columna: id_nivel_rubrica o None} de las dimensiones enviadas. Un valor que no es
    del subprograma o de esa dimensión se rechaza."""
    niveles = _niveles(db, item.id_programa)
    nuevos = {}
    for campo, (columna, dimension) in DIMENSIONES.items():
        if campo not in item.model_fields_set:
            continue
        valor = getattr(item, campo)
        if valor is not None and (dimension, valor) not in niveles:
            raise RechazoOperacion("valor_no_permitido", MENSAJE_VALOR_NO_PERMITIDO, {"campo": campo})
        nuevos[columna] = niveles[(dimension, valor)] if valor is not None else None
    return nuevos


def _fila_bloqueada(db: Session, item: RubricaGuardar) -> Optional[RubricaMensual]:
    return db.exec(
        select(RubricaMensual)
        .where(*_del_grupo(GrupoMes.de(item)), RubricaMensual.id_alumno == item.id_alumno)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


@dataclass(frozen=True)
class _Captura:
    usuario: Usuario
    contexto: ContextoOperacion
    id_permiso: Optional[int]

    def auditoria(self, ahora) -> dict[str, Any]:
        return {
            "id_usuario": self.usuario.id_usuario, "fecha": ahora, "id_actividad": self.contexto.id_actividad,
            "id_permiso": self.id_permiso,
        }


def _insertar(db: Session, item: RubricaGuardar, nuevos: dict, periodo: int, captura: _Captura) -> RubricaMensual:
    ahora = ahora_utc()
    id_nuevo = db.execute(
        insert_con_conflicto(db, _rubrica)
        .values(
            **GrupoMes.de(item).columnas(), id_alumno=item.id_alumno, id_periodo_academico=periodo, **nuevos,
            id_docente=captura.usuario.id_docente, id_actividad=captura.contexto.id_actividad,
            id_permiso=captura.id_permiso, fecha_registro=captura.contexto.en, version=1,
            creado_por=captura.usuario.id_usuario, creado_en=ahora,
        )
        .on_conflict_do_nothing()
        .returning(_rubrica.c.id)
    ).scalar_one_or_none()
    if id_nuevo is None:
        # Otra operación simultánea la creó antes.
        raise RechazoOperacion("modificacion_concurrente", MENSAJE_CONCURRENTE)
    registrar_auditoria(db, tabla=TABLA, id_registro=id_nuevo, accion=CREAR, **captura.auditoria(ahora))
    return db.get(RubricaMensual, id_nuevo)


def _actualizar(db: Session, fila: RubricaMensual, cambios: dict, periodo: int, captura: _Captura) -> None:
    ahora = ahora_utc()
    registrar_auditoria(db, tabla=TABLA, id_registro=fila.id, accion=EDITAR, cambios=cambios, **captura.auditoria(ahora))
    for columna, (_, nuevo) in cambios.items():
        setattr(fila, columna, nuevo)
    fila.id_periodo_academico = periodo
    fila.id_docente, fila.id_actividad, fila.id_permiso = (
        captura.usuario.id_docente, captura.contexto.id_actividad, captura.id_permiso
    )
    fila.fecha_registro, fila.version = captura.contexto.en, fila.version + 1
    fila.modificado_por, fila.modificado_en = captura.usuario.id_usuario, ahora
    db.add(fila)
    db.flush()


def _registro(db: Session, alumno: Alumno, fila: Optional[RubricaMensual]) -> dict[str, Any]:
    return _item(alumno, fila, _valores(db)).model_dump(mode="json")


def _guardar(db: Session, item: RubricaGuardar, periodo: int, captura: _Captura) -> Optional[RubricaMensual]:
    """Crea o actualiza la evaluación; sin cambios no escribe ni audita."""
    nuevos = _ids_nuevos(db, item)
    fila = _fila_bloqueada(db, item)
    if fila is None:
        if not any(v is not None for v in nuevos.values()):
            return None
        return _insertar(db, item, nuevos, periodo, captura)
    cambios = {c: (getattr(fila, c), v) for c, v in nuevos.items() if getattr(fila, c) != v}
    if cambios:
        if item.version != fila.version:
            raise RechazoOperacion(
                "modificacion_concurrente", MENSAJE_CONCURRENTE, _registro(db, db.get(Alumno, item.id_alumno), fila)
            )
        _actualizar(db, fila, cambios, periodo, captura)
    return fila


def _aplicador(db: Session, usuario: Usuario, item: RubricaGuardar):
    def aplicar(contexto: ContextoOperacion) -> dict[str, Any]:
        grupo = GrupoMes.de(item)
        _validar_grupo(db, usuario, grupo)
        mes = mes_evaluado(db, grupo)
        periodo = mes.periodo.id_periodo_academico
        habilitacion = habilitacion_escritura(
            db, usuario, grupo.id_colegio, grupo.id_grado, periodo, MODULO, en=contexto.en
        )
        if not habilitacion.permitido:
            raise RechazoOperacion("sin_habilitacion", MENSAJE_SIN_HABILITACION)
        alumno = db.get(Alumno, item.id_alumno)
        if alumno is None or not _pertenece(db, alumno.id_alumno, grupo, mes):
            raise RechazoOperacion("alumno_fuera_del_grupo", MENSAJE_FUERA_DEL_GRUPO)
        fila = _guardar(db, item, periodo, _Captura(usuario, contexto, habilitacion.id_permiso))
        return _registro(db, alumno, fila)

    return aplicar


def _id_entidad(item: RubricaGuardar) -> str:
    return "-".join(str(v) for v in GrupoMes.de(item).columnas().values()) + f":{item.id_alumno}"


def guardar(db: Session, usuario: Usuario, items: list[RubricaGuardar]) -> list[ResultadoOperacion]:
    """Registra o corrige evaluaciones (CU029, CU030). Cada ítem es una operación con su
    resultado; un lote puede tener aceptadas y rechazadas."""
    resultados = [
        procesar_operacion(
            db, usuario, item, _aplicador(db, usuario, item),
            Operacion(MODULO, TABLA, _id_entidad(item), MENSAJE_SIN_ACTIVIDAD),
        )
        for item in items
    ]
    db.commit()
    return resultados


# ── Criterios, motor y Salud del Sistema ────────────────────────────────────────────

def criterios(db: Session, id_programa: int, id_ciclo: int) -> list[Criterio]:
    """Niveles de la rúbrica del subprograma con su descriptor para el ciclo: Fluidez y
    luego Comprensión, de menor a mayor."""
    if db.get(Programa, id_programa) is None:
        raise _no_procesable(MENSAJE_PROGRAMA_INEXISTENTE, "programa_inexistente")
    if db.get(CicloEbr, id_ciclo) is None:
        raise _no_procesable(MENSAJE_CICLO_INEXISTENTE, "ciclo_inexistente")
    filas = db.exec(
        select(NivelRubrica, DescriptorRubrica.texto)
        .join(DescriptorRubrica, DescriptorRubrica.id_nivel_rubrica == NivelRubrica.id_nivel_rubrica)
        .where(NivelRubrica.id_programa == id_programa, DescriptorRubrica.id_ciclo == id_ciclo)
        .order_by(col(NivelRubrica.dimension).desc(), NivelRubrica.valor)
    ).all()
    return [
        Criterio(dimension=n.dimension, valor=n.valor, nombre=n.nombre_nivel, descriptor=texto) for n, texto in filas
    ]


def ultima_rubrica_completa(db: Session, id_alumno: int, id_periodo_academico: int) -> Optional[RubricaMensual]:
    """La evaluación completa (las dos dimensiones) de mayor mes del alumno en ese PA, o
    None. La usa el Registro de Vuelo (T9) para la sugerencia de nivel."""
    return db.exec(
        select(RubricaMensual)
        .where(
            RubricaMensual.id_alumno == id_alumno,
            RubricaMensual.id_periodo_academico == id_periodo_academico,
            col(RubricaMensual.id_nivel_fluidez).is_not(None),
            col(RubricaMensual.id_nivel_comprension).is_not(None),
        )
        .order_by(col(RubricaMensual.mes).desc())
    ).first()


def rubricas_completadas(db: Session, pa_por_colegio: dict) -> int:
    """Salud del Sistema (CU012): evaluaciones completadas del PA vigente de su colegio."""
    return db.exec(
        select(func.count())
        .select_from(RubricaMensual)
        .where(
            col(RubricaMensual.id_nivel_fluidez).is_not(None),
            col(RubricaMensual.id_nivel_comprension).is_not(None),
            en_pa_de_su_colegio(RubricaMensual.id_colegio, RubricaMensual.id_periodo_academico, pa_por_colegio),
        )
    ).one()
