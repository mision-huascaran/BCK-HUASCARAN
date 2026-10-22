"""sembrar-e2e: cuentas y datos fijos para las pruebas E2E (Playwright) del frontend.

Solo actúa con SEED_E2E=true y ENTORNO en ENTORNOS_PERMITIDOS (qa o dev). Cualquier otro
entorno (uat, producción, piloto) o ENTORNO vacío se rechaza con ERROR aunque
SEED_E2E=true. Es el último paso de `inicializar`, así que corre en cada arranque: busca
por correo (y el colegio por nombre) y crea o corrige, nunca duplica.

Siembra el colegio "I.E. E2E Playwright" (cada grado con su día de clase, para que las
pruebas cubran las clases vencidas de CU011) y cuatro cuentas (Docente, Supervisor,
Directivo y una Directivo siempre inactiva). El Docente queda en 1.º, 2.º y 3.º de ese
colegio en todos los periodos del año escolar vigente; 4.º, 5.º y 6.º quedan sin docente
desde el periodo vigente en adelante (las asignaciones de periodos pasados se conservan
como historial, CU016).

Las variables se leen con os.environ y no con Settings: en el contenedor llegan por
`env_file`. En local, fuera de Docker, se exportan en la terminal (Settings rechaza
claves desconocidas dentro del .env). Las contraseñas vienen de E2E_*_PASS, nunca del
código ni del log; si falta una o no cumple la política de CU006, esa cuenta no se toca.

No toca al Supervisor original ni ninguna fila fuera del colegio y las cuentas E2E.
"""
import os
from dataclasses import dataclass
from typing import Mapping, Optional

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.cli.catalogos import roles_por_nombre
from app.cli.comun import Resumen, id_supervisor_original
from app.core.correo import normalizar_correo
from app.core.politica_contrasena import errores_politica_contrasena
from app.core.security import hash_password, verify_password
from app.core.texto import nombre_comparable
from app.core.tiempo import ahora_utc, hoy_lima
from app.models.organizacion import (
    AnioEscolar,
    Colegio,
    ColegioGrado,
    Docente,
    DocenteColegioGrado,
    Grado,
    PeriodoAcademico,
    Programa,
    Usuario,
)
from app.models.seguridad import ControlAccesoCorreo
from app.services.calendario import (
    anio_de_referencia,
    anio_vigente,
    cargar_calendarios,
    periodos_academicos_del_anio,
)
from app.services.colegio_service import completar_oferta
from app.services.sesiones import cerrar_sesiones_usuario
from app.services.usuario_service import CIERRE_POR_DESACTIVACION

ENTORNOS_PERMITIDOS = frozenset({"qa", "dev"})

COLEGIO = {
    "nombre": "I.E. E2E Playwright",
    "departamento": "Áncash",
    "provincia": "Huaraz",
    "distrito": "Huaraz",
    "nivel_educativo": "Primaria",
}
GRADOS = ("1.º", "2.º", "3.º", "4.º", "5.º", "6.º")
PROGRAMAS = ("Alfabetización", "Comprensión Lectora")
GRADOS_DEL_DOCENTE = ("1.º", "2.º", "3.º")
# Día de clase de cada grado (1 = lunes … 5 = viernes): uno distinto por grado del Docente.
DIAS_DE_CLASE = {"1.º": 1, "2.º": 2, "3.º": 3, "4.º": 4, "5.º": 5, "6.º": 1}
GRADOS_LIBRES = ("4.º", "5.º", "6.º")

DOCENTE = "Docente"


@dataclass(frozen=True)
class CuentaE2E:
    variable: str  # variable de entorno con la contraseña
    rol: str
    nombres: str
    apellidos: str
    dni: str
    correo: str
    activo: bool


CUENTAS = (
    CuentaE2E("E2E_DOCENTE_PASS", DOCENTE, "E2E Docente", "Playwright", "90000001",
              "soloparaunuso123+e2e.docente@gmail.com", True),
    CuentaE2E("E2E_SUPERVISOR_PASS", "Supervisor", "E2E Supervisora", "Playwright", "90000002",
              "soloparaunuso123+e2e.supervisor@gmail.com", True),
    CuentaE2E("E2E_DIRECTIVO_PASS", "Directivo", "E2E Directivo", "Playwright", "90000003",
              "soloparaunuso123+e2e.directivo@gmail.com", True),
    CuentaE2E("E2E_INACTIVO_PASS", "Directivo", "E2E Inactivo", "Playwright", "90000004",
              "soloparaunuso123+e2e.inactivo@gmail.com", False),
)


