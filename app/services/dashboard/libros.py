"""Consolidado de libros leídos del año escolar (CU034; CU035 usa su total y promedio).

Intervalo: el año escolar de cada colegio según su calendario (del inicio del PA1 al fin
del PR4), hasta hoy. Cada cantidad se atribuye al mes de la fecha de la clase de referencia
de su tabla, con su año, y al grupo de esa tabla (la pertenencia de esa fecha): un cambio
posterior de colegio, grado o subprograma no traslada las lecturas.

Libros de una fila: 1 si tiene el libro para subir de nivel (LSN, todo o nada) más su
cantidad de selección libre (LSL). LSL null no suma ni es un 0 confirmado. Una tabla
"No realizada" aporta 0 confirmado. Se suman las relecturas de un mismo título.

Completitud: con los registros esperados de Seguimiento de Lectura
(`registros_vencidos.obligaciones_lectura`, la misma regla de CU011). Falta información si
una tabla esperada no existe o tiene filas "Pendiente". Una tabla que todavía no vence no
falta.

Alumnos incluidos (A4): los de las filas de las tablas consideradas (la lista de la clase
de referencia, también "No aplica" y ausentes) y, por cada tabla esperada que no existe, los
alumnos del grupo y subprograma en la fecha de su clase. Cada alumno, una vez por colegio.
Promedio = total / alumnos incluidos; se muestra con 2 decimales y se ordena con el valor
exacto (empates 1, 1, 3). Sin alumnos o sin información, no hay promedio ni posición.

Estados: `completo`, `incompleto` (hay cantidades y faltan registros esperados),
`sin_informacion` (ninguna cantidad ni declaración: total null, nunca 0) y `futuro`.
"""
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from typing import Optional

from sqlalchemy import and_, or_
from sqlmodel import Session, col, select

from app.core.errores import error_de_campo
from app.core.texto import comparable
from app.core.tiempo import hoy_lima
from app.models.evaluacion import (
    ESTADOS_ASISTIO,
    LECTURA_NO_REALIZADA,
    Asistencia,
    Clase,
    LecturaSemanal,
    LecturaSemanalAlumno,
)
from app.models.organizacion import Alumno, AlumnoPertenencia, Colegio, Grado, Programa, Seccion, Usuario
from app.schemas.comun import ItemCatalogo
from app.schemas.dashboard import (
    AlumnoLibros,
    ClaseReferencia,
    ColegioLibros,
    ColegioLibrosSinPromedio,
    DesgloseLibros,
    GradoLibros,
    LibrosDelAnio,
    LibrosDelColegio,
    MesLibros,
    RegistroLibros,
    ResumenLibros,
)
from app.services.dashboard.filtros import (
    FiltrosResueltos,
    colegios_del_alcance,
    fuera_de_alcance,
    item_colegio,
    validar_colegio_seleccionado,
)
from app.services.dashboard.numeros import dos_decimales, meses_entre, ranking
from app.services.registros_vencidos import ObligacionLectura, obligaciones_lectura

COMPLETO, INCOMPLETO, SIN_INFORMACION, FUTURO = "completo", "incompleto", "sin_informacion", "futuro"
AUSENTE = "ausente"
ETIQUETA_PARCIAL = "Total registrado hasta el momento"


# ── Aportes y acumulados ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Aporte:
    """Lo que aporta un alumno en una tabla (o en una tabla esperada que falta)."""

    id_colegio: int
    id_grado: int
    id_alumno: int
    anio: int
    mes: int
    libros: int
    # Hay una cantidad o una declaración (fila completada, "No aplica" o "No realizada").
    con_dato: bool
    # Falta un registro esperado (tabla inexistente o fila "Pendiente" de una tabla vencida).
    pendiente: bool


