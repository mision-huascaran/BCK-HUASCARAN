"""Alumnos (CU014).

- Supervisor: alcance global; crea y edita alumnos de cualquier colegio sin actividad.
- Docente: solo alumnos de su alcance (pares colegio + grado vigentes); toda escritura
  requiere una actividad activa (CU010), que queda vinculada a la auditoría.
- Ciclo EBR: se calcula (app/core/ciclo.py). Sección: una de su grado (CU013); se
  asigna sola si el grado tiene una sola activa.
- Pertenencia histórica (app/services/pertenencia.py): crear, cambiar de grupo,
  inactivar y activar llevan una fecha efectiva (hoy o antes) y abren o cierran
  intervalos en la misma transacción. Corregir nombres no la toca.
- Borrado lógico: activar / inactivar; ningún alumno se elimina.
- Colegio inactivo (CU013): no afecta a sus alumnos. El Supervisor puede corregir sus
  nombres y apellidos; cambiar su ubicación (colegio, grado, subprograma) o activarlo
  exige un colegio activo. No salen en el listado (tampoco para el Supervisor); el
  Docente no los ve: quedan fuera de su alcance.
- Alumno con pertenencia pendiente (sin intervalos, CU014): puede no tener grado,
  subprograma ni sección. Se edita sin fecha hasta que se confirme su grupo, activarlo o
  inactivarlo no toca intervalos y el Docente lo alcanza por colegio (DP-4, DP-5).
"""
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.ciclo import calcular_ciclo, ciclo_sql, programa_permitido_en_grado
from app.core.errores import ErrorNegocio, campo_obligatorio, error_de_campo, errores_de_campos
from app.core.paginacion import Paginado, pagina, paginar
from app.core.texto import comparable_sql, palabras_buscadas
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    Alumno,
    CicloEbr,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    Grado,
    Programa,
    Seccion,
    Usuario,
)
from app.schemas.alumno import AlumnoCrear, AlumnoDetalle, AlumnoEditar, AlumnoItem, CambioEstadoAlumno
from app.schemas.comun import ItemCatalogo
from app.services.actividad import requerir_actividad_activa
from app.services.alcance import alcance_docente
from app.services import pertenencia
from app.services.auditoria import ACTIVAR, CREAR, EDITAR, INACTIVAR, registrar_auditoria
from app.services.habilitacion import pares_visibles
from app.services.pertenencia import CAMPO_AUDITORIA, Movimiento
from app.services.secciones import seccion_para
from app.services.usuario_service import DOCENTE, nombre_rol

TABLA = "alumno"
CAMPO_PERTENECE_DESDE = "pertenece_desde"
CAMPO_CAMBIO_GRUPO_DESDE = "cambio_grupo_desde"
CAMPO_APLICAR_DESDE = "aplicar_desde"
CAMPO_DNI = "dni"
MENSAJE_FECHA_DE_CAMBIO = "Indica desde qué fecha el alumno pertenece a su nuevo grupo."
MENSAJE_CONFIRMA_PERTENENCIA = "Confirma desde qué fecha el alumno forma parte de este grupo."
MENSAJE_YA_TIENE_PERTENENCIA = "El alumno ya tiene un inicio de pertenencia registrado."
MENSAJE_USA_PERTENECE_DESDE = (
    "Este alumno todavía no tiene un inicio de pertenencia: usa 'Pertenece al grupo desde'."
)
MENSAJE_ACTIVA_ANTES = "Activa al alumno antes de confirmar desde qué fecha forma parte de su grupo."
MENSAJE_GRUPO_INCOMPLETO = (
    "Completa el grado, el subprograma y la sección, o deja vacíos el subprograma y la sección."
)
MENSAJE_DNI_FORMATO = "El DNI debe tener exactamente 8 dígitos."
MENSAJE_DNI_DUPLICADO = "Ya existe un alumno registrado con ese DNI."
PERTENENCIA_PENDIENTE = "pendiente"

