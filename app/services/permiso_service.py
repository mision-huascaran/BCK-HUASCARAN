"""Permisos excepcionales de corrección (CU028).

- Solo el Supervisor los otorga y revoca, con conexión y sin actividad.
- Habilitan a un Docente a escribir en un módulo para un colegio, grado y PA cuyo plazo
  ordinario de regularización ya terminó (pasó el fin del PR que sigue a ese PA, en el
  calendario del colegio), durante [inicio, fin).
- No se editan, no se reactivan ni se borran. El estado se calcula al consultar.
- Sin superposición de permisos no revocados del mismo alcance (docente, año, colegio,
  grado, PA y módulo). Cada otorgamiento y revocación queda en `auditoria`.
"""
from datetime import datetime
from typing import Any, Optional

from fastapi import status
from sqlalchemy import and_
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio, error_de_campo
from app.core.paginacion import Paginado, pagina, paginar
from app.core.tiempo import ahora_utc, fecha_lima, hoy_lima
from app.models.organizacion import (
    TIPO_PA,
    TIPO_PR,
    AnioEscolar,
    Colegio,
    ColegioGrado,
    Docente,
    Grado,
    PeriodoAcademico,
    PermisoCorreccion,
    Usuario,
)
from app.schemas.comun import ItemCatalogo
from app.schemas.permiso import OpcionesPermiso, PermisoCrear, PermisoDetalle, PermisoItem, PermisoVigente
from app.services.auditoria import CREAR, INACTIVAR, registrar_auditoria
from app.services.calendario import CalendarioResuelto, cargar_calendarios, nombre_periodo
from app.services.habilitacion import ORIGEN_PERMISO, habilitaciones_vigentes
from app.services.usuario_service import DOCENTE, nombre_rol

TABLA = "permiso_correccion"
PROGRAMADO = "programado"
VIGENTE = "vigente"
VENCIDO = "vencido"
REVOCADO = "revocado"

MENSAJE_DOCENTE_INACTIVO = "La cuenta del Docente está inactiva. No se puede otorgar el permiso."
MENSAJE_SIN_MOTIVO = "Ingresa el motivo de la autorización."
MENSAJE_INICIO_PASADO = "Selecciona una fecha y hora futura o utiliza la opción ‘Desde ahora’."
MENSAJE_FIN_ANTES_DEL_INICIO = "El fin de vigencia debe ser posterior al inicio."
MENSAJE_PLAZO_ORDINARIO = (
    "Este periodo todavía admite regularización ordinaria y no requiere un permiso excepcional."
)
MENSAJE_SUPERPOSICION = (
    "Ya existe un permiso para este mismo alcance con una vigencia que coincide total o "
    "parcialmente con la propuesta."
)
MENSAJE_SIN_MOTIVO_REVOCACION = "Ingresa el motivo de la revocación."
MENSAJE_YA_REVOCADO = "El permiso ya fue revocado. Se actualizó su información."
MENSAJE_YA_VENCIDO = "El permiso ya venció y no requiere revocación."
CAMPO_PERIODO = "id_periodo_academico"


# ── Estado ──────────────────────────────────────────────────────────────────────────

def estado_de(permiso: PermisoCorreccion, ahora: datetime) -> str:
    if permiso.revocado_en is not None:
        return REVOCADO
    if ahora < permiso.inicio:
        return PROGRAMADO
    return VIGENTE if ahora < permiso.fin else VENCIDO


def _filtro_estado(estado: str, ahora: datetime):
    """El mismo cálculo que `estado_de`, como condición SQL."""
    no_revocado = col(PermisoCorreccion.revocado_en).is_(None)
    condiciones = {
        REVOCADO: col(PermisoCorreccion.revocado_en).is_not(None),
        PROGRAMADO: and_(no_revocado, PermisoCorreccion.inicio > ahora),
        VIGENTE: and_(no_revocado, PermisoCorreccion.inicio <= ahora, PermisoCorreccion.fin > ahora),
        VENCIDO: and_(no_revocado, PermisoCorreccion.fin <= ahora),
    }
    return condiciones[estado]


# ── Docente de la ruta ──────────────────────────────────────────────────────────────

def docente_de_la_ruta(db: Session, id_usuario: int) -> tuple[Usuario, Docente]:
    """La cuenta y su ficha de docente. 404 si no existe o no tiene rol Docente."""
    usuario = db.get(Usuario, id_usuario)
    docente = db.get(Docente, usuario.id_docente) if usuario and usuario.id_docente else None
    if usuario is None or docente is None or nombre_rol(db, usuario.id_rol) != DOCENTE:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el Docente.", motivo="docente_no_encontrado")
    return usuario, docente


