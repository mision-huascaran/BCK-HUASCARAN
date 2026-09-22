"""
Datos de prueba para HU14 (autenticacion). Ficticios, no son informacion
real de Mision Huascaran. Correr manualmente con:

    python -m app.seed_data
"""
from datetime import date

from sqlmodel import Session, select

from app.core.database import engine
from app.core.security import hash_password
from app.models.organizacion import Colegio, Docente, Rol, Usuario

ROLES = ["Docente", "Supervisor", "Directivo"]

PROFESOR_CORREO = "profesor.prueba@sicedu.test"
PROFESOR_PASSWORD = "ProfesorTest123"

JEFA_CORREO = "jefa.prueba@sicedu.test"
JEFA_PASSWORD = "JefaTest123"

DIRECTIVO_CORREO = "directivo.prueba@sicedu.test"
DIRECTIVO_PASSWORD = "DirectivoTest123"


def get_or_create_rol(session: Session, nombre: str) -> Rol:
    rol = session.exec(select(Rol).where(Rol.nombre == nombre)).first()
    if rol is None:
        rol = Rol(nombre=nombre)
        session.add(rol)
        session.commit()
        session.refresh(rol)
    return rol


def seed() -> None:
    with Session(engine) as session:
        roles = {nombre: get_or_create_rol(session, nombre) for nombre in ROLES}
        hoy = date.today()

        # Primer usuario del sistema (Supervisor) — debe insertarse antes que cualquier
        # otra fila auditable, porque creado_por de todas las demás apunta a él.
        # Su propia fila se auto-referencia: se inserta con un placeholder (el id que
        # se espera obtener, 1, en una BD vacía) y se corrige con un UPDATE una vez
        # que el id_usuario real es conocido.
        usuario_jefa = session.exec(
            select(Usuario).where(Usuario.correo == JEFA_CORREO)
        ).first()
        if usuario_jefa is None:
            usuario_jefa = Usuario(
                id_rol=roles["Supervisor"].id_rol,
                correo=JEFA_CORREO,
                password_hash=hash_password(JEFA_PASSWORD),
                id_docente=None,
                nombres="Jefa",
                apellidos="de Prueba",
                activo=True,
                creado_por=1,
                creado_en=hoy,
            )
            session.add(usuario_jefa)
            session.commit()
            session.refresh(usuario_jefa)

            usuario_jefa.creado_por = usuario_jefa.id_usuario
            session.add(usuario_jefa)
            session.commit()
            session.refresh(usuario_jefa)

        colegio = session.exec(
            select(Colegio).where(Colegio.nombre == "Colegio de Prueba")
        ).first()
        if colegio is None:
            colegio = Colegio(
                nombre="Colegio de Prueba",
                zona="Zona de Prueba",
                creado_por=usuario_jefa.id_usuario,
                creado_en=hoy,
            )
            session.add(colegio)
            session.commit()
            session.refresh(colegio)

        docente = session.exec(
            select(Docente).where(
                Docente.nombres == "Docente", Docente.apellidos == "de Prueba"
            )
        ).first()
        if docente is None:
            docente = Docente(
                nombres="Docente",
                apellidos="de Prueba",
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=hoy,
            )
            session.add(docente)
            session.commit()
            session.refresh(docente)

        usuario_profesor = session.exec(
            select(Usuario).where(Usuario.correo == PROFESOR_CORREO)
        ).first()
        if usuario_profesor is None:
            usuario_profesor = Usuario(
                id_rol=roles["Docente"].id_rol,
                correo=PROFESOR_CORREO,
                password_hash=hash_password(PROFESOR_PASSWORD),
                id_docente=docente.id_docente,
                nombres="Docente",
                apellidos="de Prueba",
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=hoy,
            )
            session.add(usuario_profesor)

        usuario_directivo = session.exec(
            select(Usuario).where(Usuario.correo == DIRECTIVO_CORREO)
        ).first()
        if usuario_directivo is None:
            usuario_directivo = Usuario(
                id_rol=roles["Directivo"].id_rol,
                correo=DIRECTIVO_CORREO,
                password_hash=hash_password(DIRECTIVO_PASSWORD),
                id_docente=None,
                nombres="Directivo",
                apellidos="de Prueba",
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=hoy,
            )
            session.add(usuario_directivo)

        session.commit()

        print("Seed completado.")
        print(f"Docente     -> correo: {PROFESOR_CORREO}  password: {PROFESOR_PASSWORD}")
        print(f"Supervisor  -> correo: {JEFA_CORREO}  password: {JEFA_PASSWORD}")
        print(f"Directivo   -> correo: {DIRECTIVO_CORREO}  password: {DIRECTIVO_PASSWORD}")


if __name__ == "__main__":
    seed()