def motivo_de_rechazo(entorno: Mapping[str, str]) -> Optional[str]:
    """Por qué no se siembra en este entorno, o None si se puede. Sin la BD."""
    nombre = entorno.get("ENTORNO", "").strip().lower()
    if nombre not in ENTORNOS_PERMITIDOS:
        permitidos = ", ".join(sorted(ENTORNOS_PERMITIDOS))
        return f"ENTORNO={nombre or '(vacío)'} no está permitido (solo {permitidos})"
    return None


# ── Colegio ─────────────────────────────────────────────────────────────────────────

def _asegurar_dias_de_clase(db: Session, colegio: Colegio, grados: list[Grado], id_autor: int, ahora) -> bool:
    """Deja cada grado del colegio E2E con su día de DIAS_DE_CLASE. True si cambió alguno."""
    db.flush()
    cambiado = False
    for grado in grados:
        fila = db.get(ColegioGrado, (colegio.id_colegio, grado.id_grado))
        if fila.dia_clase != DIAS_DE_CLASE[grado.nombre]:
            fila.dia_clase = DIAS_DE_CLASE[grado.nombre]
            fila.modificado_por, fila.modificado_en = id_autor, ahora
            db.add(fila)
            cambiado = True
    return cambiado


def _asegurar_colegio(
    db: Session, grados: list[Grado], programas: list[Programa], id_autor: int, resumen: Resumen
) -> Colegio:
    ahora = ahora_utc()
    auditoria = {"creado_por": id_autor, "creado_en": ahora}
    buscado = nombre_comparable(COLEGIO["nombre"])
    colegio = next(
        (c for c in db.exec(select(Colegio)).all() if nombre_comparable(c.nombre) == buscado), None
    )
    if colegio is None:
        colegio = Colegio(**COLEGIO, activo=True, **auditoria)
        db.add(colegio)
        db.flush()
        completar_oferta(db, colegio, grados, programas, auditoria)
        _asegurar_dias_de_clase(db, colegio, grados, id_autor, ahora)
        db.commit()
        db.refresh(colegio)
        resumen.creados["colegio E2E"] += 1
        return colegio

    deseado = {**COLEGIO, "activo": True}
    cambiado = any(getattr(colegio, campo) != valor for campo, valor in deseado.items())
    for campo, valor in deseado.items():
        setattr(colegio, campo, valor)
    nuevos_grados, nuevos_programas = completar_oferta(db, colegio, grados, programas, auditoria)
    dias_cambiados = _asegurar_dias_de_clase(db, colegio, grados, id_autor, ahora)
    if cambiado or nuevos_grados or nuevos_programas or dias_cambiados:
        colegio.modificado_por, colegio.modificado_en = id_autor, ahora
        resumen.actualizados["colegio E2E"] += 1
    db.add(colegio)
    db.commit()
    db.refresh(colegio)
    return colegio


# ── Cuentas ─────────────────────────────────────────────────────────────────────────

def _errores_de_cuenta(db: Session, cuenta: CuentaE2E, password: str, existente: Optional[Usuario]) -> list[str]:
    errores = []
    reglas = errores_politica_contrasena(password)
    if reglas:
        errores.append(f"{cuenta.variable} no cumple la política de CU006: {', '.join(reglas)}")
    if existente is not None and existente.es_supervisor_original:
        errores.append("el correo es del Supervisor original")
    dueno_dni = db.exec(select(Usuario).where(Usuario.dni == cuenta.dni)).first()
    if dueno_dni is not None and (existente is None or dueno_dni.id_usuario != existente.id_usuario):
        errores.append(f"el DNI {cuenta.dni} ya está en uso por otra cuenta")
    return errores


def _asegurar_ficha(db: Session, usuario: Usuario, cuenta: CuentaE2E, id_autor: int, ahora) -> bool:
    """Ficha `docente` activa con los mismos nombres. True si cambió algo."""
    ficha = db.get(Docente, usuario.id_docente) if usuario.id_docente is not None else None
    if ficha is None:
        ficha = Docente(nombres=cuenta.nombres, apellidos=cuenta.apellidos, activo=True,
                        creado_por=id_autor, creado_en=ahora)
        db.add(ficha)
        db.flush()
        usuario.id_docente = ficha.id_docente
        return True
    deseado = {"nombres": cuenta.nombres, "apellidos": cuenta.apellidos, "activo": True}
    if all(getattr(ficha, campo) == valor for campo, valor in deseado.items()):
        return False
    for campo, valor in deseado.items():
        setattr(ficha, campo, valor)
    ficha.modificado_por, ficha.modificado_en = id_autor, ahora
    db.add(ficha)
    return True


def _misma_contrasena(password: str, password_hash: str) -> bool:
    """Se compara antes de volver a hashear para no cambiar el hash en cada arranque."""
    try:
        return verify_password(password, password_hash)
    except ValueError:  # hash que passlib no reconoce: se reemplaza
        return False


