"""Colegios (CU013).

- Solo el Supervisor los gestiona; el Docente lee los de su alcance (periodo vigente).
- Borrado lógico: desactivar no toca alumnos, registros ni asignaciones; el colegio sale
  del alcance operativo de su docente (las habilitaciones excluyen los inactivos).
- Grados y programas ofrecidos viven en `colegio_grado` y `colegio_programa`; cada grado
  con su día de clase (1 = lunes … 5 = viernes), obligatorio en la API, y sus secciones
  (`seccion`): solo se agregan, y cada grado nace con "Única".
- Cada cambio queda en `auditoria` (tabla `colegio`).
"""
from typing import Any, Optional

from fastapi import status
from sqlalchemy import delete, func
from sqlmodel import Session, SQLModel, col, select

from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.core.texto import nombre_comparable
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    Alumno,
    AlumnoPertenencia,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Seccion,
    Usuario,
)
from app.schemas.colegio import (
    ColegioCrear,
    ColegioDetalle,
    ColegioEditar,
    ColegioEstadoCalendario,
    ColegioItem,
    GradoColegio,
    EstadoCalendario,
    GradoOfrecido,
    Ubicaciones,
)
from app.schemas.comun import ItemCatalogo
from app.services.habilitacion import pares_visibles
from app.services.auditoria import ACTIVAR, CREAR, EDITAR, INACTIVAR, registrar_auditoria
from app.services.calendario import Calendarios, cargar_calendarios
from app.services.secciones import agregar_secciones, secciones_por_grado

TABLA = "colegio"
CAMPOS_SIMPLES = ("nombre", "departamento", "distrito", "provincia", "nivel_educativo")
DIAS = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes"}


def _texto_comparable(columna):
    """Expresión SQL para comparar textos sin distinguir mayúsculas ni espacios extremos."""
    return func.lower(func.trim(columna))


# ── Errores ─────────────────────────────────────────────────────────────────────────

def _no_existe() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el colegio.", motivo="colegio_no_encontrado")


def _fuera_de_alcance() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_403_FORBIDDEN,
        "El colegio no está entre los que tiene asignados.",
        motivo="colegio_fuera_de_alcance",
    )


