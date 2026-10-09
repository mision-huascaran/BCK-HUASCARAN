"""Alumnos (CU014).

- Supervisor: alcance global; crea y edita alumnos de cualquier colegio sin actividad.
- Docente: solo alumnos de su alcance (pares colegio + grado vigentes); toda escritura
  requiere una actividad activa (CU010), que queda vinculada a la auditoría.
- Ciclo EBR: se calcula (app/core/ciclo.py). Sección: la del colegio.
- Borrado lógico: activar / inactivar; ningún alumno se elimina.
- Colegio inactivo (CU013): no afecta a sus alumnos. El Supervisor puede corregir sus
  nombres y apellidos; cambiar su ubicación (colegio, grado, subprograma) o activarlo
  exige un colegio activo. El Docente no los ve: quedan fuera de su alcance.
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.core.ciclo import calcular_ciclo, ciclo_sql, programa_permitido_en_grado
from app.core.database import insert_con_conflicto
from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    Alumno,
    AlumnoProgramaHistorial,
    CicloEbr,
    Colegio,
    ColegioGrado,
    ColegioPrograma,
    Grado,
    Programa,
    Usuario,
)
from app.schemas.alumno import AlumnoCrear, AlumnoDetalle, AlumnoEditar, AlumnoItem
from app.schemas.comun import ItemCatalogo
from app.services.actividad import requerir_actividad_activa
from app.services.alcance import alcance_docente
from app.services.auditoria import ACTIVAR, CREAR, EDITAR, INACTIVAR, registrar_auditoria
from app.services.calendario import periodo_de_referencia
from app.services.usuario_service import DOCENTE, nombre_rol

TABLA = "alumno"
_historial = AlumnoProgramaHistorial.__table__

# Quien busca escribe "perez", no "Pérez": se comparan ambos lados sin tildes. Se usa
# translate() en vez de la extensión unaccent porque no requiere instalar nada en la BD.
CON_TILDES = "áéíóúüñÁÉÍÓÚÜÑ"
SIN_TILDES = "aeiouunAEIOUUN"

# Campo auditado -> atributo del modelo (el subprograma se guarda en `id_programa_actual`).
CAMPOS_AUDITADOS = {
    "nombres": "nombres",
    "apellidos": "apellidos",
    "id_colegio": "id_colegio",
    "id_grado": "id_grado",
    "id_programa": "id_programa_actual",
}
# Los que ubican al alumno: si alguno cambia, se vuelve a validar su estado.
CAMPOS_DE_UBICACION = ("id_colegio", "id_grado", "id_programa")

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

def alcance_de(db: Session, usuario: Usuario) -> Alcance:
    """Pares (colegio, grado) del Docente; None (sin recorte) para el Supervisor.

    Un conjunto vacío significa "no tiene nada a cargo", que no es lo mismo que None.
    """
    if nombre_rol(db, usuario.id_rol) != DOCENTE:
        return None
    if usuario.id_docente is None:
        return set()
    return alcance_docente(db, usuario.id_docente)


def _exigir_alcance(alcance: Alcance, id_colegio: int, id_grado: int) -> None:
    if alcance is not None and (id_colegio, id_grado) not in alcance:
        raise _fuera_de_alcance()


# ── Respuestas ──────────────────────────────────────────────────────────────────────

def _consulta_items():
    """Alumno con su colegio, grado, programa y el ciclo de su grado, en una sola consulta."""
    return (
        select(Alumno, Colegio, Grado, Programa, CicloEbr.nombre)
        .join(Colegio, Colegio.id_colegio == Alumno.id_colegio)
        .join(Grado, Grado.id_grado == Alumno.id_grado)
        .join(CicloEbr, CicloEbr.id_ciclo == Grado.id_ciclo)
        .join(Programa, Programa.id_programa == Alumno.id_programa_actual)
    )


def _item(fila) -> AlumnoItem:
    alumno, colegio, grado, programa, ciclo_grado = fila
    return AlumnoItem(
        id=alumno.id_alumno,
        nombres=alumno.nombres,
        apellidos=alumno.apellidos,
        colegio=ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre),
        grado=ItemCatalogo(id=grado.id_grado, nombre=grado.nombre),
        programa=ItemCatalogo(id=programa.id_programa, nombre=programa.nombre),
        ciclo=calcular_ciclo(programa.nombre, ciclo_grado),
        seccion=colegio.seccion,
        activo=alumno.activo,
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
    alumno = _fila(db, id_alumno)[0]
    _exigir_alcance(alcance_de(db, usuario), alumno.id_colegio, alumno.id_grado)
    return alumno


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
) -> Paginado[AlumnoItem]:
    """Listado paginado, ordenado por apellidos y nombres.

    `q`: cada palabra debe aparecer en "nombres apellidos", sin distinguir mayúsculas ni
    tildes y en cualquier orden ("juan perez" encuentra a "Juan Carlos Pérez Quispe").
    Los caracteres % y _ se buscan literalmente.
    """
    alcance = alcance_de(db, usuario)
    if alcance is not None and not alcance:
        return pagina([], 0, page)

    consulta = _consulta_items().where(Alumno.activo == activo)
    if alcance is not None:
        consulta = consulta.where(or_(*(and_(Alumno.id_colegio == c, Alumno.id_grado == g) for c, g in alcance)))
    if id_colegio is not None:
        consulta = consulta.where(Alumno.id_colegio == id_colegio)
    if id_grado is not None:
        consulta = consulta.where(Alumno.id_grado == id_grado)
    if id_programa is not None:
        consulta = consulta.where(Alumno.id_programa_actual == id_programa)
    if ciclo is not None:
        consulta = consulta.where(ciclo_sql(Programa.nombre, CicloEbr.nombre) == ciclo)
    if q and q.strip():
        nombre_completo = func.translate(
            func.lower(Alumno.nombres + " " + Alumno.apellidos), CON_TILDES, SIN_TILDES
        )
        for palabra in q.strip().lower().translate(str.maketrans(CON_TILDES, SIN_TILDES)).split():
            consulta = consulta.where(nombre_completo.contains(palabra, autoescape=True))
    consulta = consulta.order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)

    filas, total = paginar(db, consulta, page)
    return pagina([_item(f) for f in filas], total, page)


def obtener_alumno(db: Session, usuario: Usuario, id_alumno: int) -> AlumnoDetalle:
    fila = _fila(db, id_alumno)
    alumno = fila[0]
    _exigir_alcance(alcance_de(db, usuario), alumno.id_colegio, alumno.id_grado)
    return AlumnoDetalle(**_item(fila).model_dump(), fecha_registro=alumno.fecha_registro)


# ── Validación del estado del alumno ────────────────────────────────────────────────

def _colegio_activo(db: Session, id_colegio: int) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise _no_procesable("No existe el colegio indicado.", "colegio_inexistente")
    if not colegio.activo:
        raise _no_procesable(f"El colegio {colegio.nombre} está inactivo.", "colegio_inactivo")
    return colegio


def validar_estado(db: Session, id_colegio: int, id_grado: int, id_programa: int) -> None:
    """Colegio activo, grado y programa ofrecidos por el colegio, y regla de 1.º."""
    colegio = _colegio_activo(db, id_colegio)
    grado = db.exec(
        select(Grado)
        .join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
        .where(ColegioGrado.id_colegio == id_colegio, Grado.id_grado == id_grado)
    ).first()
    if grado is None:
        raise _no_procesable(f"El colegio {colegio.nombre} no ofrece ese grado.", "grado_no_ofrecido")
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


def registrar_programa(db: Session, alumno: Alumno, id_actor: int, ahora: datetime) -> None:
    """Deja el subprograma actual del alumno en `alumno_programa_historial`, en el periodo
    de referencia (vigente, próximo o último cargado).

    Una sola sentencia INSERT ... ON CONFLICT sobre el UNIQUE (alumno, periodo): si ya hay
    fila para ese periodo, la actualiza (solo si el subprograma cambió) en vez de
    duplicarla, y dos peticiones simultáneas nunca chocan en un 500.
    """
    periodo = periodo_de_referencia(db)
    if periodo is None:
        return
    sentencia = insert_con_conflicto(db, _historial).values(
        id_alumno=alumno.id_alumno,
        id_programa=alumno.id_programa_actual,
        id_periodo_academico=periodo.id_periodo_academico,
        creado_por=id_actor,
        creado_en=ahora,
    )
    db.execute(
        sentencia.on_conflict_do_update(
            index_elements=[_historial.c.id_alumno, _historial.c.id_periodo_academico],
            set_={
                "id_programa": sentencia.excluded.id_programa,
                "modificado_por": id_actor,
                "modificado_en": ahora,
            },
            where=_historial.c.id_programa != sentencia.excluded.id_programa,
        )
    )


# ── Escritura ───────────────────────────────────────────────────────────────────────

def crear_alumno(db: Session, data: AlumnoCrear, actor: Usuario) -> AlumnoItem:
    id_actividad = requerir_actividad_activa(db, actor)
    _exigir_alcance(alcance_de(db, actor), data.id_colegio, data.id_grado)
    validar_estado(db, data.id_colegio, data.id_grado, data.id_programa)

    ahora = ahora_utc()
    alumno = Alumno(
        nombres=data.nombres,
        apellidos=data.apellidos,
        id_colegio=data.id_colegio,
        id_grado=data.id_grado,
        id_programa_actual=data.id_programa,
        fecha_registro=hoy_lima(),
        activo=True,
        creado_por=actor.id_usuario,
        creado_en=ahora,
    )
    db.add(alumno)
    db.flush()
    registrar_programa(db, alumno, actor.id_usuario, ahora)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=alumno.id_alumno, accion=CREAR,
        id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
    )
    db.commit()
    return item_de(db, alumno.id_alumno)


def _alumno_para_escribir(
    db: Session, actor: Usuario, id_alumno: int
) -> tuple[Alumno, Alcance, Optional[uuid.UUID]]:
    """El alumno (404), la actividad del Docente (409) y su alcance sobre el alumno (403)."""
    alumno = _fila(db, id_alumno)[0]
    id_actividad = requerir_actividad_activa(db, actor)
    alcance = alcance_de(db, actor)
    _exigir_alcance(alcance, alumno.id_colegio, alumno.id_grado)
    return alumno, alcance, id_actividad


def editar_alumno(db: Session, id_alumno: int, data: AlumnoEditar, actor: Usuario) -> AlumnoItem:
    alumno, alcance, id_actividad = _alumno_para_escribir(db, actor, id_alumno)
    enviados = data.model_fields_set
    if alcance is not None and "id_colegio" in enviados and data.id_colegio != alumno.id_colegio:
        raise ErrorNegocio(
            status.HTTP_403_FORBIDDEN,
            "Solo el Supervisor puede cambiar el colegio de un alumno.",
            motivo="cambio_colegio_no_permitido",
        )

    final = {campo: getattr(alumno, atributo) for campo, atributo in CAMPOS_AUDITADOS.items()}
    final.update({campo: getattr(data, campo) for campo in enviados})
    _exigir_alcance(alcance, final["id_colegio"], final["id_grado"])
    cambios: dict[str, tuple[Any, Any]] = {
        campo: (getattr(alumno, atributo), final[campo])
        for campo, atributo in CAMPOS_AUDITADOS.items()
        if final[campo] != getattr(alumno, atributo)
    }
    if not cambios:
        return item_de(db, id_alumno)
    # Corregir solo nombres o apellidos no exige colegio activo (CU013, CU014).
    if any(campo in cambios for campo in CAMPOS_DE_UBICACION):
        validar_estado(db, final["id_colegio"], final["id_grado"], final["id_programa"])

    ahora = ahora_utc()
    for campo, (_, nuevo) in cambios.items():
        setattr(alumno, CAMPOS_AUDITADOS[campo], nuevo)

    alumno.modificado_por = actor.id_usuario
    alumno.modificado_en = ahora
    db.add(alumno)
    if "id_programa" in cambios:
        registrar_programa(db, alumno, actor.id_usuario, ahora)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=alumno.id_alumno, accion=EDITAR, cambios=cambios,
        id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
    )
    db.commit()
    return item_de(db, id_alumno)


def cambiar_estado(db: Session, id_alumno: int, activo: bool, actor: Usuario) -> AlumnoItem:
    """Activa o inactiva (borrado lógico). Idempotente: sin cambios ni auditoría si ya
    estaba en ese estado. Para activar, el colegio debe estar activo."""
    alumno, _, id_actividad = _alumno_para_escribir(db, actor, id_alumno)
    if alumno.activo == activo:
        return item_de(db, id_alumno)
    if activo:
        _colegio_activo(db, alumno.id_colegio)

    ahora = ahora_utc()
    alumno.activo = activo
    alumno.modificado_por = actor.id_usuario
    alumno.modificado_en = ahora
    db.add(alumno)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=alumno.id_alumno, accion=ACTIVAR if activo else INACTIVAR,
        id_usuario=actor.id_usuario, fecha=ahora, id_actividad=id_actividad,
    )
    db.commit()
    return item_de(db, id_alumno)
