"""Habilitación de escritura del Docente (CU016, CU028, CU009, CU010).

Un Docente puede escribir en un módulo (asistencias, rubrica, registro_vuelo, lectura)
para un colegio, grado y PA si su cuenta y el colegio están activos y cumple UNA de estas
condiciones, según el calendario de ESE colegio en el instante evaluado:
1. Tiene asignación en ese PA y el colegio está en ese PA.
2. Tiene una asignación retroactiva de ese PA y el colegio está en el PR inmediatamente
   posterior.
3. Tiene un permiso de corrección vigente (CU028) para ese colegio, grado, PA y módulo.
Se evalúan en ese orden. `en` (por defecto, ahora) permite validar una operación offline
con su hora original. La sesión vigente y la actividad activa las exige cada endpoint.

Una sola implementación para los cuatro módulos y para iniciar una actividad (CU009).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, col, select

from app.core.tiempo import ahora_utc, fecha_lima, limites_lima
from app.models.organizacion import (
    MODULOS_CON_PERMISO,
    Colegio,
    Docente,
    DocenteColegioGrado,
    PermisoCorreccion,
    Usuario,
)
from app.services.calendario import Calendarios, PeriodoCalendario, cargar_calendarios

ORIGEN_ASIGNACION = "asignacion"
ORIGEN_RETROACTIVA = "retroactiva"
ORIGEN_PERMISO = "permiso"

MENSAJE_CUENTA_INACTIVA = "La cuenta está inactiva."
MENSAJE_COLEGIO_INACTIVO = "El colegio está inactivo."
MENSAJE_SIN_HABILITACION = (
    "No tienes una asignación habilitada ni un permiso vigente para este colegio, grado, "
    "periodo y módulo."
)


@dataclass(frozen=True)
class Habilitacion:
    """Si se puede escribir y, si no, por qué; si se puede, con qué (y qué permiso)."""

    permitido: bool
    motivo: Optional[str] = None
    id_permiso: Optional[int] = None
    origen: Optional[str] = None


@dataclass(frozen=True)
class HabilitacionVigente:
    """Un colegio, grado y PA en que el docente puede escribir ahora. `modulo` None =
    todos los módulos (asignaciones); `vence_en`: primer instante en que deja de valer."""

    id_colegio: int
    id_grado: int
    id_periodo_academico: int
    origen: str
    vence_en: datetime
    modulo: Optional[str] = None
    id_permiso: Optional[int] = None

    def cubre(self, id_colegio: int, id_grado: int, id_periodo: int, modulo: str) -> bool:
        mismo_grupo = (self.id_colegio, self.id_grado, self.id_periodo_academico) == (id_colegio, id_grado, id_periodo)
        return mismo_grupo and self.modulo in (None, modulo)


def docente_habilitable(db: Session, usuario: Usuario) -> Optional[int]:
    """El id_docente si la cuenta y su ficha de docente están activas; si no, None: una
    cuenta inactiva nunca está habilitada."""
    if not usuario.activo or usuario.id_docente is None:
        return None
    docente = db.get(Docente, usuario.id_docente)
    return docente.id_docente if docente is not None and docente.activo else None


def permisos_vigentes(db: Session, id_docente: int, instante: datetime) -> list[PermisoCorreccion]:
    """Permisos no revocados del docente cuya vigencia [inicio, fin) contiene el instante."""
    return list(
        db.exec(
            select(PermisoCorreccion).where(
                PermisoCorreccion.id_docente == id_docente,
                col(PermisoCorreccion.revocado_en).is_(None),
                PermisoCorreccion.inicio <= instante,
                PermisoCorreccion.fin > instante,
            )
        ).all()
    )


def _origen_de_asignacion(periodo: Optional[PeriodoCalendario], fila: DocenteColegioGrado) -> Optional[str]:
    """Con qué habilita escribir la asignación en el periodo en que está su colegio: la de
    ese PA mientras dure; la retroactiva, solo en el PR que le sigue (cuyo
    `id_periodo_academico` es el de ese PA)."""
    if periodo is None or periodo.id_periodo_academico != fila.id_periodo_academico:
        return None
    if periodo.es_pa:
        return ORIGEN_ASIGNACION
    return ORIGEN_RETROACTIVA if fila.retroactiva else None


def _por_asignaciones(
    filas: list[DocenteColegioGrado], calendarios: Calendarios, instante: datetime
) -> list[HabilitacionVigente]:
    dia = fecha_lima(instante)
    habilitadas = []
    for fila in filas:
        periodo = calendarios.periodo_de(fila.id_colegio, dia)
        origen = _origen_de_asignacion(periodo, fila)
        if origen is not None:
            vence_en = limites_lima(periodo.fecha_fin, periodo.fecha_fin)[1]
            habilitadas.append(
                HabilitacionVigente(fila.id_colegio, fila.id_grado, fila.id_periodo_academico, origen, vence_en)
            )
    # Primero las del PA vigente, luego las retroactivas: el orden de evaluación.
    return sorted(habilitadas, key=lambda h: h.origen != ORIGEN_ASIGNACION)


def _colegios_activos(db: Session, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    consulta = select(Colegio.id_colegio).where(col(Colegio.id_colegio).in_(ids), col(Colegio.activo).is_(True))
    return set(db.exec(consulta).all())


def habilitaciones_vigentes(
    db: Session, usuario: Usuario, en: Optional[datetime] = None, solo_colegios_activos: bool = True
) -> list[HabilitacionVigente]:
    """Dónde puede escribir el docente en el instante `en` (ahora por defecto): sus
    asignaciones del PA vigente de cada colegio, sus retroactivas en su PR y sus permisos
    vigentes, en ese orden. Vacía si la cuenta está inactiva. `solo_colegios_activos`
    descarta los colegios inactivos (no se guarda desde cuándo lo están: para un instante
    pasado puede no tener sentido exigirlo)."""
    id_docente = docente_habilitable(db, usuario)
    if id_docente is None:
        return []
    instante = en or ahora_utc()
    filas = list(db.exec(select(DocenteColegioGrado).where(DocenteColegioGrado.id_docente == id_docente)).all())
    calendarios = cargar_calendarios(db, ids_colegio={f.id_colegio for f in filas})
    habilitadas = _por_asignaciones(filas, calendarios, instante) + [
        HabilitacionVigente(
            p.id_colegio, p.id_grado, p.id_periodo_academico, ORIGEN_PERMISO, p.fin, p.modulo, p.id_permiso
        )
        for p in permisos_vigentes(db, id_docente, instante)
    ]
    if not solo_colegios_activos:
        return habilitadas
    activos = _colegios_activos(db, {h.id_colegio for h in habilitadas})
    return [h for h in habilitadas if h.id_colegio in activos]


def habilitacion_escritura(
    db: Session,
    usuario: Usuario,
    id_colegio: int,
    id_grado: int,
    id_periodo_academico: int,
    modulo: str,
    en: Optional[datetime] = None,
) -> Habilitacion:
    """Si el usuario puede escribir en `modulo` para ese colegio, grado y PA en el instante
    `en` (ahora por defecto), con qué origen y, si es un permiso, cuál (para registrarlo
    en la auditoría)."""
    if modulo not in MODULOS_CON_PERMISO:
        raise ValueError(f"Módulo desconocido: {modulo}")
    if docente_habilitable(db, usuario) is None:
        return Habilitacion(False, MENSAJE_CUENTA_INACTIVA)
    colegio = db.get(Colegio, id_colegio)
    if colegio is None or not colegio.activo:
        return Habilitacion(False, MENSAJE_COLEGIO_INACTIVO)
    for vigente in habilitaciones_vigentes(db, usuario, en):
        if vigente.cubre(id_colegio, id_grado, id_periodo_academico, modulo):
            return Habilitacion(True, id_permiso=vigente.id_permiso, origen=vigente.origen)
    return Habilitacion(False, MENSAJE_SIN_HABILITACION)


def pares_visibles(db: Session, usuario: Usuario) -> set[tuple[int, int]]:
    """(colegio, grado) que el Docente puede consultar hoy: los de sus asignaciones
    habilitadas y los cubiertos por un permiso vigente (CU028)."""
    return {(h.id_colegio, h.id_grado) for h in habilitaciones_vigentes(db, usuario)}
