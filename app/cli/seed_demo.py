"""sembrar-demo: un colegio con aulas llenas e historial, para demostrar SICEDU con volumen.

Solo actúa con SEED_DEMO=true y ENTORNO en ENTORNOS_PERMITIDOS (dev, qa o uat). Es
independiente de los datos E2E: las pruebas de Playwright necesitan su colegio casi
vacío, y una demostración necesita lo contrario.

Siembra el colegio "I.E. Demostración SICEDU" con 3.º (42 alumnos, más de una página de
40) y 4.º (30), cada grado con su día de clase, y asigna ambos al Docente de demostración
en todos los periodos del año escolar vigente.

El Docente es la cuenta de DEMO_DOCENTE_CORREO. Si YA EXISTE, se usa tal cual: no se le
cambian la contraseña, los nombres ni el DNI (puede ser la cuenta de una persona del
equipo); solo debe ser un Docente activo. Si no existe, se crea con DEMO_DOCENTE_PASS. El
Supervisor y el Directivo de demostración son opcionales: se crean solo si llegan sus
contraseñas (DEMO_SUPERVISOR_PASS, DEMO_DIRECTIVO_PASS).

El HISTORIAL (asistencias, lectura semanal, rúbricas de los meses cerrados y Registro de
Vuelo de los PA terminados, desde el inicio del año hasta ayer) se registra a través de
los mismos servicios que usa la API, como operaciones del Docente dentro de actividades
de trabajo en esas fechas: las validaciones, la auditoría y los cálculos son los reales.
Los `id_operacion` son fijos, así que repetir el seed no duplica nada, y un día ya
generado se salta. Hoy queda libre para registrarlo en vivo. Como son miles de
operaciones contra la BD, en el contenedor corre en segundo plano después de arrancar la
API (docker-compose.yml) y retoma donde quedó si se interrumpe.

Los nombres de los alumnos son ficticios. No toca nada fuera del colegio de demostración
y sus cuentas.
"""
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Mapping, Optional

from sqlmodel import Session, col, select

from app.cli.catalogos import roles_por_nombre
from app.cli.comun import Resumen, id_supervisor_original
from app.cli.seed_e2e import (
    DOCENTE,
    CuentaE2E,
    _anio_escolar,
    _asegurar_asignaciones,
    _asegurar_colegio,
    _asegurar_cuenta,
    _limpiar_control_acceso,
    motivo_de_rechazo,
)
from app.core.correo import normalizar_correo
from app.core.errores import ErrorNegocio
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.evaluacion import NivelEsperadoPorGrado, SemanaReporte
from app.models.organizacion import (
    SECCION_UNICA,
    AnioEscolar,
    Alumno,
    AlumnoPertenencia,
    Colegio,
    Grado,
    Programa,
    Seccion,
    Usuario,
)
from app.models.seguridad import Sesion
from app.models.trazabilidad import Actividad
from app.schemas.asistencia import AsistenciaGuardar, RegistroAsistencia
from app.schemas.lectura import FilaLecturaGuardar, TablaLecturaCrear
from app.schemas.registro_vuelo import RegistroVueloGuardar
from app.schemas.rubrica import RubricaGuardar
from app.services import asistencia_service, lectura_service, registro_vuelo, rubrica_service
from app.services.calendario import cargar_calendarios, periodos_academicos_del_anio
from app.services.programacion import ClaseProgramada, FiltroGrupos, clases_programadas
from app.services.sesiones import CIERRE_ACTIVIDAD_MANUAL, CIERRE_MANUAL

ENTORNOS_PERMITIDOS = frozenset({"dev", "qa", "uat"})

COLEGIO = {
    "nombre": "I.E. Demostración SICEDU",
    "departamento": "Áncash",
    "provincia": "Huaraz",
    "distrito": "Independencia",
    "nivel_educativo": "Primaria",
}
PROGRAMAS = ("Alfabetización", "Comprensión Lectora")
# 3.º pasa de 40 alumnos a propósito: así se ve la segunda página de los listados.
ALUMNOS_POR_GRADO = {"3.º": 42, "4.º": 30}
GRADOS = tuple(ALUMNOS_POR_GRADO)
# Martes y jueves: cada grado en su día.
DIAS_DE_CLASE = {"3.º": 2, "4.º": 4}

