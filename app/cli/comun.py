"""Piezas compartidas por los comandos de carga de datos maestros.

Regla de todos los comandos: un dato raro se registra como WARNING o ERROR y la carga
continúa; solo un fallo de infraestructura (sin conexión a la BD) debe tumbar el proceso.
"""
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import InstrumentedAttribute
from sqlmodel import Session, col, select

from app.models.organizacion import Usuario

log = logging.getLogger("sicedu.cli")

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
DIR_DATOS = RAIZ_PROYECTO / "app" / "datos"


@dataclass
class Resumen:
    """Lo que hizo un comando: se acumula paso a paso y se imprime al final."""

    creados: Counter = field(default_factory=Counter)
    actualizados: Counter = field(default_factory=Counter)
    eliminados: Counter = field(default_factory=Counter)
    omitidos: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)

    def advertir(self, mensaje: str) -> None:
        log.warning(mensaje)
        self.advertencias.append(mensaje)

    def error(self, mensaje: str) -> None:
        log.error(mensaje)
        self.errores.append(mensaje)

    def omitir(self, mensaje: str) -> None:
        log.info("Omitido: %s", mensaje)
        self.omitidos.append(mensaje)

    def absorber(self, otro: "Resumen") -> None:
        self.creados.update(otro.creados)
        self.actualizados.update(otro.actualizados)
        self.eliminados.update(otro.eliminados)
        self.omitidos.extend(otro.omitidos)
        self.advertencias.extend(otro.advertencias)
        self.errores.extend(otro.errores)

    def registrar_en_log(self, titulo: str) -> None:
        def conteo(c: Counter) -> str:
            return ", ".join(f"{k}={v}" for k, v in sorted(c.items())) or "nada"

        log.info("===== Resumen: %s =====", titulo)
        log.info("Creados: %s", conteo(self.creados))
        log.info("Actualizados: %s", conteo(self.actualizados))
        log.info("Eliminados: %s", conteo(self.eliminados))
        log.info("Omitidos (%d): %s", len(self.omitidos), "; ".join(self.omitidos) or "ninguno")
        log.info("Advertencias: %d | Errores: %d", len(self.advertencias), len(self.errores))
        for mensaje in self.advertencias:
            log.info("  WARNING: %s", mensaje)
        for mensaje in self.errores:
            log.info("  ERROR: %s", mensaje)


def leer_json(ruta: Path) -> Optional[dict]:
    """Lee un archivo de datos. None si no existe o no es JSON válido (ya registrado)."""
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error("No existe el archivo de datos %s", ruta)
    except json.JSONDecodeError:
        log.exception("El archivo %s no es JSON válido", ruta)
    return None


def id_supervisor_original(db: Session) -> Optional[int]:
    """Las filas cargadas por script registran como autor al Supervisor original (§1.2)."""
    return db.exec(
        select(Usuario.id_usuario).where(col(Usuario.es_supervisor_original).is_(True))
    ).first()


def ajustar_secuencia(db: Session, columna: InstrumentedAttribute) -> None:
    """Tras insertar ids explícitos, deja la secuencia en el máximo para que el próximo
    INSERT sin id no choque. Solo aplica a PostgreSQL (SQLite no usa secuencias)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    columna_sql = columna.property.columns[0]
    maximo = db.exec(select(func.max(columna))).one()
    db.connection().execute(
        text("SELECT setval(pg_get_serial_sequence(:tabla, :columna), :valor, :usado)"),
        {
            "tabla": columna_sql.table.name,
            "columna": columna_sql.name,
            "valor": maximo or 1,
            "usado": maximo is not None,
        },
    )
