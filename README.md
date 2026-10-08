# BCK-HUASCARAN: backend de SICEDU

Backend de SICEDU (Sistema de Centralización de Datos Educativos) para Misión Huascarán.
FastAPI + SQLModel + PostgreSQL + Alembic. La API está documentada en [API.md](API.md) y el
diseño de base de datos en el documento de diseño v3.

## Levantar en local

```bash
python run.py
```

`run.py` arranca Docker y la PostgreSQL local (`sicedu-db`, puerto 5433), instala
dependencias, aplica las migraciones, carga los datos maestros (`python -m app.cli
inicializar`), carga los usuarios de prueba (`app/seed_data.py`) e inicia la API en
http://127.0.0.1:8000. Usa `--sin-seed` para no cargar los usuarios de prueba.

## Despliegue

Jenkins levanta el contenedor con `docker-compose.yml` y el `.env` del entorno (dev, qa o
uat) como secreto. En cada arranque el contenedor ejecuta, en orden:

1. `alembic upgrade head`
2. `python -m app.cli inicializar` (datos maestros reales)
3. `python -m app.seed_data`, solo si `CARGAR_DATOS_PRUEBA=true`
4. `uvicorn`

## Datos maestros reales: `python -m app.cli`

Son distintos del seed (`app/seed_data.py`, datos de prueba ficticios). Todos los comandos
son idempotentes y se pueden ejecutar en cada arranque:

- nunca duplican y nunca sobrescriben datos editados por usuarios;
- un dato con problemas queda como `WARNING` o `ERROR` en el log y la carga continúa;
- solo terminan con código distinto de 0 ante un fallo de infraestructura (sin conexión a
  la BD) o si `generar-recovery-keys` no puede escribir su archivo.

Los datos viven en archivos versionados en `app/datos/`.

| Comando | Cuándo | Qué hace |
|---|---|---|
| `inicializar` | cada arranque | Ejecuta en orden los cinco comandos de abajo marcados como "cada arranque" y registra un resumen (creado, actualizado, omitido, advertencias, errores). |
| `cargar-catalogos` | cada arranque | Roles, ciclos EBR, grados y programas (buscados por nombre) y los niveles Raz-Kids (29, el id es el orden: J = 11), generales (4), de rúbrica (17) y el nivel esperado por grado (6), desde `app/datos/catalogos.json`. |
| `inicializar-supervisor-original` | cada arranque | Si no existe, crea el Supervisor original (CU001) con las variables `SUPERVISOR_ORIGINAL_*` y sus 10 Recovery Keys. Si falta alguna variable, avisa y lo omite. |
| `cargar-calendario [--archivo RUTA]` | cada arranque | Año escolar, periodos, cortes diagnósticos y semanas (lunes a viernes, solo dentro de los periodos) desde `app/datos/calendario_2026.json`. |
| `cargar-feriados` | cada arranque | Feriados nacionales de Perú (librería `holidays`) que caen de lunes a viernes, para cada año escolar de la BD. |
| `cargar-colegios-iniciales [--archivo RUTA]` | solo con `CARGAR_COLEGIOS_INICIALES=true` | Los 9 colegios reales con sus 6 grados y 2 programas, desde `app/datos/colegios_iniciales.json`. |
| `generar-recovery-keys --salida RUTA.txt` | SOLO local, una vez | Genera las 10 Recovery Keys del Supervisor original (ver abajo). No toca la BD. |

### Supervisor original y Recovery Keys

1. En tu máquina, fuera del repositorio:

   ```bash
   python -m app.cli generar-recovery-keys --salida "C:/ruta/fuera/del/repo/recovery-keys.txt"
   ```

   Escribe el `.txt` con las 10 llaves en texto plano (para entregar a la alta dirección) y
   por consola imprime solo la línea `SUPERVISOR_ORIGINAL_RECOVERY_HASHES='...'` con los 10
   hashes bcrypt. Nunca sobrescribe un archivo existente y se niega a escribir dentro del
   repositorio (`.gitignore` también ignora `*recovery*key*.txt`).
2. Copia esa línea y el resto de variables `SUPERVISOR_ORIGINAL_*` (ver `.env.example`) en
   el `.env` del entorno. Los valores con `$` (los hashes, y la contraseña si los tiene) van
   entre comillas simples para que docker compose no los interprete.
3. En el siguiente arranque, `inicializar` crea la cuenta. La contraseña debe cumplir la
   política de CU006 (8+ caracteres, mayúscula, minúscula, número y carácter especial) y el
   DNI tener 8 dígitos; si algo no valida, queda un `ERROR` en el log y no se crea nada.

### Calendario (FICTICIO por ahora)

`app/datos/calendario_2026.json` es un **calendario ficticio** de desarrollo, no el
calendario escolar real: hay que reemplazarlo cuando el equipo confirme las fechas. El
archivo es la fuente de verdad (no hay pantalla para editarlo):

- valida que los periodos no se solapen, estén dentro del año, empiecen en lunes, terminen
  en viernes y que cada corte caiga dentro de su periodo; si algo falla, no escribe nada;
- si cambia una fecha, la actualiza en la BD y lo registra en el log, salvo que eso deje
  fuera de rango semanas que ya tienen registros (asistencia, rúbrica o reporte): en ese
  caso registra un `ERROR` y no toca nada.

### Colegios iniciales: `CARGAR_COLEGIOS_INICIALES`

Se activa (`true`) **solo para la primera carga** y luego se vuelve a poner en `false`:
los colegios los administra la Supervisora desde la app (CU013), y una recarga podría
duplicar un colegio que ya se renombró. La provincia y el distrito marcados como
"Por definir" en `app/datos/colegios_iniciales.json` están pendientes de confirmar.

## Pruebas

```bash
pytest tests/ --cov=app --cov-report=xml:coverage.xml
```

Las pruebas usan SQLite en memoria y no necesitan PostgreSQL.