# Apellido de las cuentas que crea el seed (no el de una cuenta existente).
APELLIDO_CUENTAS = "Demostración"
CORREO_DOCENTE = "juan.paredes+demo.docente@utec.edu.pe"
CORREO_SUPERVISOR = "juan.paredes+demo.supervisor@utec.edu.pe"
CORREO_DIRECTIVO = "juan.paredes+demo.directivo@utec.edu.pe"

# Nombres ficticios: se combinan de forma fija para que cada corrida dé los mismos alumnos.
NOMBRES = (
    "Abigail", "Adrián", "Alessandra", "Ángel", "Brenda", "Bruno", "Camila", "Carlos", "Daniela", "Diego",
    "Elena", "Emilio", "Fabiola", "Fernando", "Gabriela", "Gael", "Hilda", "Hugo", "Isabel", "Iván",
    "Jimena", "Joaquín", "Karen", "Kevin", "Lucía", "Luis", "Mariana", "Mateo", "Nayeli", "Nicolás",
    "Olivia", "Óscar", "Paola", "Renato", "Sofía", "Thiago", "Valeria", "Walter", "Ximena", "Yuri",
)
APELLIDOS = (
    "Alva", "Blas", "Cáceres", "Chávez", "Depaz", "Espinoza", "Flores", "Gamarra", "Huamán", "Jamanca",
    "León", "Maguiña", "Milla", "Ñique", "Obregón", "Palacios", "Quispe", "Ramírez", "Sánchez", "Tinoco",
    "Valverde", "Villanueva", "Yanac", "Zambrano",
)
TITULOS_LSN = (
    "El zorro y el cóndor", "La laguna de Llanganuco", "Mi abuela y el telar", "El puma curioso",
    "Viaje al nevado", "La semilla de quinua", "El río que cantaba", "Cuentos del Callejón",
)
OBSERVACIONES_AUSENCIA = ("Faltó por enfermedad.", "Viaje familiar.", "Sin justificación.")

# Espacio de nombres de los uuid fijos del seed (actividades, sesiones y operaciones).
ESPACIO = uuid.UUID("5d1c9a6e-3f0b-4b7e-9a51-2c8e7d4f6a10")
# Lima es UTC-5 todo el año: 13:00 UTC son las 8:00 de la mañana.
INICIO_JORNADA = time(13, 0, tzinfo=timezone.utc)
DURACION_JORNADA = timedelta(hours=4)


def uuid_fijo(*partes) -> uuid.UUID:
    return uuid.uuid5(ESPACIO, "|".join(str(p) for p in partes))


def entorno_permitido(entorno: Mapping[str, str]) -> Optional[str]:
    """Por qué no se siembra en este entorno, o None si se puede. Sin la BD."""
    if entorno.get("SEED_DEMO", "").strip().lower() != "true":
        return "SEED_DEMO no es true"
    return motivo_de_rechazo(entorno, ENTORNOS_PERMITIDOS)


def alumnos_ficticios(grado: str, cantidad: int) -> list[tuple[str, str, str]]:
    """(nombres, apellidos, programa) de cada alumno del grado. Sin repetidos; uno de cada
    tres en Alfabetización y el resto en Comprensión Lectora."""
    desplazamiento = GRADOS.index(grado) * 11
    alumnos = []
    for i in range(cantidad):
        nombre = NOMBRES[(i * 7 + desplazamiento) % len(NOMBRES)]
        paterno = APELLIDOS[(i + desplazamiento) % len(APELLIDOS)]
        materno = APELLIDOS[(i * 5 + 3 + desplazamiento) % len(APELLIDOS)]
        programa = PROGRAMAS[0] if i % 3 == 0 else PROGRAMAS[1]
        alumnos.append((nombre, f"{paterno} {materno}", programa))
    return alumnos


def semilla(*partes) -> int:
    """Un entero estable para elegir valores "al azar" sin que cambien entre corridas."""
    return uuid_fijo(*partes).int % 1000


# ── Cuentas ─────────────────────────────────────────────────────────────────────────

