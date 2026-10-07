"""
Datos de prueba para HU14 (autenticacion). Ficticios, no son informacion
real de Mision Huascaran. Correr manualmente con:

    python -m app.seed_data
"""
from datetime import date

from sqlmodel import Session, select

from app.core.database import engine
from app.core.security import hash_password
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    AnioEscolar,
    CicloEbr,
    Colegio,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Rol,
    Usuario,
)

ROLES = ["Docente", "Supervisor", "Directivo"]

CICLOS = ["III", "IV", "V"]

GRADOS_POR_CICLO = {
    "1.º": "III",
    "2.º": "III",
    "3.º": "IV",
    "4.º": "IV",
    "5.º": "V",
    "6.º": "V",
}

PROGRAMAS = ["Alfabetización", "Comprensión Lectora"]

APELLIDOS_PRUEBA = "de Prueba"

DEPARTAMENTO = "Áncash"

PROFESOR_CORREO = "profesor.prueba@sicedu.test"
PROFESOR_PASSWORD = "ProfesorTest123"
PROFESOR_DNI = "00000002"

JEFA_CORREO = "jefa.prueba@sicedu.test"
JEFA_PASSWORD = "JefaTest123"
JEFA_DNI = "00000001"

DIRECTIVO_CORREO = "directivo.prueba@sicedu.test"
DIRECTIVO_PASSWORD = "DirectivoTest123"
DIRECTIVO_DNI = "00000003"


def get_or_create_rol(session: Session, nombre: str) -> Rol:
    rol = session.exec(select(Rol).where(Rol.nombre == nombre)).first()
    if rol is None:
        rol = Rol(nombre=nombre)
        session.add(rol)
        session.commit()
        session.refresh(rol)
    return rol


def get_or_create_ciclo(session: Session, nombre: str) -> CicloEbr:
    ciclo = session.exec(select(CicloEbr).where(CicloEbr.nombre == nombre)).first()
    if ciclo is None:
        ciclo = CicloEbr(nombre=nombre)
        session.add(ciclo)
        session.commit()
        session.refresh(ciclo)
    return ciclo


def get_or_create_grado(session: Session, nombre: str, id_ciclo: int) -> Grado:
    grado = session.exec(select(Grado).where(Grado.nombre == nombre)).first()
    if grado is None:
        grado = Grado(nombre=nombre, id_ciclo=id_ciclo)
        session.add(grado)
        session.commit()
        session.refresh(grado)
    return grado


def get_or_create_programa(session: Session, nombre: str) -> Programa:
    programa = session.exec(select(Programa).where(Programa.nombre == nombre)).first()
    if programa is None:
        programa = Programa(nombre=nombre)
        session.add(programa)
        session.commit()
        session.refresh(programa)
    return programa


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
        roles = {nombre: get_or_create_rol(session, nombre) for nombre in ROLES}
        hoy = hoy_lima()
        ahora = ahora_utc()

        ciclos = {nombre: get_or_create_ciclo(session, nombre) for nombre in CICLOS}
        for nombre_grado, nombre_ciclo in GRADOS_POR_CICLO.items():
            get_or_create_grado(session, nombre_grado, ciclos[nombre_ciclo].id_ciclo)
        for nombre_programa in PROGRAMAS:
            get_or_create_programa(session, nombre_programa)

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

        # Año escolar y periodo académico vigentes. Sin un periodo que contenga la fecha
        # de hoy no se puede crear ninguna asignación docente↔colegio/grado, y sin
        # asignaciones un Docente no tiene ningún alumno a la vista: el recorte por
        # alcance lo dejaría con la lista vacía. Se siembra el año en curso completo.
        anio = session.exec(
            select(AnioEscolar).where(AnioEscolar.nombre == str(hoy.year))
        ).first()
        if anio is None:
            anio = AnioEscolar(
                nombre=str(hoy.year),
                fecha_inicio=date(hoy.year, 1, 1),
                fecha_fin=date(hoy.year, 12, 31),
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
            )
            session.add(anio)
            session.commit()
            session.refresh(anio)

        periodo = session.exec(
            select(PeriodoAcademico).where(
                PeriodoAcademico.id_anio_escolar == anio.id_anio_escolar,
                PeriodoAcademico.numero == 1,
            )
        ).first()
        if periodo is None:
            periodo = PeriodoAcademico(
                id_anio_escolar=anio.id_anio_escolar,
                numero=1,
                fecha_inicio=date(hoy.year, 1, 1),
                fecha_fin=date(hoy.year, 12, 31),
                creado_por=usuario_jefa.id_usuario,
                creado_en=ahora,
            )
            session.add(periodo)
            session.commit()
            session.refresh(periodo)

        # Asignación de ejemplo para el docente de prueba, para que el recorte por
        # alcance se pueda ver funcionando sin tener que crearla a mano.
        asignacion = session.exec(
            select(DocenteColegioGrado).where(
                DocenteColegioGrado.id_docente == docente.id_docente,
                DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
            )
        ).first()
        if asignacion is None:
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
        print(f"Periodo académico vigente: id={periodo.id_periodo_academico}")


if __name__ == "__main__":
    seed()