from datetime import date

from sqlmodel import Session

from app.models.organizacion import Colegio, Usuario
from app.schemas.colegio import ColegioCreate


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
