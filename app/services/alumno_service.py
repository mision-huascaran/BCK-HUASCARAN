from datetime import date

from sqlmodel import Session

from app.models.organizacion import Alumno, Colegio, Grado, Programa, Usuario
from app.schemas.alumno import AlumnoCreate


class ColegioNoExiste(Exception):
    """id_colegio no corresponde a ningún colegio existente."""


class GradoNoExiste(Exception):
    """id_grado no corresponde a ningún grado existente."""


class ProgramaNoExiste(Exception):
    """id_programa_actual no corresponde a ningún programa existente."""


def crear_alumno(db: Session, data: AlumnoCreate, usuario_actual: Usuario) -> Alumno:
    if db.get(Colegio, data.id_colegio) is None:
        raise ColegioNoExiste()
    if db.get(Grado, data.id_grado) is None:
        raise GradoNoExiste()
    if db.get(Programa, data.id_programa_actual) is None:
        raise ProgramaNoExiste()

    hoy = date.today()
    alumno = Alumno(
        nombres=data.nombres,
        apellidos=data.apellidos,
        id_colegio=data.id_colegio,
        id_grado=data.id_grado,
        id_programa_actual=data.id_programa_actual,
        activo=data.activo,
        fecha_registro=hoy,
        creado_por=usuario_actual.id_usuario,
        creado_en=hoy,
    )
    db.add(alumno)
    db.commit()
    db.refresh(alumno)
    return alumno