# Campo auditado -> atributo del modelo (el subprograma se guarda en `id_programa_actual`).
CAMPOS_AUDITADOS = {
    "nombres": "nombres",
    "apellidos": "apellidos",
    "dni": "dni",
    "id_colegio": "id_colegio",
    "id_grado": "id_grado",
    "id_seccion": "id_seccion",
    "id_programa": "id_programa_actual",
}
# Los que definen su grupo: si alguno cambia, se vuelve a validar su estado y cambia su
# pertenencia.
CAMPOS_DE_UBICACION = ("id_colegio", "id_grado", "id_seccion", "id_programa")
# Los que un alumno con pertenencia pendiente puede no tener.
CAMPOS_DEL_GRUPO = ("id_grado", "id_programa", "id_seccion")

Alcance = Optional[set[tuple[int, int]]]


# ── Errores ─────────────────────────────────────────────────────────────────────────

def _no_procesable(detail: str, motivo: str) -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_422_UNPROCESSABLE_CONTENT, detail, motivo=motivo)


def _fuera_de_alcance() -> ErrorNegocio:
    return ErrorNegocio(
        status.HTTP_403_FORBIDDEN,
        "El alumno no pertenece a un colegio y grado que tenga asignados.",
        motivo="alumno_fuera_de_alcance",
    )


# ── Alcance ─────────────────────────────────────────────────────────────────────────

def alcance_de(db: Session, usuario: Usuario, lectura: bool = False, en: Optional[datetime] = None) -> Alcance:
    """Pares (colegio, grado) del Docente; None (sin recorte) para el Supervisor.

    Escritura: sus asignaciones del PA vigente de cada colegio. Lectura (`lectura`):
    además, los grupos de sus asignaciones retroactivas en su PR y de sus permisos de
    corrección vigentes (CU028), en el instante `en` (ahora por defecto; solo se usa en
    lectura). Un conjunto vacío significa "no tiene nada a cargo", que no es lo mismo que
    None.
    """
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return None
    if usuario.id_docente is None:
        return set()
    if lectura:
        return pares_visibles(db, usuario, en)
    return alcance_docente(db, usuario.id_docente)


def _exigir_alcance(alcance: Alcance, id_colegio: int, id_grado: int) -> None:
    if alcance is not None and (id_colegio, id_grado) not in alcance:
        raise _fuera_de_alcance()


def _exigir_alcance_sobre(alcance: Alcance, alumno: Alumno, pendiente: bool) -> None:
    """Un alumno con pertenencia pendiente se alcanza por su colegio, sin importar su
    grado (DP-4); los demás, por el par (colegio, grado)."""
    if not pendiente:
        _exigir_alcance(alcance, alumno.id_colegio, alumno.id_grado)
    elif alcance is not None and alumno.id_colegio not in {c for c, _ in alcance}:
        raise _fuera_de_alcance()


# ── Respuestas ──────────────────────────────────────────────────────────────────────

def _consulta_items():
    """Alumno con su colegio, grado, sección, programa, el ciclo de su grado y si tiene la
    pertenencia pendiente, en una sola consulta. Outer joins: el grupo puede faltar."""
    return (
        select(Alumno, Colegio, Grado, Seccion, Programa, CicloEbr.nombre, pertenencia.pendiente_sql())
        .join(Colegio, Colegio.id_colegio == Alumno.id_colegio)
        .outerjoin(Grado, Grado.id_grado == Alumno.id_grado)
        .outerjoin(Seccion, Seccion.id_seccion == Alumno.id_seccion)
        .outerjoin(CicloEbr, CicloEbr.id_ciclo == Grado.id_ciclo)
        .outerjoin(Programa, Programa.id_programa == Alumno.id_programa_actual)
    )


def _catalogo(id_item: Optional[int], fila: Any) -> Optional[ItemCatalogo]:
    return ItemCatalogo(id=id_item, nombre=fila.nombre) if fila is not None else None