@dataclass
class Acumulado:
    total: int = 0
    con_dato: bool = False
    pendiente: bool = False
    # (colegio, alumno): cada alumno una vez por colegio.
    alumnos: set[tuple[int, int]] = field(default_factory=set)

    def agregar(self, aporte: Aporte) -> None:
        self.total += aporte.libros
        self.con_dato |= aporte.con_dato
        self.pendiente |= aporte.pendiente
        self.alumnos.add((aporte.id_colegio, aporte.id_alumno))

    @property
    def estado(self) -> str:
        if not self.con_dato:
            return SIN_INFORMACION
        return INCOMPLETO if self.pendiente else COMPLETO

    @property
    def total_visible(self) -> Optional[int]:
        return self.total if self.con_dato else None

    @property
    def promedio(self) -> Optional[Fraction]:
        """Valor exacto; None sin alumnos o sin información."""
        return Fraction(self.total, len(self.alumnos)) if self.con_dato and self.alumnos else None


def acumular(aportes: Iterable[Aporte]) -> Acumulado:
    acumulado = Acumulado()
    for aporte in aportes:
        acumulado.agregar(aporte)
    return acumulado


# ── Filas de Seguimiento de Lectura ─────────────────────────────────────────────────

@dataclass(frozen=True)
class FilaLectura:
    fila: LecturaSemanalAlumno
    tabla: LecturaSemanal
    fecha: date
    # Asistencia del alumno en la clase de referencia (None = sin marcar).
    asistencia: Optional[str]

    @property
    def lsn(self) -> int:
        return 0 if self.no_realizada or self.fila.lsn_titulo is None else 1

    @property
    def lsl(self) -> Optional[int]:
        return 0 if self.no_realizada else self.fila.cantidad_lsl

    @property
    def no_realizada(self) -> bool:
        return self.tabla.estado == LECTURA_NO_REALIZADA

    @property
    def cantidad(self) -> Optional[int]:
        """Libros de la fila; None si no tiene ninguna cantidad."""
        if self.lsn == 0 and self.lsl is None:
            return None
        return self.lsn + (self.lsl or 0)

    @property
    def confirmada(self) -> bool:
        """`completada` o `no_aplica` (la regla de Seguimiento de Lectura)."""
        if self.no_realizada or self.asistencia == AUSENTE:
            return True
        return self.asistencia in ESTADOS_ASISTIO and self.fila.cantidad_lsl is not None

    def aporte(self, esperadas: set[int]) -> Aporte:
        cantidad = self.cantidad
        return Aporte(
            self.tabla.id_colegio, self.tabla.id_grado, self.fila.id_alumno, self.fecha.year, self.fecha.month,
            libros=cantidad or 0,
            con_dato=cantidad is not None or self.confirmada,
            pendiente=not self.confirmada and self.tabla.id in esperadas,
        )


def _filas(
    db: Session, ids_colegio: list[int], desde: date, hasta: date, id_alumno: Optional[int] = None
) -> list[FilaLectura]:
    """Filas de las tablas de esos colegios con clase de referencia en [desde, hasta]."""
    consulta = (
        select(LecturaSemanalAlumno, LecturaSemanal, Clase.fecha, Asistencia.estado)
        .join(LecturaSemanal, LecturaSemanal.id == LecturaSemanalAlumno.id_lectura_semanal)
        .join(Clase, Clase.id_clase == LecturaSemanal.id_clase_referencia)
        .outerjoin(Asistencia, and_(
            Asistencia.id_clase == LecturaSemanal.id_clase_referencia,
            Asistencia.id_alumno == LecturaSemanalAlumno.id_alumno,
        ))
        .where(col(LecturaSemanal.id_colegio).in_(ids_colegio), col(Clase.fecha).between(desde, hasta))
    )
    if id_alumno is not None:
        consulta = consulta.where(LecturaSemanalAlumno.id_alumno == id_alumno)
    return [FilaLectura(*fila) for fila in db.exec(consulta.order_by(Clase.fecha, LecturaSemanal.id)).all()]


# ── Aportes del año ─────────────────────────────────────────────────────────────────

