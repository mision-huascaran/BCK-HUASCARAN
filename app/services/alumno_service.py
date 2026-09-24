from datetime import date
from typing import Optional

from sqlalchemy import and_, or_
from sqlmodel import Session, func, select

from app.models.organizacion import Alumno, Colegio, Grado, Programa, Usuario
from app.schemas.alumno import AlumnoCreate, AlumnoUpdate


# Quien busca escribe "perez", no "Pérez". Se comparan ambos lados sin tildes para que
# el acento no decida si un alumno aparece o no. Se usa translate() en vez de la
# extension unaccent porque no requiere instalar nada en la base de datos.
CON_TILDES = "áéíóúüñÁÉÍÓÚÜÑ"
SIN_TILDES = "aeiouunAEIOUUN"


def _sin_tildes(texto: str) -> str:
    return texto.translate(str.maketrans(CON_TILDES, SIN_TILDES))


def _sin_tildes_sql(columna):
    return func.translate(columna, CON_TILDES, SIN_TILDES)


class FueraDeTuAlcance(Exception):
    """El alumno pertenece a un colegio/grado que el docente no tiene asignado."""


class ColegioNoExiste(Exception):
    """id_colegio no corresponde a ningún colegio existente."""


class GradoNoExiste(Exception):
    """id_grado no corresponde a ningún grado existente."""


class ProgramaNoExiste(Exception):
    """id_programa_actual no corresponde a ningún programa existente."""


def crear_alumno(
    db: Session,
    data: AlumnoCreate,
    usuario_actual: Usuario,
    alcance: Optional[list[tuple[int, int]]] = None,
) -> Alumno:
    if alcance is not None and (data.id_colegio, data.id_grado) not in alcance:
        raise FueraDeTuAlcance()
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


class AlumnoNoExiste(Exception):
    """id_alumno no corresponde a ningún alumno existente."""


def listar_alumnos(
    db: Session,
    colegio: Optional[int] = None,
    grado: Optional[int] = None,
    programa: Optional[int] = None,
    q: Optional[str] = None,
    activo: Optional[bool] = None,
    alcance: Optional[list[tuple[int, int]]] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[Alumno]]:
    """Devuelve (total_que_cumple_el_filtro, pagina_de_alumnos).

    `q` busca en nombres y apellidos ignorando mayusculas y tildes. Cada palabra debe
    aparecer en el nombre completo, en cualquier orden: "juan perez" encuentra a
    "Juan Carlos Pérez Quispe", y "perez" encuentra a "Pérez".
    """
    filtros = []

    # `alcance` son los pares (colegio, grado) que el docente tiene asignados. Se aplica
    # como filtro de la consulta, antes que cualquier otro: lo que queda fuera no existe
    # para quien pregunta, ni siquiera en el `total`. Una lista vacia (docente sin
    # asignaciones vigentes) devuelve cero, que es lo correcto: no tiene alumnos a cargo.
    if alcance is not None:
        if not alcance:
            return 0, []
        filtros.append(
            or_(*[
                and_(Alumno.id_colegio == c, Alumno.id_grado == g) for c, g in alcance
            ])
        )

    if colegio is not None:
        filtros.append(Alumno.id_colegio == colegio)
    if grado is not None:
        filtros.append(Alumno.id_grado == grado)
    if programa is not None:
        filtros.append(Alumno.id_programa_actual == programa)
    if activo is not None:
        filtros.append(Alumno.activo.is_(activo))
    if q:
        # Cada palabra debe aparecer en el nombre completo, en cualquier orden y posicion:
        # asi "juan perez" encuentra a "Juan Carlos Pérez Quispe", que con una sola
        # busqueda de la frase entera se quedaria fuera por el "Carlos" de en medio.
        nombre_completo = _sin_tildes_sql(
            func.lower(Alumno.nombres + " " + Alumno.apellidos)
        )
        for palabra in _sin_tildes(q.strip().lower()).split():
            filtros.append(nombre_completo.like(f"%{palabra}%"))

    total = db.exec(
        select(func.count()).select_from(Alumno).where(*filtros)
    ).one()

    alumnos = db.exec(
        select(Alumno)
        .where(*filtros)
        .order_by(Alumno.apellidos, Alumno.nombres, Alumno.id_alumno)
        .limit(limit)
        .offset(offset)
    ).all()

    return total, alumnos


def actualizar_alumno(
    db: Session,
    id_alumno: int,
    data: AlumnoUpdate,
    usuario_actual: Usuario,
    alcance: Optional[list[tuple[int, int]]] = None,
) -> Alumno:
    alumno = db.get(Alumno, id_alumno)
    if alumno is None:
        raise AlumnoNoExiste()

    cambios = data.model_dump(exclude_unset=True)

    # Se comprueba el alumno tal como esta y tal como quedaria: un docente no puede
    # editar un alumno que no es suyo, ni sacar uno de los suyos hacia un colegio o
    # grado que no tiene asignado.
    if alcance is not None:
        destino = (
            cambios.get("id_colegio", alumno.id_colegio),
            cambios.get("id_grado", alumno.id_grado),
        )
        if (alumno.id_colegio, alumno.id_grado) not in alcance or destino not in alcance:
            raise FueraDeTuAlcance()

    if "id_colegio" in cambios and db.get(Colegio, cambios["id_colegio"]) is None:
        raise ColegioNoExiste()
    if "id_grado" in cambios and db.get(Grado, cambios["id_grado"]) is None:
        raise GradoNoExiste()
    if "id_programa_actual" in cambios and db.get(Programa, cambios["id_programa_actual"]) is None:
        raise ProgramaNoExiste()

    for campo, valor in cambios.items():
        setattr(alumno, campo, valor)

    alumno.modificado_por = usuario_actual.id_usuario
    alumno.modificado_en = date.today()
    db.add(alumno)
    db.commit()
    db.refresh(alumno)
    return alumno