def _item(fila) -> AlumnoItem:
    alumno, colegio, grado, seccion, programa, ciclo_grado, pendiente = fila
    return AlumnoItem(
        id=alumno.id_alumno,
        nombres=alumno.nombres,
        apellidos=alumno.apellidos,
        dni=alumno.dni,
        colegio=ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre),
        grado=_catalogo(alumno.id_grado, grado),
        programa=_catalogo(alumno.id_programa_actual, programa),
        ciclo=calcular_ciclo(programa.nombre, ciclo_grado) if programa is not None else None,
        seccion=_catalogo(alumno.id_seccion, seccion),
        activo=alumno.activo,
        pertenencia_pendiente=pendiente,
    )


def _fila(db: Session, id_alumno: int):
    fila = db.exec(_consulta_items().where(Alumno.id_alumno == id_alumno)).first()
    if fila is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el alumno.", motivo="alumno_no_encontrado")
    return fila


def item_de(db: Session, id_alumno: int) -> AlumnoItem:
    return _item(_fila(db, id_alumno))


def alumno_visible(db: Session, usuario: Usuario, id_alumno: int) -> Alumno:
    """El alumno, si existe (404) y está en el alcance de quien pregunta (403)."""
    fila = _fila(db, id_alumno)
    _exigir_alcance_sobre(alcance_de(db, usuario, lectura=True), fila[0], fila[-1])
    return fila[0]


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def listar_alumnos(
    db: Session,
    usuario: Usuario,
    id_colegio: Optional[int],
    id_programa: Optional[int],
    ciclo: Optional[str],
    id_grado: Optional[int],
    activo: bool,
    q: Optional[str],
    page: int,
    id_seccion: Optional[int] = None,
    estado_pertenencia: Optional[str] = None,
) -> Paginado[AlumnoItem]:
    """Listado paginado, ordenado por apellidos y nombres sin distinguir mayúsculas ni
    tildes, y por `id_alumno` si empatan.

    `q`: cada palabra debe aparecer en "nombres apellidos", sin distinguir mayúsculas ni
    tildes y en cualquier orden ("juan perez" encuentra a "Juan Carlos Pérez Quispe").
    Los caracteres % y _ se buscan literalmente.

    `estado_pertenencia`: `definida` o `pendiente`; sin él, todos. Un alumno sin grado,
    sección o subprograma no coincide con los filtros por ese dato.
    """
    alcance = alcance_de(db, usuario, lectura=True)
    if alcance is not None and not alcance:
        return pagina([], 0, page)

    # CU014: el contexto global del Supervisor es el de los colegios activos (al Docente
    # ya lo recorta `pares_visibles`). Un `id_colegio` inactivo da una lista vacía.
    consulta = _consulta_items().where(Alumno.activo == activo, col(Colegio.activo).is_(True))
    pendiente = pertenencia.pendiente_sql()
    if alcance is not None:
        consulta = consulta.where(
            or_(
                and_(pendiente, col(Alumno.id_colegio).in_({c for c, _ in alcance})),
                *(and_(Alumno.id_colegio == c, Alumno.id_grado == g) for c, g in alcance),
            )
        )
    if estado_pertenencia is not None:
        consulta = consulta.where(pendiente if estado_pertenencia == PERTENENCIA_PENDIENTE else ~pendiente)
    if id_colegio is not None:
        consulta = consulta.where(Alumno.id_colegio == id_colegio)
    if id_grado is not None:
        consulta = consulta.where(Alumno.id_grado == id_grado)
    if id_seccion is not None:
        consulta = consulta.where(Alumno.id_seccion == id_seccion)
    if id_programa is not None:
        consulta = consulta.where(Alumno.id_programa_actual == id_programa)
    if ciclo is not None:
        consulta = consulta.where(ciclo_sql(Programa.nombre, CicloEbr.nombre) == ciclo)
    if q and q.strip():
        nombre_completo = comparable_sql(Alumno.nombres + " " + Alumno.apellidos)
        for palabra in palabras_buscadas(q):
            consulta = consulta.where(nombre_completo.contains(palabra, autoescape=True))
    consulta = consulta.order_by(comparable_sql(Alumno.apellidos), comparable_sql(Alumno.nombres), Alumno.id_alumno)

    filas, total = paginar(db, consulta, page)
    return pagina([_item(f) for f in filas], total, page)


