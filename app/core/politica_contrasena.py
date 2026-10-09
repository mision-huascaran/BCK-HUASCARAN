"""Política de contraseñas de CU006.

Mínimo 8 caracteres, con al menos una mayúscula, una minúscula, un número y un carácter
especial (cualquier carácter que no sea letra, número ni espacio).

Cada regla tiene un código estable (lo que viaja en `requisitos_incumplidos` hacia el
frontend) y una descripción legible (para los mensajes de la CLI y del log).
"""
import secrets

LONGITUD_MINIMA = 8

LONGITUD_MINIMA_REGLA = "longitud_minima"
MAYUSCULA = "mayuscula"
MINUSCULA = "minuscula"
NUMERO = "numero"
CARACTER_ESPECIAL = "caracter_especial"

DESCRIPCIONES = {
    LONGITUD_MINIMA_REGLA: f"debe tener al menos {LONGITUD_MINIMA} caracteres",
    MAYUSCULA: "debe tener al menos una mayúscula",
    MINUSCULA: "debe tener al menos una minúscula",
    NUMERO: "debe tener al menos un número",
    CARACTER_ESPECIAL: "debe tener al menos un carácter especial",
}

# Contraseñas temporales: sin caracteres que se confunden al leerlas en un correo
# (O/0, I/l/1) ni símbolos que se escapan en HTML o se pierden al copiar (< > & " ' espacio).
LONGITUD_TEMPORAL = 14
_MAYUSCULAS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUSCULAS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"
_ESPECIALES = "!#$%*+-=?@_"


def requisitos_incumplidos(contrasena: str) -> list[str]:
    """Códigos de las reglas que incumple la contraseña; lista vacía si las cumple todas."""
    incumplidos = []
    if len(contrasena) < LONGITUD_MINIMA:
        incumplidos.append(LONGITUD_MINIMA_REGLA)
    if not any(c.isupper() for c in contrasena):
        incumplidos.append(MAYUSCULA)
    if not any(c.islower() for c in contrasena):
        incumplidos.append(MINUSCULA)
    if not any(c.isdigit() for c in contrasena):
        incumplidos.append(NUMERO)
    if not any(not c.isalnum() and not c.isspace() for c in contrasena):
        incumplidos.append(CARACTER_ESPECIAL)
    return incumplidos


def errores_politica_contrasena(contrasena: str) -> list[str]:
    """Descripción legible de las reglas incumplidas. Nunca repite la contraseña."""
    return [DESCRIPCIONES[codigo] for codigo in requisitos_incumplidos(contrasena)]


def cumple_politica_contrasena(contrasena: str) -> bool:
    return not requisitos_incumplidos(contrasena)


def generar_contrasena_temporal(longitud: int = LONGITUD_TEMPORAL) -> str:
    """Contraseña aleatoria que siempre cumple la política: un carácter de cada clase
    garantizado, el resto de cualquiera, y todo mezclado para que la posición de cada
    clase no sea predecible."""
    clases = (_MAYUSCULAS, _MINUSCULAS, _DIGITOS, _ESPECIALES)
    if longitud < max(LONGITUD_MINIMA, len(clases)):
        raise ValueError(f"La contraseña temporal debe tener al menos {LONGITUD_MINIMA} caracteres")
    todos = "".join(clases)
    caracteres = [secrets.choice(clase) for clase in clases]
    caracteres += [secrets.choice(todos) for _ in range(longitud - len(clases))]
    # SystemRandom usa os.urandom (CSPRNG); Sonar lo confunde con el generador de `random`.
    secrets.SystemRandom().shuffle(caracteres)  # NOSONAR
    return "".join(caracteres)