def _docente(
    db: Session, entorno: Mapping[str, str], roles: dict, id_autor: int, resumen: Resumen
) -> Optional[Usuario]:
    """La cuenta de DEMO_DOCENTE_CORREO: la existente sin tocarla, o una nueva."""
    correo = normalizar_correo(entorno.get("DEMO_DOCENTE_CORREO", "").strip() or CORREO_DOCENTE)
    existente = db.exec(select(Usuario).where(Usuario.correo == correo)).first()
    if existente is None:
        cuenta = CuentaE2E("DEMO_DOCENTE_PASS", DOCENTE, "Docente", APELLIDO_CUENTAS, "88000001", correo, True)
        return _asegurar_cuenta(db, cuenta, entorno, roles, id_autor, resumen, etiqueta="demo")
    if existente.id_rol != roles[DOCENTE].id_rol or existente.id_docente is None or not existente.activo:
        resumen.error(f"No se siembran los datos demo: {correo} no es una cuenta de Docente activa")
        return None
    return existente


def _cuentas_opcionales(db: Session, entorno: Mapping[str, str], roles: dict, id_autor: int, resumen: Resumen) -> None:
    """Supervisor y Directivo de demostración, solo si llegan sus contraseñas."""
    cuentas = (
        CuentaE2E("DEMO_SUPERVISOR_PASS", "Supervisor", "Supervisora", APELLIDO_CUENTAS, "88000002",
                  entorno.get("DEMO_SUPERVISOR_CORREO", "").strip() or CORREO_SUPERVISOR, True),
        CuentaE2E("DEMO_DIRECTIVO_PASS", "Directivo", "Directivo", APELLIDO_CUENTAS, "88000003",
                  entorno.get("DEMO_DIRECTIVO_CORREO", "").strip() or CORREO_DIRECTIVO, True),
    )
    presentes = [c for c in cuentas if entorno.get(c.variable, "").strip()]
    for cuenta in presentes:
        _asegurar_cuenta(db, cuenta, entorno, roles, id_autor, resumen, etiqueta="demo")
    if presentes:
        _limpiar_control_acceso(db, presentes)


# ── Alumnos ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Grupo:
    id_grado: int
    id_seccion: int
    alumnos: tuple[Alumno, ...]


def _seccion_unica(db: Session, id_colegio: int, id_grado: int) -> Seccion:
    return db.exec(select(Seccion).where(
        Seccion.id_colegio == id_colegio, Seccion.id_grado == id_grado, Seccion.nombre == SECCION_UNICA,
    )).one()


def _asegurar_alumnos(
    db: Session, colegio: Colegio, grados: dict[str, Grado], programas: dict[str, int], desde: date,
    id_autor: int, resumen: Resumen,
) -> list[Grupo]:
    """Los alumnos de cada grado en su sección "Única", con su pertenencia desde el inicio
    del año. Los que ya existen (mismo nombre en el grado) no se tocan."""
    grupos = []
    for nombre_grado, cantidad in ALUMNOS_POR_GRADO.items():
        grado = grados[nombre_grado]
        seccion = _seccion_unica(db, colegio.id_colegio, grado.id_grado)
        existentes = {
            (a.nombres, a.apellidos): a
            for a in db.exec(select(Alumno).where(
                Alumno.id_colegio == colegio.id_colegio, Alumno.id_grado == grado.id_grado,
            )).all()
        }
        alumnos = []
        for nombres, apellidos, programa in alumnos_ficticios(nombre_grado, cantidad):
            alumno = existentes.get((nombres, apellidos))
            if alumno is None:
                alumno = _crear_alumno(db, colegio, grado, seccion, programas[programa], (nombres, apellidos),
                                       desde, id_autor)
                resumen.creados["alumno demo"] += 1
            alumnos.append(alumno)
        db.commit()
        grupos.append(Grupo(grado.id_grado, seccion.id_seccion, tuple(alumnos)))
    return grupos