def obtener_alumno(db: Session, usuario: Usuario, id_alumno: int) -> AlumnoDetalle:
    fila = _fila(db, id_alumno)
    alumno = fila[0]
    _exigir_alcance_sobre(alcance_de(db, usuario, lectura=True), alumno, fila[-1])
    actual = pertenencia.vigente(db, id_alumno)
    return AlumnoDetalle(
        **_item(fila).model_dump(),
        fecha_registro=alumno.fecha_registro,
        pertenece_desde=actual.desde if actual else None,
    )


# ── Validación del estado del alumno ────────────────────────────────────────────────

def _colegio_activo(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    if not colegio.activo:
        raise _no_procesable(f"El colegio {colegio.nombre} está inactivo.", "colegio_inactivo")
    return colegio


def _grado_del_colegio(db: Session, id_colegio: int, id_grado: int) -> Optional[Grado]:
    return db.exec(
        select(Grado)
        .join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
        .where(ColegioGrado.id_colegio == id_colegio, Grado.id_grado == id_grado)
    ).first()


def _grado_ofrecido(db: Session, colegio: Colegio, id_grado: int) -> Grado:
    grado = _grado_del_colegio(db, colegio.id_colegio, id_grado)
    if grado is None:
        raise _no_procesable(f"El colegio {colegio.nombre} no ofrece ese grado.", "grado_no_ofrecido")
    return grado


def validar_estado(db: Session, id_colegio: int, id_grado: int, id_programa: int) -> None:
    """Colegio activo, grado y programa ofrecidos por el colegio, y regla de 1.º."""
    colegio = _colegio_activo(db, id_colegio)
    grado = _grado_ofrecido(db, colegio, id_grado)
    programa = db.exec(
        select(Programa)
        .join(ColegioPrograma, ColegioPrograma.id_programa == Programa.id_programa)
        .where(ColegioPrograma.id_colegio == id_colegio, Programa.id_programa == id_programa)
    ).first()
    if programa is None:
        raise _no_procesable(f"El colegio {colegio.nombre} no ofrece ese subprograma.", "programa_no_ofrecido")
    if not programa_permitido_en_grado(grado.nombre, programa.nombre):
        raise _no_procesable(
            "En 1.º grado todos los alumnos pertenecen a Alfabetización.",
            "primer_grado_solo_alfabetizacion",
        )


def _fecha_efectiva(fecha: date) -> dict[str, tuple[Any, Any]]:
    """Campo virtual `pertenencia` de la auditoría: la fecha efectiva del evento (CU015)."""
    return {CAMPO_AUDITORIA: (None, fecha)}


# ── DNI ─────────────────────────────────────────────────────────────────────────────

def _dni_duplicado() -> ErrorNegocio:
    return ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_DNI_DUPLICADO, motivo="dni_duplicado")


def _dni_valido(db: Session, dni: Optional[str], excepto: Optional[int] = None) -> Optional[str]:
    """El DNI recortado, o None si viene vacío. 422 en `dni` si no son 8 dígitos y 409
    `dni_duplicado` si lo tiene otro alumno, de cualquier colegio y estado."""
    limpio = (dni or "").strip()
    if not limpio:
        return None
    if re.fullmatch(r"\d{8}", limpio, re.ASCII) is None:
        raise error_de_campo(CAMPO_DNI, MENSAJE_DNI_FORMATO)
    otro = db.exec(select(Alumno.id_alumno).where(Alumno.dni == limpio)).first()
    if otro is not None and otro != excepto:
        raise _dni_duplicado()
    return limpio


