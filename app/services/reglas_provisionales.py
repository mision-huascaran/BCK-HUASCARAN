"""Reglas provisionales: decisiones que el equipo tomó para avanzar mientras el negocio
(Carlos) no las define. Cada una lleva la marca `# REGLA PROVISIONAL` y su código (L1,
R1...), para encontrarlas y reemplazarlas cuando se confirmen.

Sin dependencias de la app: se usan desde los modelos y los servicios.
"""
# REGLA PROVISIONAL L1 (Seguimiento de Lectura, CU024): una clase puede ser la clase de
# referencia de una sola tabla de lectura por subprograma. La clase ya no es por
# subprograma (es del grupo), así que en una misma clase pueden leer los alumnos de los
# dos subprogramas, cada uno en su tabla. Se aplica con este UNIQUE en `lectura_semanal`.
UNICA_TABLA_POR_CLASE_Y_SUBPROGRAMA = ("id_clase_referencia", "id_programa")