def _crear_alumno(
    db: Session, colegio: Colegio, grado: Grado, seccion: Seccion, id_programa: int, nombre: tuple[str, str],
    desde: date, id_autor: int,
) -> Alumno:
    ahora = ahora_utc()
    auditoria = {"creado_por": id_autor, "creado_en": ahora}
    alumno = Alumno(
        nombres=nombre[0], apellidos=nombre[1], id_colegio=colegio.id_colegio, id_grado=grado.id_grado,
        id_seccion=seccion.id_seccion, id_programa_actual=id_programa, fecha_registro=desde, activo=True,
        **auditoria,
    )
    db.add(alumno)
    db.flush()
    db.add(AlumnoPertenencia(
        id_alumno=alumno.id_alumno, id_colegio=colegio.id_colegio, id_grado=grado.id_grado,
        id_seccion=seccion.id_seccion, id_programa=id_programa, desde=desde, **auditoria,
    ))
    return alumno


# ── Historial ───────────────────────────────────────────────────────────────────────

@dataclass
class Contexto:
    """Lo que el historial necesita a mano para no volver a consultarlo en cada día."""

    docente: Usuario
    colegio: Colegio
    anio: AnioEscolar
    grupos: dict[tuple[int, int], Grupo]
    letra_esperada: dict[int, int]
    hoy: date
    resumen: Resumen


def _actividad_del_dia(db: Session, docente: Usuario, fecha: date) -> Actividad:
    """La sesión y la actividad de trabajo del Docente ese día (de 8:00 a 12:00), ya
    cerradas. Fijas por fecha: una corrida posterior encuentra las mismas."""
    id_actividad = uuid_fijo("actividad", docente.id_usuario, fecha)
    actividad = db.get(Actividad, id_actividad)
    if actividad is not None:
        return actividad
    inicio = datetime.combine(fecha, INICIO_JORNADA)
    fin = inicio + DURACION_JORNADA
    sesion = Sesion(
        id_sesion=uuid_fijo("sesion", docente.id_usuario, fecha), id_usuario=docente.id_usuario,
        inicio=inicio - timedelta(minutes=5), expira=inicio + timedelta(hours=8), fin=fin + timedelta(minutes=5),
        tipo_cierre=CIERRE_MANUAL, ultima_peticion_en=fin,
    )
    actividad = Actividad(
        id_actividad=id_actividad, id_sesion=sesion.id_sesion, id_docente=docente.id_docente, inicio=inicio,
        fin=fin, tipo_cierre=CIERRE_ACTIVIDAD_MANUAL, sincronizado_en=inicio,
    )
    db.add(sesion)
    db.flush()
    db.add(actividad)
    db.commit()
    return actividad


def _operacion(actividad: Actividad, minutos: int, *clave) -> dict:
    """Los campos de operación de un ítem: su id fijo, su hora real y su actividad."""
    return {
        "id_operacion": uuid_fijo(*clave),
        "realizado_en": actividad.inicio + timedelta(minutes=minutos),
        "id_actividad": actividad.id_actividad,
    }


def estado_de_asistencia(id_alumno: int, fecha: date) -> str:
    valor = semilla("asistencia", id_alumno, fecha)
    if valor < 80:
        return "ausente"
    if valor < 160:
        return "tardanza"
    return "presente"


def _asistencias(db: Session, ctx: Contexto, clase: ClaseProgramada, grupo: Grupo, actividad: Actividad) -> dict:
    """Registra la asistencia de la clase; devuelve {id_alumno: estado}."""
    estados, registros = {}, []
    for alumno in grupo.alumnos:
        estado = estado_de_asistencia(alumno.id_alumno, clase.fecha)
        estados[alumno.id_alumno] = estado
        extra = {}
        if estado == "ausente":
            extra["observacion"] = OBSERVACIONES_AUSENCIA[semilla("obs", alumno.id_alumno, clase.fecha) % 3]
        registros.append(RegistroAsistencia(
            **_operacion(actividad, 10, "asistencia", alumno.id_alumno, clase.fecha),
            id_alumno=alumno.id_alumno, estado=estado, **extra,
        ))
    asistencia_service.guardar_asistencia(db, ctx.docente, AsistenciaGuardar(
        id_colegio=ctx.colegio.id_colegio, id_grado=grupo.id_grado, id_seccion=grupo.id_seccion,
        fecha=clase.fecha, registros=registros,
    ))
    return estados


def _semana(db: Session, ctx: Contexto, fecha: date) -> Optional[SemanaReporte]:
    return db.exec(select(SemanaReporte).where(
        SemanaReporte.id_anio_escolar == ctx.anio.id_anio_escolar,
        SemanaReporte.fecha_inicio <= fecha, SemanaReporte.fecha_fin >= fecha,
    )).first()