# ── Respuestas ──────────────────────────────────────────────────────────────────────

def _consulta():
    return (
        select(PermisoCorreccion, AnioEscolar, Colegio, Grado, PeriodoAcademico)
        .join(AnioEscolar, AnioEscolar.id_anio_escolar == PermisoCorreccion.id_anio_escolar)
        .join(Colegio, Colegio.id_colegio == PermisoCorreccion.id_colegio)
        .join(Grado, Grado.id_grado == PermisoCorreccion.id_grado)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == PermisoCorreccion.id_periodo_academico)
    )


def _item(fila, ahora: datetime) -> dict[str, Any]:
    permiso, anio, colegio, grado, periodo = fila
    return {
        "id": permiso.id_permiso,
        "anio_escolar": ItemCatalogo(id=anio.id_anio_escolar, nombre=anio.nombre),
        "colegio": ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre),
        "grado": ItemCatalogo(id=grado.id_grado, nombre=grado.nombre),
        "periodo": ItemCatalogo(id=periodo.id_periodo_academico, nombre=nombre_periodo(TIPO_PA, periodo.numero)),
        "modulo": permiso.modulo,
        "inicio": permiso.inicio,
        "fin": permiso.fin,
        "estado": estado_de(permiso, ahora),
        "otorgado_en": permiso.otorgado_en,
    }


def _persona(db: Session, id_usuario: Optional[int]) -> Optional[ItemCatalogo]:
    usuario = db.get(Usuario, id_usuario) if id_usuario is not None else None
    if usuario is None:
        return None
    return ItemCatalogo(id=usuario.id_usuario, nombre=f"{usuario.nombres} {usuario.apellidos}")


def _detalle(db: Session, id_docente: int, id_permiso: int) -> PermisoDetalle:
    fila = db.exec(
        _consulta().where(PermisoCorreccion.id_permiso == id_permiso, PermisoCorreccion.id_docente == id_docente)
    ).first()
    if fila is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el permiso.", motivo="permiso_no_encontrado")
    permiso = fila[0]
    return PermisoDetalle(
        **_item(fila, ahora_utc()),
        motivo=permiso.motivo,
        otorgado_por=_persona(db, permiso.otorgado_por),
        revocado_por=_persona(db, permiso.revocado_por),
        revocado_en=permiso.revocado_en,
        motivo_revocacion=permiso.motivo_revocacion,
    )


# ── Lectura ─────────────────────────────────────────────────────────────────────────

def listar(db: Session, id_usuario: int, filtros: dict[str, Any], page: int) -> Paginado[PermisoItem]:
    """Del más reciente otorgado al más antiguo. `filtros`: id_anio_escolar, id_colegio,
    id_periodo_academico, modulo y estado (los None no filtran)."""
    _, docente = docente_de_la_ruta(db, id_usuario)
    ahora = ahora_utc()
    consulta = _consulta().where(PermisoCorreccion.id_docente == docente.id_docente)
    for campo in ("id_anio_escolar", "id_colegio", "id_periodo_academico", "modulo"):
        if filtros.get(campo) is not None:
            consulta = consulta.where(getattr(PermisoCorreccion, campo) == filtros[campo])
    if filtros.get("estado") is not None:
        consulta = consulta.where(_filtro_estado(filtros["estado"], ahora))
    consulta = consulta.order_by(col(PermisoCorreccion.otorgado_en).desc(), col(PermisoCorreccion.id_permiso).desc())
    filas, total = paginar(db, consulta, page)
    return pagina([PermisoItem(**_item(f, ahora)) for f in filas], total, page)


def obtener(db: Session, id_usuario: int, id_permiso: int) -> PermisoDetalle:
    _, docente = docente_de_la_ruta(db, id_usuario)
    return _detalle(db, docente.id_docente, id_permiso)


def vigentes_del_docente(db: Session, usuario: Usuario) -> list[PermisoVigente]:
    """Permisos vigentes ahora del Docente, en colegios activos, por fin de vigencia
    (vacío si su cuenta está inactiva)."""
    ids = [h.id_permiso for h in habilitaciones_vigentes(db, usuario) if h.origen == ORIGEN_PERMISO]
    if not ids:
        return []
    filas = db.exec(_consulta().where(col(PermisoCorreccion.id_permiso).in_(ids)).order_by(PermisoCorreccion.fin)).all()
    return [
        PermisoVigente(
            id_permiso=permiso.id_permiso,
            colegio=ItemCatalogo(id=colegio.id_colegio, nombre=colegio.nombre),
            grado=ItemCatalogo(id=grado.id_grado, nombre=grado.nombre),
            anio_escolar=ItemCatalogo(id=anio.id_anio_escolar, nombre=anio.nombre),
            periodo=nombre_periodo(TIPO_PA, periodo.numero),
            modulo=permiso.modulo,
            fin=permiso.fin,
        )
        for permiso, anio, colegio, grado, periodo in filas
    ]


