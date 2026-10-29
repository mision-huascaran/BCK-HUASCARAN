# BCK-HUASCARAN: backend de SICEDU

Backend de SICEDU (Sistema de Centralización de Datos Educativos) para Misión Huascarán.
FastAPI + SQLModel + PostgreSQL + Alembic. La API está documentada en [API.md](API.md) y el
diseño de base de datos en el documento de diseño v3.

## Levantar bakend en local

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

### Trivy

Antes del despliegue, Jenkins construye la imagen como `huascaran-bck-<entorno>:candidata` y
la escanea con Trivy 0.76.1, fijado por digest
(`aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`).
Solo se permiten hallazgos LOW: uno MEDIUM, HIGH, CRITICAL o UNKNOWN, o un escaneo que no
termina, detiene el pipeline y no se despliega. No hay excepciones ni forma de omitir el
control. Si pasa, la imagen se etiqueta como `:actual` y es la que levanta `docker-compose.yml`.

El reporte completo (incluidos los LOW) queda en Jenkins → build → Artifacts →
`trivy-reports/backend-trivy.html`, también cuando el escaneo falla.

## Datos maestros reales: `python -m app.cli`

Son distintos del seed (`app/seed_data.py`, datos de prueba ficticios). Todos los comandos
son idempotentes y se pueden ejecutar en cada arranque:

- nunca duplican y nunca sobrescriben datos editados por usuarios;
- un dato con problemas queda como `WARNING` o `ERROR` en el log y la carga continúa;
- solo terminan con código distinto de 0 ante un fallo de infraestructura (sin conexión a
  la BD).

Los datos viven en archivos versionados en `app/datos/`.

| Comando | Cuándo | Qué hace |
|---|---|---|
| `inicializar` | cada arranque | Ejecuta en orden los cinco comandos de abajo marcados como "cada arranque" y registra un resumen (creado, actualizado, omitido, advertencias, errores). |
| `cargar-catalogos` | cada arranque | Roles, ciclos EBR, grados y programas (buscados por nombre) y los niveles Raz-Kids (29, el id es el orden: J = 11), generales (4), de rúbrica (17) y el nivel esperado por grado (6), desde `app/datos/catalogos.json`. |
| `inicializar-supervisor-original` | cada arranque | Si no existe, crea el Supervisor original (CU001) con las variables `SUPERVISOR_ORIGINAL_*`. Si falta alguna variable, avisa y lo omite. |
| `cargar-calendario [--archivo RUTA]` | cada arranque | Año escolar, PA1 a PA4, calendario general (si el año aún no tiene: PA y PR), cortes diagnósticos y semanas (lunes a viernes, solo dentro de los PA del general) desde `app/datos/calendario_2026.json`. |
| `cargar-feriados` | cada arranque | Feriados nacionales de Perú (librería `holidays`) que caen de lunes a viernes dentro de un PA, como días no lectivos `automatico` del calendario general de cada año. |
| `cargar-colegios-iniciales [--archivo RUTA]` | solo con `CARGAR_COLEGIOS_INICIALES=true` | Los 9 colegios reales con sus 6 grados y 2 programas, desde `app/datos/colegios_iniciales.json`. |

### Supervisor original

1. Define las variables `SUPERVISOR_ORIGINAL_*` (ver `.env.example`) en el `.env` del
   entorno. Si la contraseña tiene `$`, va entre comillas simples para que docker compose
   no la interprete.
2. En el siguiente arranque, `inicializar` crea la cuenta. La contraseña debe cumplir la
   política de CU006 (8+ caracteres, mayúscula, minúscula, número y carácter especial) y el
   DNI tener 8 dígitos; si algo no valida, queda un `ERROR` en el log y no se crea nada.
3. Si pierde el acceso, lo recupera con PIN al correo, como cualquier usuario (CU001,
   CU005). No hay otro mecanismo de recuperación.

### Calendario (FICTICIO por ahora)

`app/datos/calendario_2026.json` es un **calendario ficticio** de desarrollo, no el
calendario escolar real. Desde CU027 el calendario académico (general y particular por
colegio, con sus recesos y días no lectivos) lo gestiona el Supervisor en
`/calendarios`; el archivo solo lo siembra:

- valida que los PA 1 a 4 no se solapen y dejen días de receso entre ellos, estén dentro
  del año, empiecen en lunes, terminen en viernes y que cada corte caiga dentro de su PA;
  si trae `recesos`, valida los 8 periodos; si algo falla, no escribe nada;
- crea el calendario general del año si no existe (sin `recesos`, cada PR cubre el hueco
  entre dos PA y el PR4 llega al fin del año). Si ya existe y difiere, NO lo modifica:
  registra un `WARNING` (un despliegue no debe pisar lo que guardó un Supervisor);
- las fechas del año y de los cortes sí las manda el archivo; las semanas se generan con
  los PA del calendario general de la BD, salvo que eso deje fuera de rango semanas que ya
  tienen registros (rúbrica o reporte): en ese caso registra un `ERROR` y no toca nada.

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