def _rangos(filtros: FiltrosResueltos, ids_colegio: list[int], hoy: date) -> dict[int, tuple[date, date]]:
    """{colegio: (inicio de su año, min(fin, hoy))} de los colegios con calendario cuyo
    año ya empezó."""
    rangos = {}
    for id_colegio in ids_colegio:
        calendario = filtros.calendarios.get(id_colegio)
        if calendario is not None and calendario.fecha_inicio <= hoy:
            rangos[id_colegio] = (calendario.fecha_inicio, min(calendario.fecha_fin, hoy))
    return rangos


def _aportes_sin_tabla(
    db: Session, faltantes: list[tuple[int, ObligacionLectura]], rangos: dict[int, tuple[date, date]]
) -> list[Aporte]:
    """Por cada tabla esperada que no existe, sus alumnos (grupo y subprograma en la fecha
    de su clase), pendientes y sin libros."""
    if not faltantes:
        return []
    ids = list({id_colegio for id_colegio, _ in faltantes})
    intervalos: dict[tuple, list[AlumnoPertenencia]] = defaultdict(list)
    for fila in db.exec(select(AlumnoPertenencia).where(
        col(AlumnoPertenencia.id_colegio).in_(ids),
        AlumnoPertenencia.desde <= max(rangos[c][1] for c in ids),
        or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > min(rangos[c][0] for c in ids)),
    )).all():
        intervalos[(fila.id_colegio, fila.id_grado, fila.id_seccion, fila.id_programa)].append(fila)
    aportes = []
    for id_colegio, o in faltantes:
        fecha = o.fecha_clase
        for p in intervalos[(id_colegio, o.id_grado, o.id_seccion, o.id_programa)]:
            if p.desde <= fecha and (p.hasta is None or fecha < p.hasta):
                aportes.append(Aporte(id_colegio, o.id_grado, p.id_alumno, fecha.year, fecha.month, 0, False, True))
    return aportes


def aportes_del_anio(db: Session, filtros: FiltrosResueltos, ids_colegio: list[int]) -> list[Aporte]:
    """Aportes de esos colegios en su año escolar hasta hoy. Consultas fijas por colegio
    (las obligaciones de Lectura) más un número fijo en total."""
    hoy = hoy_lima()
    rangos = _rangos(filtros, ids_colegio, hoy)
    if not rangos:
        return []
    obligaciones = [
        (id_colegio, obligacion)
        for id_colegio, (desde, hasta) in rangos.items()
        for obligacion in obligaciones_lectura(db, id_colegio, desde, hasta, filtros.cargados, hoy)
    ]
    esperadas = {o.tabla.id for _, o in obligaciones if o.tabla is not None}
    filas = _filas(db, list(rangos), min(r[0] for r in rangos.values()), max(r[1] for r in rangos.values()))
    aportes = [
        f.aporte(esperadas) for f in filas
        if rangos[f.tabla.id_colegio][0] <= f.fecha <= rangos[f.tabla.id_colegio][1]
    ]
    return aportes + _aportes_sin_tabla(db, [(c, o) for c, o in obligaciones if o.tabla is None], rangos)


# ── Presentación ────────────────────────────────────────────────────────────────────

def _meses(meses: list[tuple[int, int]], aportes: list[Aporte], hoy: date) -> list[MesLibros]:
    por_mes: dict[tuple[int, int], Acumulado] = defaultdict(Acumulado)
    for aporte in aportes:
        por_mes[(aporte.anio, aporte.mes)].agregar(aporte)
    salida = []
    for anio, mes in meses:
        if date(anio, mes, 1) > hoy:
            salida.append(MesLibros(anio=anio, mes=mes, estado=FUTURO, total=None))
            continue
        acumulado = por_mes.get((anio, mes), Acumulado())
        salida.append(MesLibros(
            anio=anio, mes=mes, estado=acumulado.estado, total=acumulado.total_visible,
            en_curso=(anio, mes) == (hoy.year, hoy.month),
        ))
    return salida