def _corregir_usuario(db: Session, usuario: Usuario, cuenta: CuentaE2E, id_rol: int, password: str) -> bool:
    """Lleva la cuenta existente a los datos E2E. True si cambió algo visible."""
    deseado = {
        "id_rol": id_rol, "nombres": cuenta.nombres, "apellidos": cuenta.apellidos,
        "dni": cuenta.dni, "activo": cuenta.activo,
    }
    cambiado = any(getattr(usuario, campo) != valor for campo, valor in deseado.items())
    desactivada = usuario.activo and not cuenta.activo
    for campo, valor in deseado.items():
        setattr(usuario, campo, valor)
    if not _misma_contrasena(password, usuario.password_hash):
        usuario.password_hash = hash_password(password)
        cambiado = True
    if desactivada:
        cerrar_sesiones_usuario(db, usuario.id_usuario, CIERRE_POR_DESACTIVACION)
    return cambiado


def _limpiar_pendientes(usuario: Usuario) -> bool:
    """Deja la cuenta lista para entrar directo. Devuelve si quitó un cambio obligatorio.

    - PIN pendiente de un cambio de contraseña: no debe sobrevivir entre corridas.
    - Cambio obligatorio de la contraseña temporal (CU016): las cuentas E2E no lo tienen.
    """
    usuario.codigo_verificacion = None
    usuario.codigo_verificacion_expira = None
    usuario.codigo_verificacion_intentos = 0
    if not usuario.debe_cambiar_contrasena and usuario.contrasena_temporal_expira is None:
        return False
    usuario.debe_cambiar_contrasena = False
    usuario.contrasena_temporal_expira = None
    return True


def _asegurar_cuenta(
    db: Session, cuenta: CuentaE2E, entorno: Mapping[str, str], roles: dict, id_autor: int, resumen: Resumen
) -> Optional[Usuario]:
    correo = normalizar_correo(cuenta.correo)
    password = entorno.get(cuenta.variable, "")
    if not password.strip():
        resumen.advertir(f"No se siembra la cuenta E2E {correo}: falta la variable {cuenta.variable}")
        return None
    rol = roles.get(cuenta.rol)
    if rol is None:
        resumen.error(f"No se siembra la cuenta E2E {correo}: no existe el rol {cuenta.rol}")
        return None

    existente = db.exec(select(Usuario).where(Usuario.correo == correo)).first()
    errores = _errores_de_cuenta(db, cuenta, password, existente)
    if errores:
        for mensaje in errores:
            resumen.error(f"No se siembra la cuenta E2E {correo}: {mensaje}")
        return None

    ahora = ahora_utc()
    if existente is None:
        usuario = Usuario(
            id_rol=rol.id_rol, correo=correo, password_hash=hash_password(password),
            dni=cuenta.dni, nombres=cuenta.nombres, apellidos=cuenta.apellidos,
            activo=cuenta.activo, creado_por=id_autor, creado_en=ahora,
        )
        cambiado = False
    else:
        usuario = existente
        cambiado = _corregir_usuario(db, usuario, cuenta, rol.id_rol, password)
    if cuenta.rol == DOCENTE:
        cambiado = _asegurar_ficha(db, usuario, cuenta, id_autor, ahora) or cambiado

    cambiado = _limpiar_pendientes(usuario) or cambiado
    if existente is not None and cambiado:
        usuario.modificado_por, usuario.modificado_en = id_autor, ahora
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        resumen.error(f"No se siembra la cuenta E2E {correo}: la BD rechazó la fila ({e.orig.__class__.__name__})")
        return None
    db.refresh(usuario)

    if existente is None:
        resumen.creados["usuario E2E"] += 1
    elif cambiado:
        resumen.actualizados["usuario E2E"] += 1
    return usuario


def _limpiar_control_acceso(db: Session) -> None:
    """Quita el bloqueo por intentos fallidos y el límite de PIN de los correos E2E."""
    correos = [normalizar_correo(c.correo) for c in CUENTAS]
    db.execute(delete(ControlAccesoCorreo).where(col(ControlAccesoCorreo.correo).in_(correos)))
    db.commit()


# ── Asignaciones ────────────────────────────────────────────────────────────────────