def _fila_de_lectura(id_alumno: int, fecha: date) -> dict:
    """Lo que leyó el alumno esa semana: casi siempre LSL y, seis de cada diez, un LSN."""
    valor = semilla("lectura", id_alumno, fecha)
    fila: dict = {"cantidad_lsl": valor % 4}
    if valor % 10 < 6:
        maximo = 10 if valor % 2 else 5
        fila.update(lsn_titulo=TITULOS_LSN[valor % len(TITULOS_LSN)], lsn_maximo=maximo,
                    lsn_nota=maximo - valor % 3)
    return fila


def _lectura(
    db: Session, ctx: Contexto, clase: ClaseProgramada, grupo: Grupo, actividad: Actividad, estados: dict
) -> None:
    """Crea la tabla de la semana de cada subprograma y registra a los que asistieron."""
    semana = _semana(db, ctx, clase.fecha)
    if semana is None:
        return
    for id_programa in sorted({a.id_programa_actual for a in grupo.alumnos}):
        crear = TablaLecturaCrear(
            **_operacion(actividad, 30, "lectura-tabla", grupo.id_seccion, id_programa, semana.id_semana),
            id_colegio=ctx.colegio.id_colegio, id_programa=id_programa, id_grado=grupo.id_grado,
            id_seccion=grupo.id_seccion, id_anio_escolar=ctx.anio.id_anio_escolar, mes=clase.fecha.month,
            id_semana=semana.id_semana, fecha_clase_referencia=clase.fecha,
        )
        tabla = lectura_service.crear_tabla(db, ctx.docente, crear)
        filas = [
            FilaLecturaGuardar(
                **_operacion(actividad, 45, "lectura-fila", tabla.id, fila.id_alumno),
                version=fila.version, id_alumno=fila.id_alumno, **_fila_de_lectura(fila.id_alumno, clase.fecha),
            )
            for fila in tabla.filas
            if estados.get(fila.id_alumno) in ("presente", "tardanza")
        ]
        if filas:
            lectura_service.guardar_filas(db, ctx.docente, tabla.id, filas)