def _calendario_de(db: Session, id_colegio: int, id_anio_escolar: int) -> Optional[CalendarioResuelto]:
    return cargar_calendarios(db, id_anio_escolar, ids_colegio=[id_colegio]).aplicable(id_colegio, id_anio_escolar)


def _plazo_ordinario_terminado(calendario: Optional[CalendarioResuelto], numero: int, hoy) -> bool:
    """Si ya pasó el fin del PR que sigue al PA `numero` en ese calendario (sin calendario,
    no se puede saber: no)."""
    if calendario is None:
        return False
    receso = next((p for p in calendario.periodos if p.tipo == TIPO_PR and p.numero == numero), None)
    return receso is not None and receso.fecha_fin < hoy


def opciones(db: Session, id_colegio: int, id_anio_escolar: int) -> OpcionesPermiso:
    """PA del año cuyo plazo ordinario ya terminó en el calendario del colegio, y los
    grados que ofrece el colegio."""
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise error_de_campo("id_colegio", "No existe el colegio.")
    if db.get(AnioEscolar, id_anio_escolar) is None:
        raise error_de_campo("id_anio_escolar", "No existe el año escolar.")
    calendario = _calendario_de(db, id_colegio, id_anio_escolar)
    hoy = hoy_lima()
    periodos = db.exec(
        select(PeriodoAcademico)
        .where(PeriodoAcademico.id_anio_escolar == id_anio_escolar)
        .order_by(PeriodoAcademico.numero)
    ).all()
    grados = db.exec(
        select(Grado).join(ColegioGrado, ColegioGrado.id_grado == Grado.id_grado)
        .where(ColegioGrado.id_colegio == id_colegio).order_by(Grado.id_grado)
    ).all()
    return OpcionesPermiso(
        periodos=[
            ItemCatalogo(id=p.id_periodo_academico, nombre=nombre_periodo(TIPO_PA, p.numero))
            for p in periodos if _plazo_ordinario_terminado(calendario, p.numero, hoy)
        ],
        grados=[ItemCatalogo(id=g.id_grado, nombre=g.nombre) for g in grados],
    )


# ── Otorgar ─────────────────────────────────────────────────────────────────────────

def _vigencia(data: PermisoCrear, ahora: datetime) -> tuple[datetime, datetime]:
    """(inicio, fin) validados. Con `desde_ahora`, el inicio es la hora del servidor."""
    inicio = ahora if data.desde_ahora else data.inicio
    if inicio is None or inicio < ahora:
        raise error_de_campo("inicio", MENSAJE_INICIO_PASADO)
    if data.fin <= inicio:
        raise error_de_campo("fin", MENSAJE_FIN_ANTES_DEL_INICIO)
    return inicio, data.fin


def _validar_alcance(db: Session, data: PermisoCrear, ahora: datetime) -> None:
    """Que el año, el colegio, el grado y el PA existan y correspondan entre sí, y que el
    PA ya no admita regularización ordinaria. 422 en el campo que falla."""
    if db.get(AnioEscolar, data.id_anio_escolar) is None:
        raise error_de_campo("id_anio_escolar", "No existe el año escolar.")
    if db.get(Colegio, data.id_colegio) is None:
        raise error_de_campo("id_colegio", "No existe el colegio.")
    if db.get(ColegioGrado, (data.id_colegio, data.id_grado)) is None:
        raise error_de_campo("id_grado", "El colegio no ofrece ese grado.")
    periodo = db.get(PeriodoAcademico, data.id_periodo_academico)
    if periodo is None or periodo.id_anio_escolar != data.id_anio_escolar:
        raise error_de_campo(CAMPO_PERIODO, "El periodo no pertenece al año escolar.")
    calendario = _calendario_de(db, data.id_colegio, data.id_anio_escolar)
    if not _plazo_ordinario_terminado(calendario, periodo.numero, fecha_lima(ahora)):
        raise error_de_campo(CAMPO_PERIODO, MENSAJE_PLAZO_ORDINARIO)


