# Colecciones Bruno del backend

Dos colecciones contra la API desplegada. Las reglas salen del documento "Actualización de
casos de uso" y de los cambios del backend de la semana 11 (`Cambios_Semana11.md`, P1 a P5).

| Carpeta | Qué prueba | Peticiones |
|---|---|---|
| `integracion/` | Cada endpoint por separado: caso válido, sin token (401), rol sin permiso (403) y validación (422) cuando la regla aplica, más las reglas de negocio de cada uno (409, rechazos por ítem). Módulos: Auth, Catálogos, Colegios, Calendario académico, Usuarios, Permisos de corrección, Sesiones de trabajo, Alumnos, Clases, Asistencias, Sincronización, Seguimiento de Lectura, Rúbrica mensual y Registro de Vuelo | 350 |
| `e2e/` | Flujos de negocio donde cada petición usa el resultado de la anterior (`bru.setVar`) | 114 |

Flujos E2E:

1. **F1. El Supervisor arma un colegio** (CU013, CU016, CU014, CU015): colegio con día de
   clase y secciones, Docente con contraseña temporal, alumno matriculado en una sección,
   su ficha, auditoría y resumen; al final todo queda inactivo (filtro Activo / Inactivo).
2. **F2. El Docente registra asistencia** (CU013, CU009, CU014, CU022, CU019): el Supervisor
   agrega una sección para la corrida; el Docente matricula tres alumnos y toma la
   asistencia de la última clase ("Marcar todos", tardanza con observación, reintento
   idempotente) y la auditoría queda ligada a la actividad.
3. **F3. El Supervisor audita la jornada** (CU020, CU021, CU022): productividad, atajo de
   registros de la actividad y la clase en solo lectura; escribir le da 403.
4. **F4. Regla de actividad** (CU009, CU014, CU022): sin actividad no se escribe; con
   actividad, fin de semana, clase futura, alumno ajeno y aula ajena se rechazan.
5. **F5. Sincronización tras una caída de internet** (CU007, CU008, CU009, CU017, CU022):
   avisos de pendientes, actividad y capturas hechas sin conexión registradas en su sesión
   original, "gana la captura más reciente" entre dos dispositivos, reintento sin
   duplicar y cierre de sesión sin red.
6. **F6. Seguimiento de Lectura** (CU023 a CU026): semanas con clase, tabla con la clase de
   referencia sugerida, LSN/LSL y total de libros, "sin asistencia", control de versión,
   tabla duplicada, "No realizada" y solo lectura del Supervisor.
7. **F7. Permisos del Directivo** (CU012): no matricula ni ve alumnos, asistencias, lectura
   ni usuarios; su Inicio no trae nombres de alumnos.

Fuera de E2E: la recuperación de contraseña con PIN (el PIN llega por correo y en CI no se
puede leer; integración prueba sus validaciones y el mensaje uniforme) y los bordes de la
Rúbrica y del Registro de Vuelo (reglas R1, R2 y V1 todavía provisionales).

## Una sección por corrida

La lista de alumnos de una clase (colegio × grado × sección × fecha) **se congela con su
primera asistencia**: un alumno creado después ya no entra. Para que las colecciones se
puedan correr varias veces el mismo día, cada corrida agrega al colegio "I.E. E2E
Playwright" una sección nueva (`Bruno <corrida>` en E2E y `Bruno <corrida> int` en
integración) en el grado del Docente E2E que tuvo clase más recientemente, y matricula ahí
a sus alumnos con pertenencia desde el inicio del PA vigente. Las secciones solo se pueden
agregar (no se borran); "Única" sigue siendo la primera de cada grado.

## Ejecutar

Se corre desde la **raíz del repo** (ahí está `bruno.json`). Las credenciales nunca van en
el repo: se leen de variables de entorno. Son las cuentas E2E que crea `sembrar-e2e`
(`app/cli/seed_e2e.py`).

```bash
# Contraseñas: del .env del ambiente (las mismas E2E_*_PASS que usa el seed)
ENV_FILE=ruta/al/.env . tests/bruno/credenciales-e2e.sh

npx @usebruno/cli@4.2.0 run tests/bruno/integracion --env qa --reporter-junit integracion.xml --reporter-html integracion.html
npx @usebruno/cli@4.2.0 run tests/bruno/e2e --env qa --reporter-junit e2e.xml --reporter-html e2e.html
```

Entornos (`environments/`): `qa`, `dev` y `local` (`despliegue-local`, puerto 8080). Para
otra URL: `--env local --env-var baseUrl=http://127.0.0.1:8001`. `correoAvisos` es el
buzón al que llegan (como alias `+bruno…`) los correos de bienvenida de los usuarios que
crean las colecciones.

En Jenkins (rama `qa`, después de "Verificar despliegue"), los stages **Integration
Tests** y **E2E Tests** toman las contraseñas del Secret file `HUASCARAN_BACKEND_QA` y
archivan los reportes (solo los del último build), con
`--reporter-skip-request-body --reporter-skip-headers Authorization` para que el reporte
no lleve contraseñas ni tokens. Además, cada flujo cierra su sesión al terminar.

## Cuidados

- **Las fechas son las reales.** Cada corrida usa el periodo vigente del calendario del
  colegio y la última clase de la sección hasta hoy; nunca una fecha fija. Si el colegio
  está en receso o fuera del calendario, el paso "Periodo académico de hoy" falla.
- **Datos que se crean en cada corrida.** Un colegio, un Docente, alumnos, tablas de
  lectura, rúbricas y registros de vuelo con nombres únicos (`Bruno …`). Como nada se
  elimina, al final quedan **inactivos** (si la clase de la corrida es de hoy, su lista
  congelada impide inactivarlos ese día: 409 `conflicto_listas_historicas`, aceptado). El
  calendario se prueba sobre un particular del colegio de la corrida, que vuelve al general.
- **Cuenta compartida.** El Docente E2E es el mismo que usa Playwright en el frontend, y
  un docente solo puede tener una actividad en curso. Si ambos pipelines corren a la vez,
  pueden chocar. Al empezar, cada flujo finaliza una actividad que haya quedado abierta.
- **Bloqueo por intentos.** Los casos de credenciales inválidas usan un correo inexistente
  para no bloquear las cuentas E2E (5 fallos = 15 min).
- **Sin `.env` en la raíz del repo al correr Bruno.** Bruno lo carga solo, pisa las
  variables exportadas y corta los valores en `#` (las contraseñas E2E lo tienen). Por
  eso los stages de Jenkins lo borran antes de correr.
- **Comparar por `motivo`**, no por el texto de `detail`. Desde la semana 11 casi todas las
  escrituras son **operaciones**: el lote responde 200 y cada ítem viene `aceptada` o
  `rechazada` con su `motivo`.
