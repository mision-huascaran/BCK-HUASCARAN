from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.organizacion import (
    Colegio,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Usuario,
)
from app.schemas.asignacion import AsignacionCreate


class AsignacionNoExiste(Exception):
    """No existe una asignación con ese id."""


class DocenteNoExiste(Exception):
    """id_docente no corresponde a ningún docente."""


class ColegioNoExiste(Exception):
    """id_colegio no corresponde a ningún colegio."""


class GradoNoExiste(Exception):
    """id_grado no corresponde a ningún grado."""


class PeriodoNoExiste(Exception):
    """id_periodo_academico no corresponde a ningún periodo."""


class AsignacionDuplicada(Exception):
    """Ya existe esa misma combinación docente/colegio/grado/periodo."""


def periodos_vigentes(db: Session) -> list[int]:
    """Ids de los periodos academicos que contienen la fecha de hoy.

    Una asignacion es "vigente" si su periodo lo es. Si no hay ningun periodo cargado
    la lista sale vacia, y entonces ningun docente tiene alcance: el recorte por
    asignaciones no puede inventarse un periodo que la coordinacion no ha creado.
    """
    hoy = date.today()
    return list(
        db.exec(
            select(PeriodoAcademico.id_periodo_academico).where(
                PeriodoAcademico.fecha_inicio <= hoy,
                PeriodoAcademico.fecha_fin >= hoy,
            )
        ).all()
    )


def alcance_del_docente(db: Session, id_docente: int) -> list[tuple[int, int]]:
    """Pares (id_colegio, id_grado) que el docente tiene asignados y vigentes."""
    vigentes = periodos_vigentes(db)
    if not vigentes:
        return []
    filas = db.exec(
        select(DocenteColegioGrado.id_colegio, DocenteColegioGrado.id_grado).where(
            DocenteColegioGrado.id_docente == id_docente,
            DocenteColegioGrado.id_periodo_academico.in_(vigentes),
        )
    ).all()
    return [(c, g) for c, g in filas]


def crear_asignacion(
    db: Session, data: AsignacionCreate, usuario_actual: Usuario
) -> DocenteColegioGrado:
    if db.get(Docente, data.id_docente) is None:
        raise DocenteNoExiste()
    if db.get(Colegio, data.id_colegio) is None:
        raise ColegioNoExiste()
    if db.get(Grado, data.id_grado) is None:
        raise GradoNoExiste()
    if db.get(PeriodoAcademico, data.id_periodo_academico) is None:
        raise PeriodoNoExiste()

    repetida = db.exec(
        select(DocenteColegioGrado).where(
            DocenteColegioGrado.id_docente == data.id_docente,
            DocenteColegioGrado.id_colegio == data.id_colegio,
            DocenteColegioGrado.id_grado == data.id_grado,
            DocenteColegioGrado.id_periodo_academico == data.id_periodo_academico,
        )
    ).first()
    if repetida is not None:
        raise AsignacionDuplicada()

    asignacion = DocenteColegioGrado(
        id_docente=data.id_docente,
        id_colegio=data.id_colegio,
        id_grado=data.id_grado,
        id_periodo_academico=data.id_periodo_academico,
        creado_por=usuario_actual.id_usuario,
        creado_en=date.today(),
    )
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion


def eliminar_asignacion(db: Session, id_asignacion: int) -> None:
    """Borra la asignacion.

    Aqui si se borra de verdad, a diferencia de alumnos y cuentas: una asignacion es
    una relacion de trabajo del periodo, no un registro historico del que dependa
    ningun dato academico.
    """
    asignacion = db.get(DocenteColegioGrado, id_asignacion)
    if asignacion is None:
        raise AsignacionNoExiste()
    db.delete(asignacion)
    db.commit()


def listar_asignaciones(
    db: Session, id_docente: Optional[int] = None
) -> list[dict]:
    """Lista asignaciones con nombres resueltos. `id_docente` la acota a un docente."""
    vigentes = set(periodos_vigentes(db))

    consulta = (
        select(DocenteColegioGrado, Docente, Colegio, Grado, PeriodoAcademico)
        .join(Docente, DocenteColegioGrado.id_docente == Docente.id_docente)
        .join(Colegio, DocenteColegioGrado.id_colegio == Colegio.id_colegio)
        .join(Grado, DocenteColegioGrado.id_grado == Grado.id_grado)
        .join(
            PeriodoAcademico,
            DocenteColegioGrado.id_periodo_academico
            == PeriodoAcademico.id_periodo_academico,
        )
    )
    if id_docente is not None:
        consulta = consulta.where(DocenteColegioGrado.id_docente == id_docente)
    consulta = consulta.order_by(DocenteColegioGrado.id)

    return [
        {
            "id": asignacion.id,
            "id_docente": asignacion.id_docente,
            "docente": f"{docente.nombres} {docente.apellidos}",
            "id_colegio": asignacion.id_colegio,
            "colegio": colegio.nombre,
            "id_grado": asignacion.id_grado,
            "grado": grado.nombre,
            "id_periodo_academico": asignacion.id_periodo_academico,
            "periodo": f"Periodo {periodo.numero}",
            "vigente": asignacion.id_periodo_academico in vigentes,
        }
        for asignacion, docente, colegio, grado, periodo in db.exec(consulta).all()
    ]
