from datetime import date

from sqlmodel import Session

from app.models.organizacion import Colegio, Usuario
from app.schemas.colegio import ColegioCreate, ColegioUpdate


def crear_colegio(db: Session, data: ColegioCreate, usuario_actual: Usuario) -> Colegio:
    colegio = Colegio(
        nombre=data.nombre,
        zona=data.zona,
        creado_por=usuario_actual.id_usuario,
        creado_en=date.today(),
    )
    db.add(colegio)
    db.commit()
    db.refresh(colegio)
    return colegio


class ColegioNoExiste(Exception):
    """id_colegio no corresponde a ningún colegio existente."""


def actualizar_colegio(
    db: Session, id_colegio: int, data: ColegioUpdate, usuario_actual: Usuario
) -> Colegio:
    colegio = db.get(Colegio, id_colegio)
    if colegio is None:
        raise ColegioNoExiste()

    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(colegio, campo, valor)

    colegio.modificado_por = usuario_actual.id_usuario
    colegio.modificado_en = date.today()
    db.add(colegio)
    db.commit()
    db.refresh(colegio)
    return colegio