@contextmanager
def _dni_unico(db: Session) -> Iterator[None]:
    """Traduce a 409 la violación de `uq_alumno_dni`: dos peticiones simultáneas pueden
    pasar la validación a la vez y chocar en la BD, al hacer flush o commit."""
    try:
        yield
    except IntegrityError as error:
        db.rollback()
        mensaje = str(error.orig)
        if "uq_alumno_dni" in mensaje or "alumno.dni" in mensaje:
            raise _dni_duplicado() from error
        raise


# ── Escritura ───────────────────────────────────────────────────────────────────────

def crear_alumno(db: Session, data: AlumnoCrear, actor: Usuario) -> AlumnoItem:
    """Crea el alumno y su primer intervalo de pertenencia, en la misma transacción."""
    id_actividad = requerir_actividad_activa(db, actor)
    _exigir_alcance(alcance_de(db, actor), data.id_colegio, data.id_grado)
    pertenencia.validar_fecha_efectiva(data.pertenece_desde, CAMPO_PERTENECE_DESDE)
    validar_estado(db, data.id_colegio, data.id_grado, data.id_programa)
    id_seccion = seccion_para(db, data.id_colegio, data.id_grado, data.id_seccion)
    dni = _dni_valido(db, data.dni)

    ahora = ahora_utc()
    alumno = Alumno(
        nombres=data.nombres,
        apellidos=data.apellidos,
        dni=dni,
        id_colegio=data.id_colegio,
        id_grado=data.id_grado,
        id_seccion=id_seccion,
        id_programa_actual=data.id_programa,
        fecha_registro=hoy_lima(),
        activo=True,
        creado_por=actor.id_usuario,
        creado_en=ahora,
    )
    with _dni_unico(db):
        db.add(alumno)
        db.flush()
        movimiento = Movimiento(actor.id_usuario, ahora, id_actividad)
        pertenencia.abrir(db, alumno, data.pertenece_desde, CAMPO_PERTENECE_DESDE, movimiento, alta=True)
        registrar_auditoria(
            db, tabla=TABLA, id_registro=alumno.id_alumno, accion=CREAR, cambios=_fecha_efectiva(data.pertenece_desde),
            id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
        )
        db.commit()
    return item_de(db, alumno.id_alumno)


def _alumno_para_escribir(
    db: Session, actor: Usuario, id_alumno: int
) -> tuple[Alumno, Alcance, Optional[uuid.UUID], bool]:
    """El alumno (404), la actividad del Docente (409), su alcance sobre el alumno (403) y
    si tiene la pertenencia pendiente."""
    fila = _fila(db, id_alumno)
    alumno, pendiente = fila[0], fila[-1]
    id_actividad = requerir_actividad_activa(db, actor)
    alcance = alcance_de(db, actor)
    _exigir_alcance_sobre(alcance, alumno, pendiente)
    return alumno, alcance, id_actividad, pendiente


def _ficha_con_cambios(alumno: Alumno, data: AlumnoEditar) -> dict[str, Any]:
    final = {campo: getattr(alumno, atributo) for campo, atributo in CAMPOS_AUDITADOS.items()}
    final.update({campo: getattr(data, campo) for campo in data.model_fields_set if campo in CAMPOS_AUDITADOS})
    return final