def _asegurar_asignaciones(
    db: Session, id_docente: int, colegio: Colegio, grados: dict[str, Grado],
    periodos: list[PeriodoAcademico], id_autor: int, resumen: Resumen,
) -> None:
    """1.º–3.º del colegio E2E para el Docente E2E en todos los periodos del año; si otro
    docente tiene alguno, se le reasigna al Docente E2E."""
    ahora = ahora_utc()
    for periodo in periodos:
        for nombre in GRADOS_DEL_DOCENTE:
            fila = db.exec(
                select(DocenteColegioGrado).where(
                    DocenteColegioGrado.id_colegio == colegio.id_colegio,
                    DocenteColegioGrado.id_grado == grados[nombre].id_grado,
                    DocenteColegioGrado.id_periodo_academico == periodo.id_periodo_academico,
                )
            ).first()
            if fila is None:
                db.add(DocenteColegioGrado(
                    id_docente=id_docente, id_colegio=colegio.id_colegio, id_grado=grados[nombre].id_grado,
                    id_periodo_academico=periodo.id_periodo_academico, creado_por=id_autor, creado_en=ahora,
                ))
                resumen.creados["asignación E2E"] += 1
            elif fila.id_docente != id_docente:
                fila.id_docente = id_docente
                fila.modificado_por, fila.modificado_en = id_autor, ahora
                db.add(fila)
                resumen.actualizados["asignación E2E (reasignada)"] += 1
    db.commit()


def _liberar_grados(db: Session, colegio: Colegio, grados: dict[str, Grado], resumen: Resumen) -> None:
    """4.º–6.º del colegio E2E sin docente desde el PA vigente (incluido) en adelante, según
    el calendario del colegio. Las asignaciones de PA terminados se conservan como
    historial (CU016)."""
    filas = db.exec(
        select(DocenteColegioGrado.id, PeriodoAcademico)
        .join(PeriodoAcademico, PeriodoAcademico.id_periodo_academico == DocenteColegioGrado.id_periodo_academico)
        .where(
            DocenteColegioGrado.id_colegio == colegio.id_colegio,
            col(DocenteColegioGrado.id_grado).in_([grados[n].id_grado for n in GRADOS_LIBRES]),
        )
    ).all()
    calendarios = cargar_calendarios(db, ids_colegio=[colegio.id_colegio])
    hoy = hoy_lima()
    liberables = [i for i, periodo in filas if not calendarios.pa_terminado(colegio.id_colegio, periodo, hoy)]
    if liberables:
        db.execute(delete(DocenteColegioGrado).where(col(DocenteColegioGrado.id).in_(liberables)))
        db.commit()
        resumen.eliminados["asignación E2E en 4.º–6.º"] += len(liberables)


def _anio_escolar(db: Session) -> Optional[AnioEscolar]:
    """El año vigente; entre años, el de referencia (el último que empezó)."""
    return anio_vigente(db) or anio_de_referencia(db)


# ── Paso ────────────────────────────────────────────────────────────────────────────

def sembrar_e2e(db: Session, entorno: Optional[Mapping[str, str]] = None) -> Resumen:
    resumen = Resumen()
    entorno = os.environ if entorno is None else entorno
    if entorno.get("SEED_E2E", "").strip().lower() != "true":
        resumen.omitir("datos E2E: SEED_E2E no es true")
        return resumen
    motivo = motivo_de_rechazo(entorno)
    if motivo is not None:
        resumen.error(f"No se siembran los datos E2E: {motivo}")
        return resumen

    id_autor = id_supervisor_original(db)
    if id_autor is None:
        resumen.advertir("No se siembran los datos E2E: todavía no existe el Supervisor original")
        return resumen
    grados = {g.nombre: g for g in db.exec(select(Grado).where(col(Grado.nombre).in_(GRADOS))).all()}
    programas = list(db.exec(select(Programa).where(col(Programa.nombre).in_(PROGRAMAS))).all())
    if len(grados) != len(GRADOS) or len(programas) != len(PROGRAMAS):
        resumen.error("No se siembran los datos E2E: faltan grados o programas (¿se ejecutó cargar-catalogos?)")
        return resumen

    colegio = _asegurar_colegio(db, [grados[n] for n in GRADOS], programas, id_autor, resumen)
    roles = roles_por_nombre(db)
    usuarios = {cuenta.correo: _asegurar_cuenta(db, cuenta, entorno, roles, id_autor, resumen) for cuenta in CUENTAS}
    _limpiar_control_acceso(db)

    docente = next(usuarios[c.correo] for c in CUENTAS if c.rol == DOCENTE)
    anio = _anio_escolar(db)
    if anio is None:
        resumen.advertir("Datos E2E sin asignaciones: no hay calendario cargado")
    elif docente is not None:
        periodos = periodos_academicos_del_anio(db, anio.id_anio_escolar)
        _asegurar_asignaciones(db, docente.id_docente, colegio, grados, periodos, id_autor, resumen)
    _liberar_grados(db, colegio, grados, resumen)
    return resumen
