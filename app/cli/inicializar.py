"""inicializar: orquestador de la carga de datos maestros al arrancar el contenedor.

Orden: cargar-catalogos -> inicializar-supervisor-original -> cargar-calendario ->
cargar-feriados -> cargar-colegios-iniciales (este último solo actúa si
CARGAR_COLEGIOS_INICIALES=true). Cada paso usa su propia sesión: si uno falla por un dato,
se registra y se sigue con el siguiente. Solo un fallo de infraestructura corta la carga.
"""
from typing import Callable

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlmodel import Session

from app.cli.calendario import cargar_calendario
from app.cli.catalogos import cargar_catalogos
from app.cli.colegios import cargar_colegios_iniciales
from app.cli.comun import Resumen, log
from app.cli.feriados import cargar_feriados
from app.cli.supervisor import inicializar_supervisor_original
from app.core.database import engine

ERRORES_INFRAESTRUCTURA = (OperationalError, InterfaceError)

Paso = Callable[[Session], Resumen]


class ErrorInfraestructura(Exception):
    """No hay conexión utilizable con la BD: el arranque no puede continuar."""


def ejecutar_paso(nombre: str, paso: Paso) -> Resumen:
    """Ejecuta un paso en su propia sesión. Un error inesperado se registra y no se propaga,
    salvo los de infraestructura, que se convierten en ErrorInfraestructura."""
    log.info("--- %s ---", nombre)
    with Session(engine) as db:
        try:
            return paso(db)
        except ERRORES_INFRAESTRUCTURA as e:
            raise ErrorInfraestructura(f"{nombre}: sin conexión utilizable con la BD ({e.__class__.__name__})") from e
        except Exception as e:  # noqa: BLE001 - un dato raro no debe tumbar el arranque
            db.rollback()
            log.exception("%s: fallo inesperado; se continúa con el siguiente paso", nombre)
            resumen = Resumen()
            resumen.errores.append(f"{nombre}: fallo inesperado ({e.__class__.__name__}), ver traceback en el log")
            return resumen


def inicializar() -> Resumen:
    pasos: list[tuple[str, Paso]] = [
        ("cargar-catalogos", cargar_catalogos),
        ("inicializar-supervisor-original", inicializar_supervisor_original),
        ("cargar-calendario", cargar_calendario),
        ("cargar-feriados", cargar_feriados),
        ("cargar-colegios-iniciales", cargar_colegios_iniciales),
    ]
    total = Resumen()
    for nombre, paso in pasos:
        total.absorber(ejecutar_paso(nombre, paso))
    total.registrar_en_log("inicializar")
    return total