def _valores_finales(db: Session, alumno: Alumno, data: AlumnoEditar, alcance: Alcance) -> dict[str, Any]:
    """Los campos auditados como quedarían. Si cambia el grupo, valida el alcance, el
    estado y la sección: cambiar de colegio o grado sin `id_seccion` toma la única
    sección activa del grado nuevo."""
    enviados = data.model_fields_set
    final = _ficha_con_cambios(alumno, data)
    _exigir_alcance(alcance, final["id_colegio"], final["id_grado"])
    cambia_aula = (final["id_colegio"], final["id_grado"]) != (alumno.id_colegio, alumno.id_grado)
    if cambia_aula and "id_seccion" not in enviados:
        final["id_seccion"] = None
    if any(final[campo] != getattr(alumno, CAMPOS_AUDITADOS[campo]) for campo in CAMPOS_DE_UBICACION):
        # Corregir solo nombres o apellidos no exige colegio activo (CU013, CU014).
        validar_estado(db, final["id_colegio"], final["id_grado"], final["id_programa"])
        final["id_seccion"] = seccion_para(db, final["id_colegio"], final["id_grado"], final["id_seccion"])
    return final


def _fecha_de_cambio(alumno: Alumno, data: AlumnoEditar) -> Optional[date]:
    """Fecha efectiva de un cambio de grupo. Obligatoria si el alumno está activo; un
    alumno inactivo no pertenece a ningún grupo: su ficha cambia y al activarlo se abre
    el intervalo con el grupo nuevo."""
    if not alumno.activo:
        return None
    if data.cambio_grupo_desde is None:
        raise error_de_campo(CAMPO_CAMBIO_GRUPO_DESDE, MENSAJE_FECHA_DE_CAMBIO)
    pertenencia.validar_fecha_efectiva(data.cambio_grupo_desde, CAMPO_CAMBIO_GRUPO_DESDE)
    return data.cambio_grupo_desde


# ── Edición de un alumno con pertenencia pendiente (CU014) ──────────────────────────

def _validar_fechas_de_pendiente(alumno: Alumno, data: AlumnoEditar) -> None:
    """No hay intervalo que cerrar, así que no lleva `cambio_grupo_desde`; un alumno
    inactivo confirma su `pertenece_desde` después de activarlo (DP-5)."""
    if data.cambio_grupo_desde is not None:
        raise error_de_campo(CAMPO_CAMBIO_GRUPO_DESDE, MENSAJE_USA_PERTENECE_DESDE)
    if data.pertenece_desde is None:
        return
    if not alumno.activo:
        raise error_de_campo(CAMPO_PERTENECE_DESDE, MENSAJE_ACTIVA_ANTES)
    pertenencia.validar_fecha_efectiva(data.pertenece_desde, CAMPO_PERTENECE_DESDE)


def _resolver_aula(db: Session, alumno: Alumno, final: dict[str, Any], enviados: set[str]) -> None:
    """Colegio y grado finales. Moverlo exige un colegio activo; si llega un grado, el
    colegio tiene que ofrecerlo. Si cambia de colegio sin indicar el grado y el nuevo no
    ofrece el que tenía, el grado queda vacío."""
    cambia_colegio = final["id_colegio"] != alumno.id_colegio
    if "id_grado" in enviados:
        if cambia_colegio or final["id_grado"] != alumno.id_grado:
            _grado_ofrecido(db, _colegio_activo(db, final["id_colegio"]), final["id_grado"])
    elif cambia_colegio:
        _colegio_activo(db, final["id_colegio"])
        if final["id_grado"] is not None and _grado_del_colegio(db, final["id_colegio"], final["id_grado"]) is None:
            final["id_grado"] = None


def _completar_grupo(db: Session, final: dict[str, Any]) -> None:
    """Resuelve la sección (`seccion_para`) o responde 422 con un error por cada campo que
    falte: el grado, el subprograma o la sección (ambigua o inexistente)."""
    faltan = [campo for campo in ("id_grado", "id_programa") if final[campo] is None]
    if final["id_grado"] is not None:
        try:
            final["id_seccion"] = seccion_para(db, final["id_colegio"], final["id_grado"], final["id_seccion"])
        except ErrorNegocio:
            faltan.append("id_seccion")
    elif final["id_seccion"] is None:
        faltan.append("id_seccion")
    if faltan:
        raise errores_de_campos(faltan, MENSAJE_GRUPO_INCOMPLETO)


