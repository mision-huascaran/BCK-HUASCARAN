"""
Datos de prueba para HU14 (autenticacion). Ficticios, no son informacion
real de Mision Huascaran. Correr manualmente con:

    python -m app.seed_data
"""
from typing import Optional

from sqlmodel import Session, col, select

from app.cli.catalogos import cargar_catalogos, roles_por_nombre
from app.core.correo import normalizar_correo
from app.core.database import engine
from app.core.security import hash_password
from app.core.tiempo import ahora_utc
from app.models.organizacion import (
    Colegio,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Usuario,
)
from app.services.calendario import periodo_vigente
from app.services.colegio_service import completar_oferta

APELLIDOS_PRUEBA = "de Prueba"

DEPARTAMENTO = "Áncash"

PROFESOR_CORREO = normalizar_correo("profesor.prueba@sicedu.test")
PROFESOR_PASSWORD = "ProfesorTest123"
PROFESOR_DNI = "00000002"

JEFA_CORREO = normalizar_correo("jefa.prueba@sicedu.test")
JEFA_PASSWORD = "JefaTest123"
JEFA_DNI = "00000001"

DIRECTIVO_CORREO = normalizar_correo("directivo.prueba@sicedu.test")
DIRECTIVO_PASSWORD = "DirectivoTest123"
DIRECTIVO_DNI = "00000003"


def periodo_para_asignaciones(session: Session) -> Optional[PeriodoAcademico]:
    """Periodo del calendario real del que cuelgan las asignaciones de prueba: el vigente
    hoy o, si no hay ninguno vigente, el último. None si no hay calendario cargado."""
    vigente = periodo_vigente(session)
    if vigente is not None:
        return vigente
    return session.exec(
        select(PeriodoAcademico).order_by(col(PeriodoAcademico.fecha_inicio).desc())
    ).first()


def completar_dni(session: Session, usuario: Usuario, dni: str) -> None:
    """Las cuentas sembradas antes de la v3 no tienen DNI: se completa sin pisar uno existente."""
    if usuario.dni is None:
        usuario.dni = dni
        session.add(usuario)
        session.commit()


def completar_distrito(session: Session, colegio: Colegio, distrito: str) -> None:
    """Los colegios sembrados antes de la v3 no tienen distrito: se completa si falta."""
    if colegio.distrito is None:
        colegio.distrito = distrito
        session.add(colegio)
        session.commit()


def seed() -> None:
    with Session(engine) as session:
        # Roles, ciclos, grados, programas y niveles son los catálogos reales: los carga
        # el mismo código que `python -m app.cli cargar-catalogos` (idempotente).
        cargar_catalogos(session)
        roles = roles_por_nombre(session)
        ahora = ahora_utc()

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
                apellidos=APELLIDOS_PRUEBA,
                dni=JEFA_DNI,
                activo=True,
                creado_por=1,
                creado_en=ahora,
            )
            session.add(usuario_jefa)
            session.commit()
            session.refresh(usuario_jefa)

            usuario_jefa.creado_por = usuario_jefa.id_usuario
            session.add(usuario_jefa)
            session.commit()
            session.refresh(usuario_jefa)
        completar_dni(session, usuario_jefa, JEFA_DNI)

        colegio = session.exec(
            select(Colegio).where(Colegio.nombre == "Colegio de Prueba")
        ).first()
        if colegio is None:
            colegio = Colegio(
                nombre="Colegio de Prueba",
                departamento=DEPARTAMENTO,
                provincia="Provincia de Prueba",
                distrito="Distrito de Prueba",
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
            )
            session.add(colegio)
            session.commit()
            session.refresh(colegio)
        completar_distrito(session, colegio, "Distrito de Prueba")
        # Los 6 grados y los 2 programas, para poder asignarle docentes y alumnos.
        completar_oferta(
            session,
            colegio,
            list(session.exec(select(Grado).order_by(Grado.id_grado)).all()),
            list(session.exec(select(Programa).order_by(Programa.id_programa)).all()),
            {"creado_por": usuario_jefa.id_usuario, "creado_en": ahora},
        )
        session.commit()

        # (nombre, provincia, distrito): distritos capitales de su provincia.
        colegios_ficticios = [
            ("Colegio Yungay", "Yungay", "Yungay"),
            ("Colegio Carhuaz", "Carhuaz", "Carhuaz"),
        ]
        for nombre_colegio, provincia_colegio, distrito_colegio in colegios_ficticios:
            colegio_ficticio = session.exec(
                select(Colegio).where(Colegio.nombre == nombre_colegio)
            ).first()
            if colegio_ficticio is None:
                colegio_ficticio = Colegio(
                    nombre=nombre_colegio,
                    departamento=DEPARTAMENTO,
                    provincia=provincia_colegio,
                    distrito=distrito_colegio,
                    creado_por=usuario_jefa.id_usuario,
                    creado_en=ahora,
                )
                session.add(colegio_ficticio)
                session.commit()
            completar_distrito(session, colegio_ficticio, distrito_colegio)

        docente = session.exec(
            select(Docente).where(
                Docente.nombres == "Docente", Docente.apellidos == APELLIDOS_PRUEBA
            )
        ).first()
        if docente is None:
            docente = Docente(
                nombres="Docente",
                apellidos=APELLIDOS_PRUEBA,
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
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
                dni=PROFESOR_DNI,
                nombres="Docente",
                apellidos=APELLIDOS_PRUEBA,
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
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
                apellidos=APELLIDOS_PRUEBA,
                dni=DIRECTIVO_DNI,
                activo=True,
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
            )
            session.add(usuario_directivo)

        session.commit()
        completar_dni(session, usuario_profesor, PROFESOR_DNI)
        completar_dni(session, usuario_directivo, DIRECTIVO_DNI)

        # Asignación de ejemplo para el docente de prueba, para que el recorte por
        # alcance se pueda ver funcionando sin tener que crearla a mano. El seed ya no crea
        # año ni periodo: cuelga del calendario real (`python -m app.cli cargar-calendario`).
        # Sin un periodo vigente un Docente no tiene ningún alumno a la vista.
        periodo = periodo_para_asignaciones(session)
        asignacion = None
        if periodo is not None:
            asignacion = session.exec(
                select(DocenteColegioGrado).where(
                    DocenteColegioGrado.id_docente == docente.id_docente,
                    DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
                )
            ).first()
        if periodo is not None and asignacion is None:
            primer_grado = session.exec(select(Grado).order_by(Grado.id_grado)).first()
            session.add(
                DocenteColegioGrado(
                    id_docente=docente.id_docente,
                    id_colegio=colegio.id_colegio,
                    id_grado=primer_grado.id_grado,
                    id_periodo_academico=periodo.id_periodo_academico,
                    creado_por=usuario_jefa.id_usuario,
                    creado_en=ahora,
                )
            )
            session.commit()

        print("Seed completado.")
        print(f"Docente     -> correo: {PROFESOR_CORREO}  password: {PROFESOR_PASSWORD}")
        print(f"Supervisor  -> correo: {JEFA_CORREO}  password: {JEFA_PASSWORD}")
        print(f"Directivo   -> correo: {DIRECTIVO_CORREO}  password: {DIRECTIVO_PASSWORD}")
        if periodo is None:
            print("Sin calendario cargado: no se creó la asignación de prueba.")
        else:
            print(f"Asignación de prueba en el periodo académico id={periodo.id_periodo_academico}")


if __name__ == "__main__":
    seed()