def _cantidad(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


# ── Respuestas ──────────────────────────────────────────────────────────────────────

def item_de(colegio: Colegio) -> ColegioItem:
    return ColegioItem(
        id=colegio.id_colegio,
        nombre=colegio.nombre,
        nivel_educativo=colegio.nivel_educativo,
        departamento=colegio.departamento,
        provincia=colegio.provincia,
        distrito=colegio.distrito,
        activo=colegio.activo,
    )


def _grados_de(db: Session, id_colegio: int) -> list[tuple[Grado, ColegioGrado]]:
    """Grados del colegio con su fila de `colegio_grado` (día de clase), por id."""
    return list(
        db.exec(
            select(Grado, ColegioGrado)
            .join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
            .where(ColegioGrado.id_colegio == id_colegio)
            .order_by(Grado.id_grado)
        ).all()
    )


def _programas_de(db: Session, id_colegio: int) -> list[Programa]:
    return list(
        db.exec(
            select(Programa)
            .join(ColegioPrograma, ColegioPrograma.id_programa == Programa.id_programa)
            .where(ColegioPrograma.id_colegio == id_colegio)
            .order_by(Programa.id_programa)
        ).all()
    )


def detalle_de(db: Session, colegio: Colegio) -> ColegioDetalle:
    secciones = secciones_por_grado(db, colegio.id_colegio)
    return ColegioDetalle(
        **item_de(colegio).model_dump(),
        grados=[
            GradoColegio(
                id=g.id_grado,
                nombre=g.nombre,
                dia_clase=fila.dia_clase,
                secciones=[ItemCatalogo(id=s.id_seccion, nombre=s.nombre) for s in secciones[g.id_grado]],
            )
            for g, fila in _grados_de(db, colegio.id_colegio)
        ],
        programas=[
            ItemCatalogo(id=p.id_programa, nombre=p.nombre) for p in _programas_de(db, colegio.id_colegio)
        ],
    )


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def colegios_visibles(db: Session, usuario: Usuario, es_docente: bool) -> Optional[set[int]]:
    """Ids de los colegios que puede ver: None = todos (Supervisor); el Docente, los de
    sus habilitaciones vigentes: asignaciones del PA vigente de cada colegio,
    retroactivas en su PR y permisos de corrección vigentes (vacío si no tiene)."""
    if not es_docente:
        return None
    return {id_colegio for id_colegio, _ in pares_visibles(db, usuario)}


def listar_colegios(
    db: Session,
    visibles: Optional[set[int]],
    departamento: Optional[str],
    distrito: Optional[str],
    activo: bool,
    page: int,
) -> Paginado[ColegioItem]:
    consulta = select(Colegio).where(Colegio.activo == activo)
    if visibles is not None:
        if not visibles:
            return pagina([], 0, page)
        consulta = consulta.where(col(Colegio.id_colegio).in_(visibles))
    if departamento and departamento.strip():
        consulta = consulta.where(_texto_comparable(Colegio.departamento) == departamento.strip().lower())
    if distrito and distrito.strip():
        consulta = consulta.where(_texto_comparable(Colegio.distrito) == distrito.strip().lower())
    consulta = consulta.order_by(Colegio.nombre, Colegio.id_colegio)
    filas, total = paginar(db, consulta, page)
    return pagina([item_de(c) for c in filas], total, page)


def ubicaciones(db: Session, visibles: Optional[set[int]], departamento: Optional[str]) -> Ubicaciones:
    """Departamentos y distritos distintos de los colegios visibles, para los filtros."""
    if visibles is not None and not visibles:
        return Ubicaciones(departamentos=[], distritos=[])
    filtro = [] if visibles is None else [col(Colegio.id_colegio).in_(visibles)]
    departamentos = db.exec(
        select(Colegio.departamento).where(*filtro).distinct().order_by(Colegio.departamento)
    ).all()
    filtro_distritos = [*filtro, col(Colegio.distrito).is_not(None)]
    if departamento and departamento.strip():
        filtro_distritos.append(_texto_comparable(Colegio.departamento) == departamento.strip().lower())
    distritos = db.exec(
        select(Colegio.distrito).where(*filtro_distritos).distinct().order_by(Colegio.distrito)
    ).all()
    return Ubicaciones(departamentos=list(departamentos), distritos=list(distritos))


def _estado_de(calendarios: Calendarios, id_colegio: int, id_anio_escolar: Optional[int]) -> Optional[EstadoCalendario]:
    hoy = hoy_lima()
    if id_anio_escolar is None:
        periodo = calendarios.periodo_de(id_colegio, hoy)
    else:
        calendario = calendarios.aplicable(id_colegio, id_anio_escolar)
        periodo = calendario.periodo_de(hoy) if calendario is not None else None
    if periodo is None:
        return None
    return EstadoCalendario(
        tipo=periodo.tipo, numero=periodo.numero, es_pr4=not periodo.es_pa and periodo.numero == 4,
        fecha_fin=periodo.fecha_fin,
    )


def estado_calendario(db: Session, id_anio_escolar: Optional[int]) -> list[ColegioEstadoCalendario]:
    """Colegios activos, por nombre, con el periodo en que hoy está cada uno según su
    calendario del año `id_anio_escolar` (o del que contenga hoy, si no se indica)."""
    colegios = db.exec(select(Colegio).where(col(Colegio.activo).is_(True)).order_by(Colegio.nombre)).all()
    calendarios = cargar_calendarios(db, id_anio_escolar, ids_colegio=[c.id_colegio for c in colegios])
    return [
        ColegioEstadoCalendario(
            id=c.id_colegio, nombre=c.nombre, estado_calendario=_estado_de(calendarios, c.id_colegio, id_anio_escolar)
        )
        for c in colegios
    ]


def _colegio(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_existe()
    return colegio


def obtener_colegio(db: Session, visibles: Optional[set[int]], id_colegio: int) -> ColegioDetalle:
    colegio = _colegio(db, id_colegio)
    if visibles is not None and id_colegio not in visibles:
        raise _fuera_de_alcance()
    return detalle_de(db, colegio)


# ── Validaciones de escritura ───────────────────────────────────────────────────────

def _verificar_nombre_libre(db: Session, nombre: str, excepto: Optional[int] = None) -> None:
    """409 si otro colegio (activo o no) ya tiene ese nombre, sin distinguir mayúsculas,
    tildes ni espacios: el script de carga identifica los colegios por nombre."""
    buscado = nombre_comparable(nombre)
    for id_colegio, existente in db.exec(select(Colegio.id_colegio, Colegio.nombre)).all():
        if id_colegio != excepto and nombre_comparable(existente) == buscado:
            raise ErrorNegocio(
                status.HTTP_409_CONFLICT,
                f"Ya existe un colegio llamado {existente}.",
                motivo="colegio_duplicado",
            )


def _catalogo(db: Session, modelo: type[SQLModel], ids: list[int], motivo: str, etiqueta: str) -> list[Any]:
    """Filas del catálogo con esos ids (sin repetidos, en orden de id). 422 si falta alguno."""
    clave = getattr(modelo, f"id_{etiqueta}")
    unicos = sorted(set(ids))
    filas = list(db.exec(select(modelo).where(col(clave).in_(unicos)).order_by(clave)).all())
    faltan = sorted(set(unicos) - {getattr(f, f"id_{etiqueta}") for f in filas})
    if faltan:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"No existen los {etiqueta}s con id {faltan}.",
            motivo=motivo,
        )
    return filas


def _grados_validos(db: Session, ofrecidos: list[GradoOfrecido]) -> list[tuple[Grado, GradoOfrecido]]:
    """(grado, lo pedido para él) de cada grado ofrecido, por id. 422 si falta alguno."""
    pedidos = {g.id_grado: g for g in ofrecidos}
    return [(g, pedidos[g.id_grado]) for g in _catalogo(db, Grado, list(pedidos), "grado_inexistente", "grado")]


def _programas_validos(db: Session, ids: list[int]) -> list[Programa]:
    return _catalogo(db, Programa, ids, "programa_inexistente", "programa")


def _con_historial(db: Session, id_colegio: int, id_grado: int) -> bool:
    """Si algún alumno (activo o no) está o estuvo en una sección del grado: sus secciones
    no se pueden borrar sin perder la pertenencia histórica."""
    en_ficha = select(Alumno.id_alumno).where(Alumno.id_colegio == id_colegio, Alumno.id_grado == id_grado)
    en_historial = select(AlumnoPertenencia.id).where(
        AlumnoPertenencia.id_colegio == id_colegio, AlumnoPertenencia.id_grado == id_grado
    )
    return db.exec(en_ficha.limit(1)).first() is not None or db.exec(en_historial.limit(1)).first() is not None


def _uso_de_grado(db: Session, id_colegio: int, grado: Grado) -> Optional[str]:
    """Por qué no se puede retirar el grado, o None si está libre."""
    alumnos = db.exec(
        select(func.count()).select_from(Alumno).where(
            Alumno.id_colegio == id_colegio, Alumno.id_grado == grado.id_grado, col(Alumno.activo).is_(True)
        )
    ).one()
    periodos = db.exec(
        select(PeriodoAcademico)
        .join(DocenteColegioGrado, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(DocenteColegioGrado.id_colegio == id_colegio, DocenteColegioGrado.id_grado == grado.id_grado)
    ).all()
    # Vigentes o futuras: su PA no terminó en el calendario del colegio.
    calendarios = cargar_calendarios(db, ids_colegio=[id_colegio])
    hoy = hoy_lima()
    asignaciones = sum(not calendarios.pa_terminado(id_colegio, p, hoy) for p in periodos)
    usos = []
    if alumnos:
        usos.append(_cantidad(alumnos, "alumno activo", "alumnos activos"))
    if asignaciones:
        usos.append(_cantidad(asignaciones, "asignación vigente", "asignaciones vigentes"))
    if not usos and _con_historial(db, id_colegio, grado.id_grado):
        usos.append("alumnos en su historial")
    return f"No se puede retirar {grado.nombre}: tiene {' y '.join(usos)}." if usos else None


def _uso_de_programa(db: Session, id_colegio: int, programa: Programa) -> Optional[str]:
    alumnos = db.exec(
        select(func.count()).select_from(Alumno).where(
            Alumno.id_colegio == id_colegio,
            Alumno.id_programa_actual == programa.id_programa,
            col(Alumno.activo).is_(True),
        )
    ).one()
    if not alumnos:
        return None
    return f"No se puede retirar {programa.nombre}: tiene {_cantidad(alumnos, 'alumno activo', 'alumnos activos')}."


def _lista_legible(filas: list[Any]) -> str:
    return ", ".join(f.nombre for f in filas)


def _grados_legibles(grados: list[tuple[Grado, Optional[int]]]) -> str:
    """'1.º (lunes), 2.º (sin día)': para la auditoría (campo virtual `grados`)."""
    return ", ".join(f"{g.nombre} ({DIAS.get(dia, 'sin día')})" for g, dia in grados)


def _secciones_legibles(db: Session, id_colegio: int) -> str:
    """'1.º: Única; 2.º: A, B': para la auditoría (campo virtual `secciones`)."""
    nombres = {g.id_grado: g.nombre for g, _ in _grados_de(db, id_colegio)}
    return "; ".join(
        f"{nombres.get(id_grado, id_grado)}: {', '.join(s.nombre for s in secciones)}"
        for id_grado, secciones in sorted(secciones_por_grado(db, id_colegio).items())
    )


def _agregar_secciones(
    db: Session, id_colegio: int, grados: list[tuple[Grado, GradoOfrecido]], auditoria: dict
) -> bool:
    """Agrega las secciones pedidas (y "Única" a los grados que no tengan). Devuelve si
    agregó alguna."""
    agregadas = [agregar_secciones(db, id_colegio, g.id_grado, pedido.secciones, auditoria) for g, pedido in grados]
    return any(agregadas)


# ── Escritura ───────────────────────────────────────────────────────────────────────

def crear_colegio(db: Session, data: ColegioCrear, actor: Usuario) -> ColegioDetalle:
    _verificar_nombre_libre(db, data.nombre)
    grados = _grados_validos(db, data.grados)
    programas = _programas_validos(db, data.programas)

    ahora = ahora_utc()
    auditoria = {"creado_por": actor.id_usuario, "creado_en": ahora}
    colegio = Colegio(
        nombre=data.nombre,
        departamento=data.departamento,
        distrito=data.distrito,
        provincia=data.provincia,
        nivel_educativo=data.nivel_educativo,
        activo=True,
        **auditoria,
    )
    db.add(colegio)
    db.flush()
    db.add_all(
        ColegioGrado(id_colegio=colegio.id_colegio, id_grado=g.id_grado, dia_clase=pedido.dia_clase, **auditoria)
        for g, pedido in grados
    )
    db.add_all(
        ColegioPrograma(id_colegio=colegio.id_colegio, id_programa=p.id_programa, **auditoria) for p in programas
    )
    db.flush()
    _agregar_secciones(db, colegio.id_colegio, grados, auditoria)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=colegio.id_colegio, accion=CREAR, id_usuario=actor.id_usuario, fecha=ahora
    )
    db.commit()
    db.refresh(colegio)
    return detalle_de(db, colegio)


def _reemplazar_grados(
    db: Session, colegio: Colegio, pedidos: list[tuple[Grado, GradoOfrecido]], auditoria: dict
) -> Optional[tuple[str, str]]:
    """Deja al colegio con esos grados y días de clase. 409 si se retira uno en uso; las
    secciones de un grado retirado (sin alumnos) se borran con él. Devuelve (antes,
    después) legibles, o None si no cambió nada."""
    nuevos = [(g, pedido.dia_clase) for g, pedido in pedidos]
    actuales = _grados_de(db, colegio.id_colegio)
    dias_nuevos = {g.id_grado: dia for g, dia in nuevos}
    retirados = [g for g, _ in actuales if g.id_grado not in dias_nuevos]
    usos = [u for u in (_uso_de_grado(db, colegio.id_colegio, g) for g in retirados) if u]
    if usos:
        raise ErrorNegocio(status.HTTP_409_CONFLICT, " ".join(usos), motivo="grado_en_uso")
    antes = [(g, fila.dia_clase) for g, fila in actuales]
    if {g.id_grado: dia for g, dia in antes} == dias_nuevos:
        return None
    ids_retirados = [g.id_grado for g in retirados]
    db.execute(
        delete(Seccion).where(Seccion.id_colegio == colegio.id_colegio, col(Seccion.id_grado).in_(ids_retirados))
    )
    db.execute(
        delete(ColegioGrado).where(
            ColegioGrado.id_colegio == colegio.id_colegio, col(ColegioGrado.id_grado).in_(ids_retirados)
        )
    )
    for g, fila in actuales:
        if g.id_grado in dias_nuevos and fila.dia_clase != dias_nuevos[g.id_grado]:
            fila.dia_clase = dias_nuevos[g.id_grado]
            fila.modificado_por, fila.modificado_en = auditoria["creado_por"], auditoria["creado_en"]
            db.add(fila)
    ids_actuales = {g.id_grado for g, _ in actuales}
    db.add_all(
        ColegioGrado(id_colegio=colegio.id_colegio, id_grado=g.id_grado, dia_clase=dia, **auditoria)
        for g, dia in nuevos if g.id_grado not in ids_actuales
    )
    return _grados_legibles(antes), _grados_legibles(nuevos)


def _reemplazar_programas(
    db: Session, colegio: Colegio, nuevos: list[Programa], auditoria: dict
) -> Optional[tuple[str, str]]:
    actuales = _programas_de(db, colegio.id_colegio)
    ids_nuevos = {p.id_programa for p in nuevos}
    retirados = [p for p in actuales if p.id_programa not in ids_nuevos]
    usos = [u for u in (_uso_de_programa(db, colegio.id_colegio, p) for p in retirados) if u]
    if usos:
        raise ErrorNegocio(status.HTTP_409_CONFLICT, " ".join(usos), motivo="programa_en_uso")
    ids_actuales = {p.id_programa for p in actuales}
    if ids_actuales == ids_nuevos:
        return None
    db.execute(
        delete(ColegioPrograma).where(
            ColegioPrograma.id_colegio == colegio.id_colegio,
            col(ColegioPrograma.id_programa).in_([p.id_programa for p in retirados]),
        )
    )
    db.add_all(
        ColegioPrograma(id_colegio=colegio.id_colegio, id_programa=p.id_programa, **auditoria)
        for p in nuevos if p.id_programa not in ids_actuales
    )
    return _lista_legible(actuales), _lista_legible(nuevos)


def _cambio_de_secciones(
    db: Session, id_colegio: int, grados: list[tuple[Grado, GradoOfrecido]], auditoria: dict
) -> Optional[tuple[str, str]]:
    """Agrega las secciones pedidas y "Única" a los grados nuevos. Devuelve (antes,
    después) legibles para la auditoría, o None si no se agregó ninguna."""
    db.flush()
    antes = _secciones_legibles(db, id_colegio)
    if not _agregar_secciones(db, id_colegio, grados, auditoria):
        return None
    db.flush()
    return antes, _secciones_legibles(db, id_colegio)


def editar_colegio(db: Session, id_colegio: int, data: ColegioEditar, actor: Usuario) -> ColegioDetalle:
    colegio = _colegio(db, id_colegio)
    enviados = data.model_fields_set
    if "nombre" in enviados:
        _verificar_nombre_libre(db, data.nombre, excepto=id_colegio)
    grados = _grados_validos(db, data.grados) if "grados" in enviados else None
    programas = _programas_validos(db, data.programas) if "programas" in enviados else None

    ahora = ahora_utc()
    auditoria = {"creado_por": actor.id_usuario, "creado_en": ahora}
    cambios: dict[str, tuple[Any, Any]] = {}
    for campo in CAMPOS_SIMPLES:
        if campo in enviados:
            cambios[campo] = (getattr(colegio, campo), getattr(data, campo))
            setattr(colegio, campo, getattr(data, campo))
    if grados is not None:
        cambio = _reemplazar_grados(db, colegio, grados, auditoria)
        if cambio:
            cambios["grados"] = cambio
        cambio = _cambio_de_secciones(db, colegio.id_colegio, grados, auditoria)
        if cambio:
            cambios["secciones"] = cambio
    if programas is not None:
        cambio = _reemplazar_programas(db, colegio, programas, auditoria)
        if cambio:
            cambios["programas"] = cambio

    if registrar_auditoria(
        db, tabla=TABLA, id_registro=colegio.id_colegio, accion=EDITAR, cambios=cambios,
        id_usuario=actor.id_usuario, fecha=ahora,
    ):
        colegio.modificado_por = actor.id_usuario
        colegio.modificado_en = ahora
    db.add(colegio)
    db.commit()
    db.refresh(colegio)
    return detalle_de(db, colegio)


def cambiar_estado(db: Session, id_colegio: int, activo: bool, actor: Usuario) -> ColegioItem:
    """Borrado lógico. Idempotente: si ya estaba en ese estado, no cambia nada."""
    colegio = _colegio(db, id_colegio)
    if colegio.activo == activo:
        return item_de(colegio)
    ahora = ahora_utc()
    colegio.activo = activo
    colegio.modificado_por = actor.id_usuario
    colegio.modificado_en = ahora
    db.add(colegio)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=colegio.id_colegio, accion=ACTIVAR if activo else INACTIVAR,
        id_usuario=actor.id_usuario, fecha=ahora,
    )
    db.commit()
    db.refresh(colegio)
    return item_de(colegio)