def _fecha_de_confirmacion(alumno: Alumno, data: AlumnoEditar) -> Optional[date]:
    """Con el grupo completo, `pertenece_desde` abre el primer intervalo. Un alumno
    inactivo solo completa su ficha."""
    if not alumno.activo:
        return None
    if data.pertenece_desde is None:
        raise error_de_campo(CAMPO_PERTENECE_DESDE, MENSAJE_CONFIRMA_PERTENENCIA)
    return data.pertenece_desde


def _edicion_de_pendiente(
    db: Session, alumno: Alumno, data: AlumnoEditar, alcance: Alcance
) -> tuple[dict[str, Any], Optional[date]]:
    """Los campos auditados como quedarían y la fecha del primer intervalo, si se abre.

    Sin subprograma, sección ni fecha, guarda lo enviado y sigue pendiente. Si llega
    alguno de ellos, confirma su grupo: tiene que quedar completo. El Docente necesita el
    par (colegio, grado) final en su alcance si pone o cambia el grado o confirma el
    grupo (DP-4).
    """
    _validar_fechas_de_pendiente(alumno, data)
    enviados = data.model_fields_set
    final = _ficha_con_cambios(alumno, data)
    _resolver_aula(db, alumno, final, enviados)

    informa_grupo = bool(enviados & {"id_programa", "id_seccion"})
    confirma = informa_grupo or data.pertenece_desde is not None
    if final["id_grado"] is not None and (confirma or final["id_grado"] != alumno.id_grado):
        _exigir_alcance(alcance, final["id_colegio"], final["id_grado"])

    cambia_aula = (final["id_colegio"], final["id_grado"]) != (alumno.id_colegio, alumno.id_grado)
    if cambia_aula and "id_seccion" not in enviados:
        final["id_seccion"] = None
    if not confirma:
        if cambia_aula:
            # La sección era de su aula anterior y el subprograma va siempre con ella.
            final["id_programa"] = None
        return final, None

    if not informa_grupo and None in (alumno.id_grado, alumno.id_seccion, alumno.id_programa_actual):
        raise errores_de_campos([c for c in CAMPOS_DEL_GRUPO if final[c] is None], MENSAJE_GRUPO_INCOMPLETO)
    if informa_grupo or cambia_aula:
        _completar_grupo(db, final)
        validar_estado(db, final["id_colegio"], final["id_grado"], final["id_programa"])
    return final, _fecha_de_confirmacion(alumno, data)


def _aplicar_pertenencia(
    db: Session, alumno: Alumno, inicial: Optional[date], cambio: Optional[date], movimiento: Movimiento
) -> Optional[date]:
    """Abre el primer intervalo (`inicial`) o cambia de grupo (`cambio`) con la ficha ya
    actualizada. Devuelve la fecha efectiva aplicada, para la auditoría."""
    if inicial is not None:
        pertenencia.abrir(db, alumno, inicial, CAMPO_PERTENECE_DESDE, movimiento, alta=True)
        return inicial
    if cambio is not None:
        pertenencia.cambiar_grupo(db, alumno, cambio, CAMPO_CAMBIO_GRUPO_DESDE, movimiento)
    return cambio


