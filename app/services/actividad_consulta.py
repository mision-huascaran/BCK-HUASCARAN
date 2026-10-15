"""Consulta de actividades: lista y detalle del Docente (CU017, CU018, CU019) e historial
de un docente para el Supervisor (CU021). Solo lectura.

Antes de consultar se cierran las actividades de sesiones vencidas del docente (el mismo
cierre perezoso del Inicio), así que una actividad con `fin` NULL está en curso.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from fastapi import status
from sqlmodel import Session, col, select

from app.core.errores import ErrorNegocio
from app.core.paginacion import Paginado, pagina, paginar
from app.core.tiempo import ahora_utc, fecha_lima, limites_lima
from app.models.trazabilidad import Actividad
from app.schemas.actividad import ActividadDetalle, ActividadResumen
from app.services import cambios
from app.services.actividad import EN_CURSO, FINALIZADA
from app.services.sincronizacion import estado_actividad_sql, pendientes_actividad_sql

MOTIVO_NO_ENCONTRADA = "actividad_no_encontrada"


def exigir_rango_valido(desde: Optional[date], hasta: Optional[date]) -> None:
    """CU018: con la fecha inicial posterior a la final no se busca nada."""
    if desde is not None and hasta is not None and desde > hasta:
        raise ErrorNegocio(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "El rango de fechas no es válido: la fecha inicial es posterior a la fecha final.",
            motivo="rango_fechas_invalido",
        )


def entre_fechas(columna, desde: Optional[date], hasta: Optional[date]) -> list:
    """Condiciones para un instante `columna` dentro de los días de Lima [desde, hasta]."""
    condiciones = []
    if desde is not None:
        condiciones.append(columna >= limites_lima(desde, desde)[0])
    if hasta is not None:
        condiciones.append(columna < limites_lima(hasta, hasta)[1])
    return condiciones


def _duracion(inicio: datetime, fin: Optional[datetime], ahora: datetime) -> int:
    return max(0, int(((fin or ahora) - inicio).total_seconds()))


@dataclass(frozen=True)
class FiltrosActividad:
    desde: Optional[date] = None
    hasta: Optional[date] = None
    estado: Optional[str] = None
    sincronizacion: Optional[str] = None
    tipo_cierre: Optional[str] = None


def listar_actividades(
    db: Session, id_docente: int, filtros: FiltrosActividad, page: int
) -> Paginado[ActividadResumen]:
    """Actividades del docente, de la más reciente a la más antigua. Tres consultas: el
    total, la página y los cambios de las actividades de la página."""
    exigir_rango_valido(filtros.desde, filtros.hasta)
    sincronizacion = estado_actividad_sql(pendientes_actividad_sql())
    consulta = select(Actividad, sincronizacion).where(
        Actividad.id_docente == id_docente,
        *entre_fechas(Actividad.inicio, filtros.desde, filtros.hasta),
    )
    if filtros.estado == EN_CURSO:
        consulta = consulta.where(col(Actividad.fin).is_(None))
    elif filtros.estado == FINALIZADA:
        consulta = consulta.where(col(Actividad.fin).is_not(None))
    if filtros.sincronizacion is not None:
        consulta = consulta.where(sincronizacion == filtros.sincronizacion)
    if filtros.tipo_cierre is not None:
        consulta = consulta.where(Actividad.tipo_cierre == filtros.tipo_cierre)
    consulta = consulta.order_by(col(Actividad.inicio).desc(), col(Actividad.id_actividad))

    filas, total = paginar(db, consulta, page)
    conteos = cambios.cambios_por_actividad(db, (a.id_actividad for a, _ in filas))
    ahora = ahora_utc()
    return pagina(
        [
            ActividadResumen(
                id=a.id_actividad,
                id_docente=a.id_docente,
                fecha=fecha_lima(a.inicio),
                inicio=a.inicio,
                fin=a.fin,
                duracion_segundos=_duracion(a.inicio, a.fin, ahora),
                estado=EN_CURSO if a.fin is None else FINALIZADA,
                tipo_cierre=a.tipo_cierre,
                sincronizacion=estado,
                cambios=sum(conteos[a.id_actividad].values()),
                productividad=cambios.productividad(conteos[a.id_actividad]),
                productividad_texto=cambios.productividad_texto(conteos[a.id_actividad]),
            )
            for a, estado in filas
        ],
        total,
        page,
    )


def detalle_actividad(db: Session, id_docente: Optional[int], id_actividad: uuid.UUID) -> ActividadDetalle:
    """Detalle de una actividad propia (CU019). Ajena o inexistente: el mismo 404, para
    no revelar que existe."""
    pendientes = pendientes_actividad_sql()
    fila = db.exec(
        select(Actividad, estado_actividad_sql(pendientes), pendientes).where(
            Actividad.id_actividad == id_actividad, Actividad.id_docente == id_docente
        )
    ).first()
    if fila is None:
        raise ErrorNegocio(
            status.HTTP_404_NOT_FOUND,
            "El detalle solicitado no se encuentra disponible.",
            motivo=MOTIVO_NO_ENCONTRADA,
        )
    actividad, sincronizacion, registros_pendientes = fila
    registros = cambios.registros_de(db, id_actividad)
    return ActividadDetalle(
        id=actividad.id_actividad,
        fecha=fecha_lima(actividad.inicio),
        inicio=actividad.inicio,
        fin=actividad.fin,
        duracion_segundos=_duracion(actividad.inicio, actividad.fin, ahora_utc()),
        estado=EN_CURSO if actividad.fin is None else FINALIZADA,
        tipo_cierre=actividad.tipo_cierre,
        sincronizacion=sincronizacion,
        sincronizado_en=actividad.sincronizado_en,
        registros_pendientes=registros_pendientes,
        cambios=len({(tabla, id_registro) for tabla, id_registro, _ in registros}),
        cambios_por_modulo=cambios.resumen_por_modulo(registros),
        asignaciones=cambios.asignaciones_involucradas(db, registros),
    )
