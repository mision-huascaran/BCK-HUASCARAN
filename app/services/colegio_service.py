from sqlmodel import Session

from app.core.tiempo import ahora_utc
from app.models.organizacion import Colegio, Usuario
from app.schemas.colegio import ColegioCreate, ColegioUpdate


def crear_colegio(db: Session, data: ColegioCreate, usuario_actual: Usuario) -> Colegio:
    colegio = Colegio(
        nombre=data.nombre,
        nivel_educativo=data.nivel_educativo,
        departamento=data.departamento,
        provincia=data.provincia,
        distrito=data.distrito,
        seccion=data.seccion,
        activo=data.activo,
        creado_por=usuario_actual.id_usuario,
        creado_en=ahora_utc(),
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
    colegio.modificado_en = ahora_utc()
    db.add(colegio)
    db.commit()
    db.refresh(colegio)
    return colegio