def _estado(acumulado: Acumulado, futuro: bool) -> str:
    return FUTURO if futuro and not acumulado.con_dato else acumulado.estado


def _resumen(acumulado: Acumulado, futuro: bool) -> ResumenLibros:
    estado = _estado(acumulado, futuro)
    promedio = acumulado.promedio
    return ResumenLibros(
        total=acumulado.total_visible,
        estado=estado,
        provisional=estado == INCOMPLETO,
        alumnos_incluidos=len(acumulado.alumnos),
        promedio=dos_decimales(promedio) if promedio is not None else None,
        etiqueta=ETIQUETA_PARCIAL if estado == INCOMPLETO else None,
    )


def _meses_del_intervalo(filtros: FiltrosResueltos, ids_colegio: list[int]) -> list[tuple[int, int]]:
    """Meses del año escolar de esos colegios (unión de sus calendarios; sin calendario, las
    fechas del año escolar)."""
    calendarios = [filtros.calendarios[c] for c in ids_colegio if c in filtros.calendarios]
    if not calendarios:
        return meses_entre(filtros.anio.fecha_inicio, filtros.anio.fecha_fin)
    return meses_entre(min(c.fecha_inicio for c in calendarios), max(c.fecha_fin for c in calendarios))


def _empieza_despues(filtros: FiltrosResueltos, ids_colegio: list[int], hoy: date) -> bool:
    calendarios = [filtros.calendarios[c] for c in ids_colegio if c in filtros.calendarios]
    inicio = min(c.fecha_inicio for c in calendarios) if calendarios else filtros.anio.fecha_inicio
    return inicio > hoy


# ── GET /dashboard/libros ───────────────────────────────────────────────────────────

def _sin_promedio(filtros: FiltrosResueltos, colegio: Colegio, acumulado: Acumulado) -> ColegioLibrosSinPromedio:
    if colegio.id_colegio not in filtros.calendarios:
        motivo = "sin_calendario"
    else:
        motivo = "sin_alumnos" if not acumulado.alumnos else "sin_informacion"
    return ColegioLibrosSinPromedio(colegio=item_colegio(colegio), alumnos_incluidos=len(acumulado.alumnos), motivo=motivo)


def libros_del_anio(db: Session, filtros: FiltrosResueltos) -> LibrosDelAnio:
    hoy = hoy_lima()
    aportes = aportes_del_anio(db, filtros, filtros.ids_colegio)
    meses = _meses_del_intervalo(filtros, filtros.ids_colegio)
    por_colegio: dict[int, list[Aporte]] = defaultdict(list)
    for aporte in aportes:
        por_colegio[aporte.id_colegio].append(aporte)
    con_promedio, sin_promedio = [], []
    for colegio in filtros.colegios:
        acumulado = acumular(por_colegio[colegio.id_colegio])
        if acumulado.promedio is None:
            sin_promedio.append(_sin_promedio(filtros, colegio, acumulado))
        else:
            con_promedio.append((colegio, acumulado))
    colegios = [
        ColegioLibros(
            posicion=posicion, provisional=acumulado.estado == INCOMPLETO, colegio=item_colegio(colegio),
            total=acumulado.total_visible, estado=acumulado.estado, alumnos_incluidos=len(acumulado.alumnos),
            promedio=dos_decimales(acumulado.promedio), meses=_meses(meses, por_colegio[colegio.id_colegio], hoy),
        )
        for posicion, (colegio, acumulado) in ranking(
            con_promedio, lambda e: e[1].promedio, lambda e: (comparable(e[0].nombre), e[0].id_colegio)
        )
    ]
    return LibrosDelAnio(
        contexto=filtros.contexto(),
        resumen=_resumen(acumular(aportes), _empieza_despues(filtros, filtros.ids_colegio, hoy)),
        meses=_meses(meses, aportes, hoy),
        colegios=colegios,
        colegios_sin_informacion=sin_promedio,
    )