def editar_alumno(db: Session, id_alumno: int, data: AlumnoEditar, actor: Usuario) -> AlumnoItem:
    alumno, alcance, id_actividad, pendiente = _alumno_para_escribir(db, actor, id_alumno)
    if alcance is not None and "id_colegio" in data.model_fields_set and data.id_colegio != alumno.id_colegio:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "Solo el Supervisor puede cambiar el colegio de un alumno.",
            motivo="cambio_colegio_no_permitido",
        )
    if CAMPO_DNI in data.model_fields_set:
        data = data.model_copy(update={CAMPO_DNI: _dni_valido(db, data.dni, excepto=id_alumno)})

    inicial: Optional[date] = None
    if pendiente:
        final, inicial = _edicion_de_pendiente(db, alumno, data, alcance)
    elif data.pertenece_desde is not None:
        raise error_de_campo(CAMPO_PERTENECE_DESDE, MENSAJE_YA_TIENE_PERTENENCIA)
    else:
        final = _valores_finales(db, alumno, data, alcance)
    cambios: dict[str, tuple[Any, Any]] = {
        campo: (getattr(alumno, atributo), final[campo])
        for campo, atributo in CAMPOS_AUDITADOS.items()
        if final[campo] != getattr(alumno, atributo)
    }
    if not cambios and inicial is None:
        return item_de(db, id_alumno)
    cambia_grupo = not pendiente and any(c in cambios for c in CAMPOS_DE_UBICACION)
    cambio = _fecha_de_cambio(alumno, data) if cambia_grupo else None

    ahora = ahora_utc()
    with _dni_unico(db):
        for campo, (_, nuevo) in cambios.items():
            setattr(alumno, CAMPOS_AUDITADOS[campo], nuevo)
        alumno.modificado_por = actor.id_usuario
        alumno.modificado_en = ahora
        db.add(alumno)
        fecha = _aplicar_pertenencia(db, alumno, inicial, cambio, Movimiento(actor.id_usuario, ahora, id_actividad))
        if fecha is not None:
            cambios.update(_fecha_efectiva(fecha))
        registrar_auditoria(
            db, tabla=TABLA, id_registro=alumno.id_alumno, accion=EDITAR, cambios=cambios,
            id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
        )
        db.commit()
    return item_de(db, id_alumno)


def _mover_pertenencia(
    db: Session, alumno: Alumno, activo: bool, desde: date, movimiento: Movimiento
) -> dict[str, tuple[Any, Any]]:
    """Activar abre un intervalo con el grupo de la ficha e inactivar cierra el vigente.
    Devuelve la fecha efectiva, para la auditoría."""
    if activo:
        pertenencia.abrir(db, alumno, desde, CAMPO_APLICAR_DESDE, movimiento)
    else:
        pertenencia.cerrar(db, alumno.id_alumno, desde, CAMPO_APLICAR_DESDE, movimiento)
    return _fecha_efectiva(desde)


def cambiar_estado(
    db: Session, id_alumno: int, activo: bool, data: CambioEstadoAlumno, actor: Usuario
) -> AlumnoItem:
    """Activa o inactiva (borrado lógico) desde `aplicar_desde`: inactivar cierra su
    intervalo vigente; activar abre uno con el grupo que conserva la ficha. Idempotente:
    sin cambios ni auditoría si ya estaba en ese estado. Para activar, el colegio debe
    estar activo. Con la pertenencia pendiente la fecha se ignora: solo cambia el estado,
    sin abrir ni cerrar intervalos, aunque la ficha tenga el grupo completo (DP-5)."""
    alumno, _, id_actividad, pendiente = _alumno_para_escribir(db, actor, id_alumno)
    if not pendiente and data.aplicar_desde is None:
        raise campo_obligatorio(CAMPO_APLICAR_DESDE)
    if alumno.activo == activo:
        return item_de(db, id_alumno)
    if activo:
        _colegio_activo(db, alumno.id_colegio)

    ahora = ahora_utc()
    movimiento = Movimiento(actor.id_usuario, ahora, id_actividad)
    fecha = {} if pendiente else _mover_pertenencia(db, alumno, activo, data.aplicar_desde, movimiento)
    alumno.activo = activo
    alumno.modificado_por = actor.id_usuario
    alumno.modificado_en = ahora
    db.add(alumno)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=alumno.id_alumno, accion=ACTIVAR if activo else INACTIVAR,
        cambios=fecha,
        id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
    )
    db.commit()
    return item_de(db, id_alumno)