def nivel_de_rubrica(id_alumno: int, mes: int, programa: str, dimension: str) -> int:
    """Un nivel que mejora con los meses: entre En proceso (2) y Avanzado (4); en
    Alfabetización la fluidez puede empezar en Pre-inicio (0)."""
    base = semilla("rubrica", id_alumno, dimension) % 3
    minimo = 0 if programa == PROGRAMAS[0] and dimension == "fluidez" else 1
    return max(minimo, min(4, base + (mes - 3) // 3 + 1))


def _rubricas(db: Session, ctx: Contexto, clase: ClaseProgramada, grupo: Grupo, actividad: Actividad,
              programas: dict[int, str]) -> None:
    mes = clase.fecha.month
    items = [
        RubricaGuardar(
            **_operacion(actividad, 60, "rubrica", alumno.id_alumno, ctx.anio.id_anio_escolar, mes),
            id_alumno=alumno.id_alumno, id_colegio=ctx.colegio.id_colegio, id_grado=grupo.id_grado,
            id_seccion=grupo.id_seccion, id_programa=alumno.id_programa_actual,
            id_anio_escolar=ctx.anio.id_anio_escolar, mes=mes,
            fluidez=nivel_de_rubrica(alumno.id_alumno, mes, programas[alumno.id_programa_actual], "fluidez"),
            comprension=nivel_de_rubrica(alumno.id_alumno, mes, programas[alumno.id_programa_actual], "comprension"),
        )
        for alumno in grupo.alumnos
    ]
    rubrica_service.guardar(db, ctx.docente, items)


def nivel_colocado(id_alumno: int, esperado: int, numero_pa: int) -> int:
    """Una letra Raz-Kids cerca de la esperada para el grado, que sube con cada PA."""
    desvio = semilla("vuelo", id_alumno) % 5 - 2
    return max(1, min(27, esperado + desvio + numero_pa - 1))


def _registro_vuelo(db: Session, ctx: Contexto, clase: ClaseProgramada, grupo: Grupo, actividad: Actividad,
                    numero_pa: int) -> None:
    esperado = ctx.letra_esperada.get(grupo.id_grado, 1)
    items = [
        RegistroVueloGuardar(
            **_operacion(actividad, 90, "vuelo", alumno.id_alumno, clase.id_periodo_academico),
            id_alumno=alumno.id_alumno, id_periodo_academico=clase.id_periodo_academico,
            id_colegio=ctx.colegio.id_colegio, id_grado=grupo.id_grado, id_seccion=grupo.id_seccion,
            id_programa=alumno.id_programa_actual,
            id_nivel_colocado=nivel_colocado(alumno.id_alumno, esperado, numero_pa),
        )
        for alumno in grupo.alumnos
    ]
    registro_vuelo.guardar(db, ctx.docente, items)


@dataclass(frozen=True)
class Cierres:
    """Qué cierra una clase: el último de su mes ya terminado y el último de su PA ya
    terminado (para la rúbrica y el Registro de Vuelo)."""

    mes: bool
    numero_pa: Optional[int]


def cierres_de(clases: list[ClaseProgramada], hoy: date, numero_de_pa: Mapping[int, int],
               fin_de_pa: Mapping[int, date]) -> dict[tuple[int, date], Cierres]:
    """{(id_seccion, fecha): Cierres} de cada clase, mirando la siguiente del mismo grupo."""
    por_grupo: dict[int, list[ClaseProgramada]] = defaultdict(list)
    for clase in clases:
        por_grupo[clase.id_seccion].append(clase)
    cierres = {}
    for lista in por_grupo.values():
        lista.sort(key=lambda c: c.fecha)
        for actual, siguiente in zip(lista, [*lista[1:], None], strict=True):
            fin_mes = (actual.fecha.year, actual.fecha.month) < (hoy.year, hoy.month) and (
                siguiente is None or siguiente.fecha.month != actual.fecha.month
            )
            pa = actual.id_periodo_academico
            fin_pa = fin_de_pa.get(pa) is not None and fin_de_pa[pa] < hoy and (
                siguiente is None or siguiente.id_periodo_academico != pa
            )
            cierres[(actual.id_seccion, actual.fecha)] = Cierres(fin_mes, numero_de_pa.get(pa) if fin_pa else None)
    return cierres


def _dia(db: Session, ctx: Contexto, fecha: date, clases: list[ClaseProgramada],
         cierres: dict[tuple[int, date], Cierres], programas: dict[int, str]) -> None:
    """Todo lo de un día de clase, en una actividad. Un día terminado se salta."""
    actividad = _actividad_del_dia(db, ctx.docente, fecha)
    if actividad.sincronizado_en == actividad.fin:
        return
    for clase in clases:
        grupo = ctx.grupos.get((clase.id_grado, clase.id_seccion))
        if grupo is None:
            continue
        try:
            estados = _asistencias(db, ctx, clase, grupo, actividad)
            _lectura(db, ctx, clase, grupo, actividad, estados)
            cierre = cierres[(clase.id_seccion, clase.fecha)]
            if cierre.mes:
                _rubricas(db, ctx, clase, grupo, actividad, programas)
            if cierre.numero_pa is not None:
                _registro_vuelo(db, ctx, clase, grupo, actividad, cierre.numero_pa)
        except ErrorNegocio as e:
            db.rollback()
            ctx.resumen.advertir(f"Historial demo del {fecha.isoformat()}: {e.motivo or e.detail}")
            return
    # Marca de día terminado: la última sincronización vuelve a ser el fin de la actividad.
    actividad = db.get(Actividad, actividad.id_actividad)
    actividad.sincronizado_en = actividad.fin
    db.add(actividad)
    db.commit()
    ctx.resumen.creados["día de historial demo"] += 1


def generar_historial(db: Session, ctx: Contexto, desde: date) -> None:
    """Del inicio del año hasta ayer, día por día y en orden."""
    hasta = ctx.hoy - timedelta(days=1)
    if hasta < desde:
        return
    calendarios = cargar_calendarios(db, ctx.anio.id_anio_escolar, ids_colegio=[ctx.colegio.id_colegio])
    clases = clases_programadas(db, FiltroGrupos(ctx.colegio.id_colegio), desde, hasta, calendarios)
    periodos = periodos_academicos_del_anio(db, ctx.anio.id_anio_escolar)
    calendario = calendarios.aplicable(ctx.colegio.id_colegio, ctx.anio.id_anio_escolar)
    numero_de_pa = {p.id_periodo_academico: p.numero for p in periodos}
    fin_de_pa = {
        p.id_periodo_academico: pa.fecha_fin
        for p in periodos
        if calendario is not None and (pa := calendario.pa(p.numero)) is not None
    }
    cierres = cierres_de(clases, ctx.hoy, numero_de_pa, fin_de_pa)
    programas = dict(db.exec(select(Programa.id_programa, Programa.nombre)).all())
    por_dia: dict[date, list[ClaseProgramada]] = defaultdict(list)
    for clase in clases:
        por_dia[clase.fecha].append(clase)
    for fecha in sorted(por_dia):
        _dia(db, ctx, fecha, por_dia[fecha], cierres, programas)


# ── Paso ────────────────────────────────────────────────────────────────────────────

def _inicio_del_anio(db: Session, colegio: Colegio, anio: AnioEscolar) -> Optional[date]:
    calendario = cargar_calendarios(db, anio.id_anio_escolar, ids_colegio=[colegio.id_colegio]).aplicable(
        colegio.id_colegio, anio.id_anio_escolar
    )
    pa1 = calendario.pa(1) if calendario is not None else None
    return pa1.fecha_inicio if pa1 is not None else None


def _por_nombre(filas: Iterable, atributo: str) -> dict:
    return {getattr(f, atributo): f for f in filas}


def sembrar_demo(db: Session, entorno: Optional[Mapping[str, str]] = None, con_historial: bool = True) -> Resumen:
    resumen = Resumen()
    entorno = os.environ if entorno is None else entorno
    motivo = entorno_permitido(entorno)
    if motivo == "SEED_DEMO no es true":
        resumen.omitir(f"datos demo: {motivo}")
        return resumen
    if motivo is not None:
        resumen.error(f"No se siembran los datos demo: {motivo}")
        return resumen

    id_autor = id_supervisor_original(db)
    anio = _anio_escolar(db)
    if id_autor is None or anio is None:
        resumen.advertir("No se siembran los datos demo: falta el Supervisor original o el calendario")
        return resumen
    grados = _por_nombre(db.exec(select(Grado).where(col(Grado.nombre).in_(GRADOS))).all(), "nombre")
    programas = _por_nombre(db.exec(select(Programa).where(col(Programa.nombre).in_(PROGRAMAS))).all(), "nombre")
    if len(grados) != len(GRADOS) or len(programas) != len(PROGRAMAS):
        resumen.error("No se siembran los datos demo: faltan grados o programas (¿se ejecutó cargar-catalogos?)")
        return resumen

    roles = roles_por_nombre(db)
    docente = _docente(db, entorno, roles, id_autor, resumen)
    _cuentas_opcionales(db, entorno, roles, id_autor, resumen)
    if docente is None:
        return resumen

    colegio = _asegurar_colegio(db, [grados[n] for n in GRADOS], list(programas.values()), id_autor, resumen,
                                datos=COLEGIO, dias=DIAS_DE_CLASE, etiqueta="demo")
    periodos = periodos_academicos_del_anio(db, anio.id_anio_escolar)
    _asegurar_asignaciones(db, docente.id_docente, colegio, grados, periodos, id_autor, resumen,
                           nombres_grados=GRADOS, etiqueta="demo")
    desde = _inicio_del_anio(db, colegio, anio)
    if desde is None:
        resumen.advertir("No se siembran alumnos demo: el colegio no tiene PA1 en su calendario")
        return resumen
    ids_programa = {nombre: p.id_programa for nombre, p in programas.items()}
    grupos = _asegurar_alumnos(db, colegio, grados, ids_programa, desde, id_autor, resumen)

    if con_historial:
        esperadas = dict(db.exec(
            select(NivelEsperadoPorGrado.id_grado, NivelEsperadoPorGrado.id_nivel_rk_esperado)
        ).all())
        ctx = Contexto(docente, colegio, anio, {(g.id_grado, g.id_seccion): g for g in grupos}, esperadas,
                       hoy_lima(), resumen)
        generar_historial(db, ctx, desde)
    return resumen