# ── GET /dashboard/libros/colegios/{id_colegio} ─────────────────────────────────────

def nombre_de_alumno(alumno: Alumno) -> ItemCatalogo:
    return ItemCatalogo(id=alumno.id_alumno, nombre=f"{alumno.nombres} {alumno.apellidos}")


def _alumnos_del_grado(
    aportes: list[Aporte], alumnos: dict[int, Alumno], meses: list[tuple[int, int]], hoy: date
) -> list[AlumnoLibros]:
    por_alumno: dict[int, list[Aporte]] = defaultdict(list)
    for aporte in aportes:
        por_alumno[aporte.id_alumno].append(aporte)
    orden = sorted(por_alumno, key=lambda a: (comparable(alumnos[a].apellidos), comparable(alumnos[a].nombres), a))
    return [
        AlumnoLibros(
            alumno=nombre_de_alumno(alumnos[a]), total=acumular(por_alumno[a]).total_visible,
            estado=acumular(por_alumno[a]).estado, meses=_meses(meses, por_alumno[a], hoy),
        )
        for a in orden
    ]


def _grado(
    posicion: Optional[int], grado: Grado, aportes: list[Aporte], alumnos: list[AlumnoLibros]
) -> GradoLibros:
    acumulado = acumular(aportes)
    promedio = acumulado.promedio
    return GradoLibros(
        posicion=posicion, provisional=acumulado.estado == INCOMPLETO,
        grado=ItemCatalogo(id=grado.id_grado, nombre=grado.nombre), total=acumulado.total_visible,
        estado=acumulado.estado, alumnos_incluidos=len(acumulado.alumnos),
        promedio=dos_decimales(promedio) if promedio is not None else None, alumnos=alumnos,
    )


def libros_del_colegio(db: Session, usuario: Usuario, filtros: FiltrosResueltos, id_colegio: int) -> LibrosDelColegio:
    """Total, meses y grados del colegio (cada uno con sus alumnos), en el año hasta hoy."""
    validar_colegio_seleccionado(db, usuario, filtros.ids_colegio, id_colegio)
    hoy = hoy_lima()
    colegio = next(c for c in filtros.colegios if c.id_colegio == id_colegio)
    aportes = aportes_del_anio(db, filtros, [id_colegio])
    meses = _meses_del_intervalo(filtros, [id_colegio])
    por_grado: dict[int, list[Aporte]] = defaultdict(list)
    for aporte in aportes:
        por_grado[aporte.id_grado].append(aporte)
    grados = {g.id_grado: g for g in db.exec(select(Grado).where(col(Grado.id_grado).in_(list(por_grado)))).all()}
    alumnos = {a.id_alumno: a for a in db.exec(
        select(Alumno).where(col(Alumno.id_alumno).in_({a.id_alumno for a in aportes}))
    ).all()}
    detalle = {g: _alumnos_del_grado(por_grado[g], alumnos, meses, hoy) for g in por_grado}
    con_promedio = [g for g in sorted(por_grado) if acumular(por_grado[g]).promedio is not None]
    return LibrosDelColegio(
        contexto=filtros.contexto(),
        colegio=item_colegio(colegio),
        resumen=_resumen(acumular(aportes), _empieza_despues(filtros, [id_colegio], hoy)),
        meses=_meses(meses, aportes, hoy),
        grados=[
            _grado(posicion, grados[g], por_grado[g], detalle[g])
            for posicion, g in ranking(con_promedio, lambda g: acumular(por_grado[g]).promedio, lambda g: (g,))
        ],
        grados_sin_informacion=[
            _grado(None, grados[g], por_grado[g], detalle[g]) for g in sorted(por_grado) if g not in con_promedio
        ],
    )


# ── GET /dashboard/libros/desglose ──────────────────────────────────────────────────