def completar_oferta(
    db: Session, colegio: Colegio, grados: list[Grado], programas: list[Programa], auditoria: dict
) -> tuple[list[Grado], list[Programa]]:
    """Agrega al colegio los grados y programas que le falten (nunca quita). No hace commit.

    Para la carga de datos (seed y colegios iniciales): los grados nuevos quedan sin día
    de clase (null) y con la sección "Única". Devuelve lo que agregó.
    """
    tiene_grados = {g.id_grado for g, _ in _grados_de(db, colegio.id_colegio)}
    tiene_programas = {p.id_programa for p in _programas_de(db, colegio.id_colegio)}
    nuevos_grados = [g for g in grados if g.id_grado not in tiene_grados]
    nuevos_programas = [p for p in programas if p.id_programa not in tiene_programas]
    db.add_all(ColegioGrado(id_colegio=colegio.id_colegio, id_grado=g.id_grado, **auditoria) for g in nuevos_grados)
    db.add_all(
        ColegioPrograma(id_colegio=colegio.id_colegio, id_programa=p.id_programa, **auditoria)
        for p in nuevos_programas
    )
    db.flush()
    for g in nuevos_grados:
        agregar_secciones(db, colegio.id_colegio, g.id_grado, [], auditoria)
    return nuevos_grados, nuevos_programas
