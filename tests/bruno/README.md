# Colecciones Bruno del backend

Dos colecciones contra la API desplegada. La fuente de las reglas es el documento
"Actualización de casos de uso", hasta donde llega el backend: **CU001 a CU022**. Lectura
semanal (CU023 a CU026), Rúbrica y Registro de Vuelo quedan fuera porque todavía no tienen
endpoints.

| Carpeta | Qué prueba | Peticiones |
|---|---|---|
| `integracion/` | Cada endpoint por separado: caso válido, sin token (401), rol sin permiso (403) y validación (422) cuando la regla aplica. Módulos: Auth, Catálogos, Colegios, Usuarios, Sesiones de trabajo (actividades, seguimiento e inicio), Alumnos y Asistencias | 197 |
| `e2e/` | Flujos de negocio donde cada petición usa el resultado de la anterior (`bru.setVar`) | 88 |

Flujos E2E:

1. **F1. El Supervisor arma un colegio** (CU013, CU016, CU014, CU015): crea un colegio con
   su día de clase por grado, crea un Docente con contraseña temporal, le asigna grados,
   crea un alumno, verifica su ficha y su auditoría, y lo inactiva (no sale con el filtro
   Activo y sí con Inactivo).
2. **F2. El Docente registra asistencia** (CU009, CU010, CU014, CU022, CU019): inicia una
   actividad, matricula dos alumnos, usa "Marcar todos" y registra una tardanza con
   observación, edita un alumno y finaliza. Verifica los cambios por módulo de la
   actividad y el valor anterior/nuevo dentro de ella.
3. **F3. El Supervisor audita la jornada** (CU020, CU021, CU022): ve la actividad con
   "N Asistencias" y la asistencia en solo lectura; si intenta escribir, recibe 403.
4. **F4. Regla de actividad** (CU009, CU014, CU022): sin actividad no se escribe (409). Con
   actividad no se puede registrar en fin de semana, en una fecha futura ni en un aula ajena.
5. **F5. Sincronización tras una caída de internet** (CU007, CU008, CU009, CU022): una
   actividad, una asistencia y un cierre de sesión hechos sin conexión se sincronizan
   después de un nuevo login, en su sesión original y con su hora real. El reintento no
   duplica, y entre dos dispositivos gana la captura más reciente.
6. **F6. Permisos del Directivo** (CU012): no crea ni ve alumnos, asistencias ni usuarios.
   Su Inicio no trae nombres de alumnos.

La recuperación de contraseña con PIN no está en E2E: el PIN llega por correo y en CI no
se puede leer. En integración se prueban sus validaciones y el mensaje uniforme.

## Ejecutar

Se corre desde la **raíz del repo** (ahí está `bruno.json`). Las credenciales nunca van
en el repo: se leen de variables de entorno. Son las cuentas E2E que crea `sembrar-e2e`
(`app/cli/seed_e2e.py`).

```bash
# Contraseñas: del .env del ambiente (las mismas E2E_*_PASS que usa el seed)
ENV_FILE=ruta/al/.env . tests/bruno/credenciales-e2e.sh

npx @usebruno/cli@4.2.0 run tests/bruno/integracion --env qa --reporter-junit integracion.xml --reporter-html integracion.html
npx @usebruno/cli@4.2.0 run tests/bruno/e2e --env qa --reporter-junit e2e.xml --reporter-html e2e.html
```

Entornos (`environments/`): `qa`, `dev` y `local` (`despliegue-local`, puerto 8080).
También se puede abrir la colección en la app de Bruno con *Open Collection* sobre la
raíz del repo.

En Jenkins (rama `qa`, después de "Verificar despliegue"), los stages **Integration
Tests** y **E2E Tests** toman las contraseñas del Secret file `HUASCARAN_BACKEND_QA` y
archivan los reportes. Solo se conservan los de la última ejecución. Esos stages agregan
`--reporter-skip-request-body --reporter-skip-headers Authorization` para que el reporte
no lleve contraseñas ni tokens. Además, cada flujo cierra su sesión al terminar.

## Cuidados

- **Las fechas son las reales.** Cada corrida busca el día lectivo más reciente del
  periodo vigente, nunca una fecha fija. Entre periodos o en receso no hay actividad
  posible (`fuera_de_periodo`), y las pruebas de actividad y asistencia fallan por eso.
- **Datos que se crean en cada corrida.** Se crean un colegio, un Docente y alumnos con
  nombres únicos (`Bruno …`). Como nada se elimina, al final quedan **inactivos**. Los
  correos de bienvenida van a alias `+bruno…` de `correoAvisos` (en `environments/`).
- **Cuenta compartida.** El Docente E2E es el mismo que usa Playwright en el frontend, y
  un docente solo puede tener una actividad en curso. Si ambos pipelines corren a la vez,
  pueden chocar. Al empezar, cada flujo finaliza una actividad que haya quedado abierta.
- **Bloqueo por intentos.** Los casos de credenciales inválidas usan un correo inexistente
  para no bloquear las cuentas E2E (5 fallos = 15 min).
- **Sin `.env` en la raíz del repo al correr Bruno.** Bruno lo carga solo, pisa las
  variables exportadas y corta los valores en `#` (las contraseñas E2E lo tienen). Por
  eso los stages de Jenkins lo borran antes de correr.
- **Comparar por `motivo`**, no por el texto de `detail`.
