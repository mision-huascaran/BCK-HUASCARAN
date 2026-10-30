"""Punto de entrada: `python -m app.cli <comando>`. Ver README para el uso de cada comando.

Códigos de salida: 0 aunque haya datos con problemas (quedan como WARNING/ERROR en el
log), 1 ante un fallo de infraestructura (sin BD), 2 ante un uso incorrecto del comando
(argparse).
"""
import argparse
import logging
import sys
from pathlib import Path

from app.cli.calendario import ARCHIVO_CALENDARIO, cargar_calendario
from app.cli.catalogos import cargar_catalogos
from app.cli.colegios import ARCHIVO_COLEGIOS, cargar_colegios_iniciales
from app.cli.comun import log
from app.cli.feriados import cargar_feriados
from app.cli.inicializar import ErrorInfraestructura, ejecutar_paso, inicializar
from app.cli.seed_demo import sembrar_demo
from app.cli.seed_e2e import sembrar_e2e
from app.cli.supervisor import inicializar_supervisor_original


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Carga de datos maestros de SICEDU.")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("cargar-catalogos", help="roles, ciclos, grados, programas y niveles")
    sub.add_parser("inicializar-supervisor-original", help="crea el Supervisor original desde el entorno")
    calendario = sub.add_parser("cargar-calendario", help="año, periodos, cortes y semanas")
    calendario.add_argument("--archivo", type=Path, default=ARCHIVO_CALENDARIO)
    sub.add_parser("cargar-feriados", help="feriados nacionales de lunes a viernes")
    colegios = sub.add_parser("cargar-colegios-iniciales", help="solo con CARGAR_COLEGIOS_INICIALES=true")
    colegios.add_argument("--archivo", type=Path, default=ARCHIVO_COLEGIOS)
    sub.add_parser("sembrar-e2e", help="solo con SEED_E2E=true y ENTORNO qa o dev")
    sub.add_parser("sembrar-demo", help="solo con SEED_DEMO=true y ENTORNO dev, qa o uat (en segundo plano)")
    sub.add_parser("inicializar", help="todo lo anterior en orden (arranque del contenedor)")
    return parser


def _paso_unico(args: argparse.Namespace):
    pasos = {
        "cargar-catalogos": cargar_catalogos,
        "inicializar-supervisor-original": inicializar_supervisor_original,
        "cargar-calendario": lambda db: cargar_calendario(db, args.archivo),
        "cargar-feriados": cargar_feriados,
        "cargar-colegios-iniciales": lambda db: cargar_colegios_iniciales(db, args.archivo),
        "sembrar-e2e": sembrar_e2e,
        "sembrar-demo": sembrar_demo,
    }
    return pasos[args.comando]


def main(argv: list[str] | None = None) -> int:
    args = crear_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        if args.comando == "inicializar":
            inicializar()
        else:
            ejecutar_paso(args.comando, _paso_unico(args)).registrar_en_log(args.comando)
    except ErrorInfraestructura as e:
        log.critical("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
