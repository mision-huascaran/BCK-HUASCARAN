"""Política de contraseñas de CU006.

Mínimo 8 caracteres, con al menos una mayúscula, una minúscula, un número y un carácter
especial (cualquier carácter que no sea letra, número ni espacio).
"""

LONGITUD_MINIMA = 8


def errores_politica_contrasena(contrasena: str) -> list[str]:
    """Devuelve qué reglas incumple la contraseña; lista vacía si las cumple todas.

    Los mensajes describen la regla, nunca repiten la contraseña.
    """
    errores = []
    if len(contrasena) < LONGITUD_MINIMA:
        errores.append(f"debe tener al menos {LONGITUD_MINIMA} caracteres")
    if not any(c.isupper() for c in contrasena):
        errores.append("debe tener al menos una mayúscula")
    if not any(c.islower() for c in contrasena):
        errores.append("debe tener al menos una minúscula")
    if not any(c.isdigit() for c in contrasena):
        errores.append("debe tener al menos un número")
    if not any(not c.isalnum() and not c.isspace() for c in contrasena):
        errores.append("debe tener al menos un carácter especial")
    return errores


def cumple_politica_contrasena(contrasena: str) -> bool:
    return not errores_politica_contrasena(contrasena)