def _verificar_sin_superposicion(
    db: Session, id_docente: int, data: PermisoCrear, inicio: datetime, fin: datetime
) -> None:
    """409 si otro permiso no revocado del mismo alcance se cruza con [inicio, fin)."""
    existente = db.exec(
        select(PermisoCorreccion).where(
            PermisoCorreccion.id_docente == id_docente,
            PermisoCorreccion.id_anio_escolar == data.id_anio_escolar,
            PermisoCorreccion.id_colegio == data.id_colegio,
            PermisoCorreccion.id_grado == data.id_grado,
            PermisoCorreccion.id_periodo_academico == data.id_periodo_academico,
            PermisoCorreccion.modulo == data.modulo,
            col(PermisoCorreccion.revocado_en).is_(None),
            PermisoCorreccion.inicio < fin,
            PermisoCorreccion.fin > inicio,
        ).order_by(PermisoCorreccion.inicio)
    ).first()
    if existente is not None:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT,
            MENSAJE_SUPERPOSICION,
            motivo="permiso_superpuesto",
            extra={"permiso_existente": {
                "id": existente.id_permiso, "inicio": existente.inicio.isoformat(), "fin": existente.fin.isoformat(),
            }},
        )


def otorgar(db: Session, id_usuario: int, data: PermisoCrear, actor: Usuario) -> PermisoDetalle:
    """Valida todo antes de escribir y guarda el permiso con su auditoría, en una
    transacción. La ficha del docente se bloquea para que dos otorgamientos simultáneos
    del mismo docente no se superpongan."""
    usuario, docente = docente_de_la_ruta(db, id_usuario)
    db.get(Docente, docente.id_docente, with_for_update=True)
    if not (usuario.activo and docente.activo):
        raise ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_DOCENTE_INACTIVO, motivo="docente_inactivo")
    if not data.motivo.strip():
        raise error_de_campo("motivo", MENSAJE_SIN_MOTIVO)
    ahora = ahora_utc()
    inicio, fin = _vigencia(data, ahora)
    _validar_alcance(db, data, ahora)
    _verificar_sin_superposicion(db, docente.id_docente, data, inicio, fin)

    permiso = PermisoCorreccion(
        id_docente=docente.id_docente,
        id_anio_escolar=data.id_anio_escolar,
        id_periodo_academico=data.id_periodo_academico,
        id_colegio=data.id_colegio,
        id_grado=data.id_grado,
        modulo=data.modulo,
        inicio=inicio,
        fin=fin,
        motivo=data.motivo.strip(),
        otorgado_por=actor.id_usuario,
        otorgado_en=ahora,
    )
    db.add(permiso)
    db.flush()
    registrar_auditoria(
        db, tabla=TABLA, id_registro=permiso.id_permiso, accion=CREAR,
        id_usuario=actor.id_usuario, fecha=ahora, id_permiso=permiso.id_permiso,
    )
    db.commit()
    return _detalle(db, docente.id_docente, permiso.id_permiso)


# ── Revocar ─────────────────────────────────────────────────────────────────────────

def revocar(db: Session, id_usuario: int, id_permiso: int, motivo: str, actor: Usuario) -> PermisoDetalle:
    """Revoca un permiso programado o vigente. Ya revocado o vencido: 409 (no se registra
    una segunda revocación)."""
    _, docente = docente_de_la_ruta(db, id_usuario)
    permiso = db.exec(
        select(PermisoCorreccion)
        .where(PermisoCorreccion.id_permiso == id_permiso, PermisoCorreccion.id_docente == docente.id_docente)
        .with_for_update()
    ).first()
    if permiso is None:
        raise ErrorNegocio(status.HTTP_404_NOT_FOUND, "No existe el permiso.", motivo="permiso_no_encontrado")
    if not motivo.strip():
        raise error_de_campo("motivo", MENSAJE_SIN_MOTIVO_REVOCACION)
    ahora = ahora_utc()
    estado = estado_de(permiso, ahora)
    if estado == REVOCADO:
        raise ErrorNegocio(
            status.HTTP_409_CONFLICT, MENSAJE_YA_REVOCADO, motivo="permiso_revocado",
            extra={"permiso": _detalle(db, docente.id_docente, id_permiso).model_dump(mode="json")},
        )
    if estado == VENCIDO:
        raise ErrorNegocio(status.HTTP_409_CONFLICT, MENSAJE_YA_VENCIDO, motivo="permiso_vencido")

    permiso.revocado_por, permiso.revocado_en = actor.id_usuario, ahora
    permiso.motivo_revocacion = motivo.strip()
    db.add(permiso)
    registrar_auditoria(
        db, tabla=TABLA, id_registro=permiso.id_permiso, accion=INACTIVAR,
        cambios={"motivo_revocacion": (None, permiso.motivo_revocacion)},
        id_usuario=actor.id_usuario, fecha=ahora, id_permiso=permiso.id_permiso,
    )
    db.commit()
    return _detalle(db, docente.id_docente, id_permiso)