def _validar_alumno(db: Session, usuario: Usuario, filtros: FiltrosResueltos, id_alumno: int) -> Alumno:
    """El alumno debe haber pertenecido en el año a alguno de los colegios seleccionados:
    si solo perteneció a colegios fuera del alcance, 403; si no, 422."""
    alumno = db.get(Alumno, id_alumno)
    if alumno is None:
        raise error_de_campo("id_alumno", "El alumno seleccionado no existe.")
    colegios = set(db.exec(select(AlumnoPertenencia.id_colegio).where(
        AlumnoPertenencia.id_alumno == id_alumno,
        AlumnoPertenencia.desde <= filtros.anio.fecha_fin,
        or_(col(AlumnoPertenencia.hasta).is_(None), col(AlumnoPertenencia.hasta) > filtros.anio.fecha_inicio),
    )).all())
    if colegios & set(filtros.ids_colegio):
        return alumno
    if colegios - {c.id_colegio for c in colegios_del_alcance(db, usuario)}:
        raise fuera_de_alcance()
    raise error_de_campo("id_alumno", "El alumno no pertenece a los colegios seleccionados en este año escolar.")


def _catalogos(db: Session, filas: list[FilaLectura]) -> dict[str, dict[int, str]]:
    tablas = [f.tabla for f in filas]
    consultas = {
        "programa": (Programa.id_programa, Programa.nombre, {t.id_programa for t in tablas}),
        "colegio": (Colegio.id_colegio, Colegio.nombre, {t.id_colegio for t in tablas}),
        "grado": (Grado.id_grado, Grado.nombre, {t.id_grado for t in tablas}),
        "seccion": (Seccion.id_seccion, Seccion.nombre, {t.id_seccion for t in tablas}),
    }
    return {
        nombre: dict(db.exec(select(clave, texto).where(col(clave).in_(ids))).all()) if ids else {}
        for nombre, (clave, texto, ids) in consultas.items()
    }


def _registro(fila: FilaLectura, catalogos: dict[str, dict[int, str]]) -> RegistroLibros:
    tabla = fila.tabla
    return RegistroLibros(
        id_lectura_semanal=tabla.id,
        clase_referencia=ClaseReferencia(id_clase=tabla.id_clase_referencia, fecha=fila.fecha),
        subprograma=ItemCatalogo(id=tabla.id_programa, nombre=catalogos["programa"][tabla.id_programa]),
        colegio=ItemCatalogo(id=tabla.id_colegio, nombre=catalogos["colegio"][tabla.id_colegio]),
        grado=ItemCatalogo(id=tabla.id_grado, nombre=catalogos["grado"][tabla.id_grado]),
        seccion=ItemCatalogo(id=tabla.id_seccion, nombre=catalogos["seccion"][tabla.id_seccion]),
        estado_tabla=tabla.estado, lsn=fila.lsn, lsl=fila.lsl, total=fila.cantidad,
    )


def desglose_libros(
    db: Session, usuario: Usuario, filtros: FiltrosResueltos, id_alumno: int, anio: int, mes: int
) -> DesgloseLibros:
    """Las filas del alumno en las tablas de los colegios seleccionados cuya clase de
    referencia es de ese mes, hasta hoy."""
    alumno = _validar_alumno(db, usuario, filtros, id_alumno)
    inicio = date(anio, mes, 1)
    fin = min(date(anio + mes // 12, mes % 12 + 1, 1) - timedelta(days=1), hoy_lima())
    filas = _filas(db, filtros.ids_colegio, inicio, fin, id_alumno) if inicio <= fin else []
    total = acumular(f.aporte(set()) for f in filas).total_visible
    catalogos = _catalogos(db, filas)
    return DesgloseLibros(
        contexto=filtros.contexto(), alumno=nombre_de_alumno(alumno), anio=anio, mes=mes, total=total,
        registros=[_registro(f, catalogos) for f in filas],
    )
